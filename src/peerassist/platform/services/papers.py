"""Authorized paper upload and immutable source reads."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import PurePath
from typing import BinaryIO
from uuid import NAMESPACE_URL, UUID, uuid5

from ..errors import (
    DependencyUnavailable,
    Forbidden,
    IdempotencyConflict,
    InvalidUpload,
    NotFound,
)
from ..idempotency import canonical_json_digest
from ..models import (
    Action,
    Actor,
    ActorKind,
    AuditEvent,
    CommandRecord,
    Decision,
    OutboxEvent,
    Paper,
    PaperVersion,
    TenantScope,
)
from ..permissions import decide_permission
from ..ports import ObjectStore, UnitOfWork, UnitOfWorkFactory


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class UploadPaper:
    project_id: UUID
    filename: str
    media_type: str
    maximum_size_bytes: int
    idempotency_key: str
    request_id: str


@dataclass(frozen=True, slots=True)
class PaperUploadResult:
    paper: Paper
    version: PaperVersion


@dataclass(slots=True)
class PaperSource:
    paper: Paper
    version: PaperVersion
    stream: BinaryIO


class _PrefixedReader:
    def __init__(self, prefix: bytes, source: BinaryIO) -> None:
        self._prefix = prefix
        self._source = source

    def read(self, size: int = -1) -> bytes:
        if size == 0:
            return b""
        if self._prefix:
            if size < 0:
                prefix, self._prefix = self._prefix, b""
                return prefix + self._source.read()
            prefix, self._prefix = self._prefix[:size], self._prefix[size:]
            if len(prefix) == size:
                return prefix
            return prefix + self._source.read(size - len(prefix))
        return self._source.read(size)


class PaperService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        object_store: ObjectStore,
        *,
        clock=_utc_now,
    ) -> None:
        self._uow_factory = uow_factory
        self._object_store = object_store
        self._clock = clock

    def upload(
        self,
        actor: Actor,
        request: UploadPaper,
        source: BinaryIO,
    ) -> PaperUploadResult:
        self._validate_request(actor, request)
        with self._uow_factory(actor) as uow:
            project = self._require_project_action(
                uow, actor, request.project_id, Action.PAPER_UPLOAD
            )
        prefix = source.read(5)
        if prefix != b"%PDF-":
            raise InvalidUpload(details={"reason": "invalid_pdf_header"})
        scope = project.scope
        upload_id = self._object_store.create_temporary(scope, request.maximum_size_bytes)
        temporary = None
        try:
            temporary = self._object_store.write_temporary(
                scope, upload_id, _PrefixedReader(prefix, source)
            )
            payload = {
                "project_id": str(project.id),
                "size_bytes": temporary.size_bytes,
                "sha256": temporary.sha256,
            }
            with self._uow_factory(actor) as uow:
                project = self._require_project_action(
                    uow, actor, request.project_id, Action.PAPER_UPLOAD
                )
                scope = project.scope
                command = self._reserve(
                    uow, actor, scope, request.idempotency_key, payload
                )
                existing = uow.papers.find_by_content_digest(scope, temporary.sha256)
                if existing is not None:
                    version = uow.papers.get_version(scope, existing.current_version_id)
                    if version is None:
                        raise DependencyUnavailable()
                    if command.completed_at is None:
                        self._complete(uow, scope, command, existing, version)
                        uow.commit()
                    return PaperUploadResult(existing, version)
                paper_id = uuid5(project.id, f"paper:{temporary.sha256}")
                version_id = uuid5(paper_id, "version:1")
                object_id = f"papers/{paper_id}/versions/{version_id}/source"
                published = self._object_store.publish(scope, temporary, object_id)
                now = self._clock()
                version = PaperVersion(
                    version_id,
                    project.organization_id,
                    project.id,
                    paper_id,
                    object_id,
                    request.filename,
                    request.media_type,
                    published.size_bytes,
                    published.sha256,
                    actor.actor_id,
                    now,
                )
                paper = Paper(
                    paper_id,
                    project.organization_id,
                    project.id,
                    published.sha256,
                    version.id,
                    "active",
                    1,
                    now,
                    now,
                )
                uow.papers.add(scope, paper, version)
                self._audit(uow, actor, scope, command, paper, version, request.request_id)
                self._outbox(uow, scope, command, paper)
                self._complete(uow, scope, command, paper, version)
                uow.commit()
                return PaperUploadResult(paper, version)
        finally:
            self._object_store.delete_temporary(scope, upload_id)

    def list_papers(self, actor: Actor, project_id: UUID) -> tuple[Paper, ...]:
        with self._uow_factory(actor) as uow:
            project = self._require_project_action(uow, actor, project_id, Action.PAPER_READ)
            return tuple(uow.papers.list(project.scope))

    def open_source(self, actor: Actor, project_id: UUID, paper_id: UUID) -> PaperSource:
        with self._uow_factory(actor) as uow:
            project = self._require_project_action(uow, actor, project_id, Action.PAPER_READ)
            paper = uow.papers.get(project.scope, paper_id)
            if paper is None:
                raise NotFound()
            version = uow.papers.get_version(project.scope, paper.current_version_id)
            if version is None:
                raise NotFound()
        descriptor = self._object_store.metadata(project.scope, version.source_object_id)
        if (
            descriptor is None
            or descriptor.sha256 != version.sha256
            or descriptor.size_bytes != version.size_bytes
        ):
            raise DependencyUnavailable()
        return PaperSource(
            paper,
            version,
            self._object_store.open_immutable(project.scope, version.source_object_id),
        )

    def read_range(
        self,
        actor: Actor,
        project_id: UUID,
        paper_id: UUID,
        start: int,
        end: int,
    ) -> tuple[Paper, PaperVersion, bytes]:
        source = self.open_source(actor, project_id, paper_id)
        source.stream.close()
        return (
            source.paper,
            source.version,
            self._object_store.read_range(
                TenantScope(source.paper.organization_id, source.paper.project_id),
                source.version.source_object_id,
                start,
                end,
            ),
        )

    @staticmethod
    def _validate_request(actor: Actor, request: UploadPaper) -> None:
        if actor.kind is not ActorKind.USER:
            raise Forbidden()
        if request.media_type != "application/pdf":
            raise InvalidUpload(details={"reason": "invalid_media_type"})
        if (
            not request.filename
            or PurePath(request.filename).name != request.filename
            or "/" in request.filename
            or "\\" in request.filename
            or any(ord(character) < 32 or ord(character) == 127 for character in request.filename)
        ):
            raise InvalidUpload(details={"reason": "invalid_filename"})
        if request.maximum_size_bytes <= 0:
            raise ValueError("maximum_size_bytes must be positive")

    @staticmethod
    def _require_project_action(
        uow: UnitOfWork,
        actor: Actor,
        project_id: UUID,
        action: Action,
    ):
        project = None
        organization_membership = None
        for membership in uow.organizations.list_for_user(actor.actor_id):
            if membership.status != "active":
                continue
            candidate = uow.projects.get(TenantScope(membership.organization_id, project_id))
            if candidate is not None:
                project = candidate
                organization_membership = membership
                break
        project_membership = None
        if project is None:
            for membership in uow.projects.list_for_user(actor.actor_id):
                if membership.project_id != project_id or membership.status != "active":
                    continue
                candidate = uow.projects.get(
                    TenantScope(membership.organization_id, project_id)
                )
                if candidate is not None:
                    project = candidate
                    project_membership = membership
                    break
        if project is None:
            raise NotFound()
        membership = organization_membership or project_membership
        assert membership is not None
        decision = decide_permission(
            role=membership.role,
            membership_scope=(
                TenantScope(project.organization_id)
                if organization_membership is not None
                else project.scope
            ),
            resource_scope=project.scope,
            action=action,
        )
        if decision is Decision.NOT_FOUND:
            raise NotFound()
        if decision is not Decision.ALLOW:
            raise Forbidden()
        return project

    def _reserve(
        self,
        uow: UnitOfWork,
        actor: Actor,
        scope: TenantScope,
        idempotency_key: str,
        payload: dict[str, object],
    ) -> CommandRecord:
        operation = "paper.upload"
        command = CommandRecord(
            uuid5(
                NAMESPACE_URL,
                f"peerassist:{scope.organization_id}:{scope.project_id}:"
                f"{actor.actor_id}:{operation}:{idempotency_key}",
            ),
            scope.organization_id,
            actor.actor_id,
            operation,
            idempotency_key,
            canonical_json_digest(payload),
            self._clock(),
            project_id=scope.project_id,
        )
        reserved = uow.commands.reserve_or_replay(scope, command)
        if reserved.payload_digest != command.payload_digest:
            raise IdempotencyConflict()
        return reserved

    def _complete(
        self,
        uow: UnitOfWork,
        scope: TenantScope,
        command: CommandRecord,
        paper: Paper,
        version: PaperVersion,
    ) -> None:
        body = {
            "paper_id": str(paper.id),
            "paper_version_id": str(version.id),
            "sha256": version.sha256,
            "size_bytes": version.size_bytes,
        }
        uow.commands.complete(
            scope,
            replace(command, response_status=201, response_body=body, completed_at=self._clock()),
        )

    def _audit(
        self,
        uow: UnitOfWork,
        actor: Actor,
        scope: TenantScope,
        command: CommandRecord,
        paper: Paper,
        version: PaperVersion,
        request_id: str,
    ) -> None:
        uow.audit.append(
            scope,
            AuditEvent(
                uuid5(command.id, "audit"),
                actor.actor_id,
                scope.organization_id,
                Action.PAPER_UPLOAD,
                "paper",
                paper.id,
                "succeeded",
                request_id,
                self._clock(),
                project_id=scope.project_id,
                identity_id=actor.identity_id,
                command_id=command.id,
                safe_metadata={"size_bytes": version.size_bytes},
            ),
        )

    def _outbox(
        self,
        uow: UnitOfWork,
        scope: TenantScope,
        command: CommandRecord,
        paper: Paper,
    ) -> None:
        uow.outbox.append(
            scope,
            OutboxEvent(
                uuid5(command.id, "outbox"),
                scope.organization_id,
                "paper",
                paper.id,
                paper.version,
                "paper.uploaded",
                1,
                {"paper_id": str(paper.id)},
                self._clock(),
                project_id=scope.project_id,
            ),
        )

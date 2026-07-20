"""Authorized immutable review artifact reads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from typing import BinaryIO
from uuid import UUID, uuid5

from ..errors import DependencyUnavailable, NotFound
from ..models import Action, Actor, Artifact, ObjectDescriptor, TenantScope
from ..ports import ObjectStore, UnitOfWorkFactory
from .reviews import ReviewService


@dataclass(slots=True)
class ArtifactSource:
    artifact: Artifact
    stream: BinaryIO


class ArtifactService:
    def __init__(self, uow_factory: UnitOfWorkFactory, object_store: ObjectStore) -> None:
        self._uow_factory = uow_factory
        self._object_store = object_store

    def list(self, actor: Actor, project_id: UUID, job_id: UUID) -> tuple[Artifact, ...]:
        with self._uow_factory(actor) as uow:
            project = ReviewService._require_project_action(
                uow, actor, project_id, Action.ARTIFACT_READ
            )
            if uow.review_jobs.get(project.scope, job_id) is None:
                raise NotFound()
            return tuple(uow.artifacts.list_for_job(project.scope, job_id))

    def open(
        self, actor: Actor, project_id: UUID, job_id: UUID, artifact_id: UUID
    ) -> ArtifactSource:
        with self._uow_factory(actor) as uow:
            project = ReviewService._require_project_action(
                uow, actor, project_id, Action.ARTIFACT_READ
            )
            if uow.review_jobs.get(project.scope, job_id) is None:
                raise NotFound()
            artifact = uow.artifacts.get(project.scope, artifact_id)
            if artifact is None or artifact.job_id != job_id or artifact.status != "available":
                raise NotFound()
        descriptor = self._object_store.metadata(project.scope, artifact.object.object_id)
        if (
            descriptor is None
            or descriptor.object_id != artifact.object.object_id
            or descriptor.size_bytes != artifact.object.size_bytes
            or descriptor.sha256 != artifact.object.sha256
        ):
            raise DependencyUnavailable()
        return ArtifactSource(
            artifact,
            self._object_store.open_immutable(project.scope, artifact.object.object_id),
        )

    def read_range(
        self,
        actor: Actor,
        project_id: UUID,
        job_id: UUID,
        artifact_id: UUID,
        start: int,
        end: int,
    ) -> tuple[Artifact, bytes]:
        source = self.open(actor, project_id, job_id, artifact_id)
        source.stream.close()
        artifact = source.artifact
        return (
            artifact,
            self._object_store.read_range(
                TenantScope(artifact.organization_id, artifact.project_id),
                artifact.object.object_id,
                start,
                end,
            ),
        )

    def publish_text(
        self,
        actor: Actor,
        project_id: UUID,
        job_id: UUID,
        logical_name: str,
        content: str,
    ) -> Artifact:
        if not logical_name.strip() or len(content.encode("utf-8")) > 4 * 1024 * 1024:
            raise ValueError("artifact content is invalid")
        payload = content.encode("utf-8")
        with self._uow_factory(actor) as uow:
            project = ReviewService._require_project_action(
                uow, actor, project_id, Action.REPORT_FINALIZE
            )
            job = uow.review_jobs.get(project.scope, job_id)
            if job is None:
                raise NotFound()
            existing = next(
                (item for item in uow.artifacts.list_for_job(project.scope, job_id) if item.logical_name == logical_name),
                None,
            )
            if existing is not None:
                return existing
            upload_id = self._object_store.create_temporary(project.scope, len(payload) or 1)
            try:
                temporary = self._object_store.write_temporary(
                    project.scope, upload_id, BytesIO(payload)
                )
                descriptor = self._object_store.publish(
                    project.scope,
                    temporary,
                    f"review-jobs/{job_id}/final/{logical_name}",
                )
                descriptor = ObjectDescriptor(
                    descriptor.object_id,
                    descriptor.size_bytes,
                    descriptor.sha256,
                    "text/markdown; charset=utf-8",
                    descriptor.schema_version,
                )
            finally:
                self._object_store.delete_temporary(project.scope, upload_id)
            artifact = Artifact(
                uuid5(job_id, f"artifact:{logical_name}"),
                project.organization_id,
                project.id,
                job_id,
                logical_name,
                descriptor,
                "available",
                datetime.now(UTC),
            )
            uow.artifacts.add(project.scope, artifact)
            uow.commit()
            return artifact

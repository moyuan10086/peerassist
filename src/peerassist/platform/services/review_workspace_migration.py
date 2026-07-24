"""Conservative, idempotent migration from registered read-only M0 jobs."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid5

from ..errors import NotFound
from ..models import (
    REVIEW_DOCUMENT_SECTIONS,
    Action,
    Actor,
    ExternalServiceConsent,
    LegacyRegistration,
    ReviewDocument,
    ReviewDocumentBlock,
    ReviewEvent,
    ReviewJob,
    TenantScope,
)
from ..ports import UnitOfWork, UnitOfWorkFactory
from .reviews import ReviewService

_SHA256 = re.compile(r"[0-9a-f]{64}")


def _utc_now() -> datetime:
    return datetime.now(UTC)


class LegacyWorkspaceFactsReader(Protocol):
    def read_workspace_facts(
        self,
        scope: TenantScope,
        registration: LegacyRegistration,
    ) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class LegacyProviderState:
    service: str
    revision: int
    enabled: bool


@dataclass(frozen=True, slots=True)
class LegacyMigrationResult:
    status: str
    registration_id: UUID
    job_id: UUID | None
    paper_version_id: UUID | None


class LegacyWorkspaceMigrationService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        reader: LegacyWorkspaceFactsReader,
        *,
        provider_state: Callable[[str], LegacyProviderState | None],
        clock=_utc_now,
    ) -> None:
        self._uow_factory = uow_factory
        self._reader = reader
        self._provider_state = provider_state
        self._clock = clock

    def migrate(
        self,
        actor: Actor,
        project_id: UUID,
        registration_id: UUID,
    ) -> LegacyMigrationResult:
        with self._uow_factory(actor) as uow:
            project = ReviewService._require_project_action(
                uow, actor, project_id, Action.PROJECT_MANAGE_SETTINGS
            )
            scope = project.scope
            registration = uow.legacy_registrations.get(scope, registration_id)
            if registration is None:
                raise NotFound()
            facts = self._reader.read_workspace_facts(scope, registration)
            digest = facts.get("paper_sha256")
            if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
                return LegacyMigrationResult(
                    "paper_mapping_required", registration.id, None, None
                )
            paper = uow.papers.find_by_content_digest(scope, digest)
            if paper is None:
                return LegacyMigrationResult(
                    "paper_mapping_required", registration.id, None, None
                )
            version = uow.papers.get_version(scope, paper.current_version_id)
            if version is None or version.sha256 != digest:
                return LegacyMigrationResult(
                    "paper_mapping_required", registration.id, None, None
                )
            job_id = uuid5(registration.id, "peerassist:review-workspace-migration")
            existing = uow.review_jobs.get(scope, job_id)
            if existing is not None:
                return self._existing_result(uow, scope, registration.id, existing)

            consent = self._consent(
                uow,
                scope,
                actor,
                job_id,
                version.id,
                facts.get("consent"),
            )
            document = self._document(
                scope,
                actor,
                job_id,
                facts.get("document_sections"),
            )
            if consent is None:
                outcome = "consent_reauthorization_required"
            elif document is None:
                outcome = "draft_mapping_required"
            else:
                outcome = "migrated"
            now = self._clock()
            ready = outcome == "migrated"
            job = ReviewJob(
                job_id,
                scope.organization_id,
                project.id,
                version.id,
                self._mode(facts.get("mode")),
                "await_confirmation" if ready else "legacy_import_blocked",
                "awaiting_human_confirmation" if ready else "blocked",
                1,
                1,
                actor.actor_id,
                now,
                now,
                safe_error_code=None if ready else outcome,
            )
            uow.review_jobs.add(scope, job)
            if consent is not None:
                uow.consents.add(scope, consent)
            if document is not None:
                uow.review_documents.add(scope, document)
            self._append_event(uow, scope, job, outcome, registration.id, now)
            uow.commit()
            return LegacyMigrationResult(outcome, registration.id, job.id, version.id)

    def _consent(
        self,
        uow: UnitOfWork,
        scope: TenantScope,
        actor: Actor,
        job_id: UUID,
        paper_version_id: UUID,
        raw: object,
    ) -> ExternalServiceConsent | None:
        if not isinstance(raw, Mapping):
            return None
        service = raw.get("service")
        revision = raw.get("provider_config_revision")
        policy = raw.get("policy_version")
        data_scope = raw.get("data_scope")
        decided_by = self._uuid(raw.get("decided_by"))
        decided_at = self._datetime(raw.get("decided_at"))
        expires_at = self._datetime(raw.get("expires_at"), optional=True)
        provider = self._provider_state(service) if isinstance(service, str) else None
        if (
            not isinstance(service, str)
            or not service.strip()
            or raw.get("status") != "granted"
            or not isinstance(revision, int)
            or isinstance(revision, bool)
            or revision <= 0
            or not isinstance(policy, str)
            or not policy.strip()
            or not isinstance(data_scope, Mapping)
            or decided_by is None
            or decided_at is None
            or uow.users.get(decided_by) is None
            or provider is None
            or provider.service != service
            or provider.revision != revision
            or not provider.enabled
            or (expires_at is not None and expires_at <= self._clock())
        ):
            return None
        return ExternalServiceConsent(
            id=uuid5(job_id, f"consent:{scope.organization_id}:{paper_version_id}:{service}:1"),
            organization_id=scope.organization_id,
            project_id=scope.project_id or job_id,
            review_job_id=job_id,
            paper_version_id=paper_version_id,
            service=service,
            provider_config_revision=revision,
            policy_version=policy,
            data_scope=data_scope,  # type: ignore[arg-type]
            status="granted",
            generation=1,
            version=1,
            decided_by=decided_by,
            decided_at=decided_at,
            expires_at=expires_at,
            superseded_at=None,
            created_at=decided_at,
            updated_at=decided_at,
        )

    def _document(
        self,
        scope: TenantScope,
        actor: Actor,
        job_id: UUID,
        raw: object,
    ) -> ReviewDocument | None:
        if not isinstance(raw, Mapping) or set(raw) != set(REVIEW_DOCUMENT_SECTIONS):
            return None
        values: dict[str, str] = {}
        for section in REVIEW_DOCUMENT_SECTIONS:
            value = raw.get(section)
            if not isinstance(value, str):
                return None
            values[section] = value
        if scope.project_id is None:
            raise NotFound()
        now = self._clock()
        document_id = uuid5(
            job_id, f"review-document:{scope.organization_id}:{scope.project_id}"
        )
        return ReviewDocument(
            id=document_id,
            organization_id=scope.organization_id,
            project_id=scope.project_id,
            review_job_id=job_id,
            blocks=tuple(
                ReviewDocumentBlock(
                    id=uuid5(document_id, f"peerassist.review-document.section:{section}"),
                    section=section,
                    text=values[section],
                    source_type="legacy_import",
                )
                for section in REVIEW_DOCUMENT_SECTIONS
            ),
            document_version=1,
            base_decision_event_id=None,
            last_edited_by=actor.actor_id,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _append_event(
        uow: UnitOfWork,
        scope: TenantScope,
        job: ReviewJob,
        outcome: str,
        registration_id: UUID,
        now: datetime,
    ) -> None:
        uow.review_jobs.append_event(
            scope,
            ReviewEvent(
                uuid5(job.id, f"legacy-workspace-migration:{outcome}"),
                job.organization_id,
                job.project_id,
                job.id,
                1,
                (
                    "legacy_workspace_migrated"
                    if outcome == "migrated"
                    else outcome
                ),
                1,
                {"registration_id": str(registration_id)},
                now,
            ),
        )

    @staticmethod
    def _existing_result(
        uow: UnitOfWork,
        scope: TenantScope,
        registration_id: UUID,
        job: ReviewJob,
    ) -> LegacyMigrationResult:
        events = uow.review_jobs.list_events(scope, job.id)
        event_type = events[0].event_type if events else "consent_reauthorization_required"
        status = "migrated" if event_type == "legacy_workspace_migrated" else event_type
        return LegacyMigrationResult(status, registration_id, job.id, job.paper_version_id)

    @staticmethod
    def _mode(raw: object) -> str:
        return raw if raw in {"fast", "full"} else "full"  # type: ignore[return-value]

    @staticmethod
    def _uuid(raw: object) -> UUID | None:
        try:
            return UUID(str(raw))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _datetime(raw: object, *, optional: bool = False) -> datetime | None:
        if raw is None and optional:
            return None
        try:
            value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            return None
        return value.astimezone(UTC)

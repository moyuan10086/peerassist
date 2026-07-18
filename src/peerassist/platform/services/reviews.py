"""Authorized, PostgreSQL-authoritative review job commands and reads."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from ..errors import Forbidden, IdempotencyConflict, NotFound, StaleVersion
from ..idempotency import canonical_json_digest
from ..models import (
    Action,
    Actor,
    ActorKind,
    AuditEvent,
    CommandRecord,
    Decision,
    OutboxEvent,
    ReviewEvent,
    ReviewJob,
    TenantScope,
    WorkItem,
)
from ..permissions import decide_permission
from ..ports import UnitOfWork, UnitOfWorkFactory


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class CreateReviewJob:
    project_id: UUID
    paper_version_id: UUID
    mode: str
    idempotency_key: str
    request_id: str


@dataclass(frozen=True, slots=True)
class ChangeReviewJob:
    project_id: UUID
    job_id: UUID
    expected_version: int
    idempotency_key: str
    request_id: str


@dataclass(frozen=True, slots=True)
class RecordReviewDecision:
    project_id: UUID
    job_id: UUID
    decision_type: str
    subject_id: str
    decision: str
    expected_version: int
    idempotency_key: str
    request_id: str


class ReviewService:
    def __init__(self, uow_factory: UnitOfWorkFactory, *, clock=_utc_now) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def create(self, actor: Actor, request: CreateReviewJob) -> ReviewJob:
        if request.mode not in {"fast", "full"}:
            raise ValueError("mode must be fast or full")
        with self._uow_factory(actor) as uow:
            project = self._require_project_action(
                uow, actor, request.project_id, Action.REVIEW_JOB_CREATE
            )
            version = uow.papers.get_version(project.scope, request.paper_version_id)
            if version is None:
                raise NotFound()
            payload = {
                "mode": request.mode,
                "paper_version_id": str(request.paper_version_id),
                "project_id": str(project.id),
            }
            command = self._reserve(
                uow, actor, project.scope, "review_job.create", request.idempotency_key, payload
            )
            replay = self._replayed_job(uow, project.scope, command)
            if replay is not None:
                return replay
            now = self._clock()
            job_id = uuid5(command.id, "review-job")
            job = ReviewJob(
                job_id,
                project.organization_id,
                project.id,
                version.id,
                request.mode,
                "queued",
                "queued",
                1,
                1,
                actor.actor_id,
                now,
                now,
            )
            uow.review_jobs.add(project.scope, job)
            self._append_event(uow, project.scope, job, "review_job.created")
            self._enqueue(uow, project.scope, job)
            self._audit(uow, actor, project.scope, command, job, request.request_id)
            self._outbox(uow, project.scope, job, "review_job.created")
            self._complete(uow, project.scope, command, job, 201)
            uow.commit()
            return job

    def list(self, actor: Actor, project_id: UUID) -> tuple[ReviewJob, ...]:
        with self._uow_factory(actor) as uow:
            project = self._require_project_action(uow, actor, project_id, Action.REVIEW_JOB_READ)
            return tuple(uow.review_jobs.list(project.scope))

    def get(self, actor: Actor, project_id: UUID, job_id: UUID) -> ReviewJob:
        with self._uow_factory(actor) as uow:
            project = self._require_project_action(uow, actor, project_id, Action.REVIEW_JOB_READ)
            job = uow.review_jobs.get(project.scope, job_id)
            if job is None:
                raise NotFound()
            return job

    def events(
        self, actor: Actor, project_id: UUID, job_id: UUID
    ) -> tuple[ReviewEvent, ...]:
        with self._uow_factory(actor) as uow:
            project = self._require_project_action(
                uow, actor, project_id, Action.REVIEW_EVENT_READ
            )
            if uow.review_jobs.get(project.scope, job_id) is None:
                raise NotFound()
            return uow.review_jobs.list_events(project.scope, job_id)

    def cancel(self, actor: Actor, request: ChangeReviewJob) -> ReviewJob:
        return self._change(actor, request, operation="cancel")

    def retry(self, actor: Actor, request: ChangeReviewJob) -> ReviewJob:
        return self._change(actor, request, operation="retry")

    def record_decision(self, actor: Actor, request: RecordReviewDecision) -> ReviewJob:
        if request.decision_type not in {"consent", "concern"}:
            raise ValueError("decision_type must be consent or concern")
        if not request.subject_id.strip() or not request.decision.strip():
            raise ValueError("decision subject and value must not be blank")
        with self._uow_factory(actor) as uow:
            project = self._require_project_action(
                uow, actor, request.project_id, Action.CONCERN_DECIDE
            )
            scope = project.scope
            command = self._reserve(
                uow,
                actor,
                scope,
                "review_job.decision",
                request.idempotency_key,
                {
                    "decision": request.decision,
                    "decision_type": request.decision_type,
                    "expected_version": request.expected_version,
                    "job_id": str(request.job_id),
                    "subject_id": request.subject_id,
                },
            )
            replay = self._replayed_job(uow, scope, command)
            if replay is not None:
                return replay
            job = self._versioned_job(uow, scope, request.job_id, request.expected_version)
            changed = replace(job, version=job.version + 1, updated_at=self._clock())
            uow.review_jobs.save(scope, changed, job.version)
            self._append_event(
                uow,
                scope,
                changed,
                "review_job.decision_recorded",
                payload={
                    "decision": request.decision,
                    "decision_type": request.decision_type,
                    "subject_id": request.subject_id,
                },
            )
            self._audit(
                uow,
                actor,
                scope,
                command,
                changed,
                request.request_id,
                action=Action.CONCERN_DECIDE,
            )
            self._outbox(uow, scope, changed, "review_job.decision_recorded")
            self._complete(uow, scope, command, changed, 200)
            uow.commit()
            return changed

    def finalize(self, actor: Actor, request: ChangeReviewJob) -> ReviewJob:
        with self._uow_factory(actor) as uow:
            project = self._require_project_action(
                uow, actor, request.project_id, Action.REPORT_FINALIZE
            )
            scope = project.scope
            command = self._reserve(
                uow,
                actor,
                scope,
                "review_job.finalize",
                request.idempotency_key,
                {"expected_version": request.expected_version, "job_id": str(request.job_id)},
            )
            replay = self._replayed_job(uow, scope, command)
            if replay is not None:
                return replay
            job = self._versioned_job(uow, scope, request.job_id, request.expected_version)
            changed = replace(
                job,
                stage="completed",
                status="completed",
                version=job.version + 1,
                updated_at=self._clock(),
            )
            uow.review_jobs.save(scope, changed, job.version)
            self._append_event(uow, scope, changed, "review_job.finalized")
            self._audit(
                uow,
                actor,
                scope,
                command,
                changed,
                request.request_id,
                action=Action.REPORT_FINALIZE,
            )
            self._outbox(uow, scope, changed, "review_job.finalized")
            self._complete(uow, scope, command, changed, 200)
            uow.commit()
            return changed

    def _change(
        self, actor: Actor, request: ChangeReviewJob, *, operation: str
    ) -> ReviewJob:
        action = Action.REVIEW_JOB_CANCEL if operation == "cancel" else Action.REVIEW_JOB_RETRY
        with self._uow_factory(actor) as uow:
            project = self._require_project_action(uow, actor, request.project_id, action)
            scope = project.scope
            command = self._reserve(
                uow,
                actor,
                scope,
                f"review_job.{operation}",
                request.idempotency_key,
                {"expected_version": request.expected_version, "job_id": str(request.job_id)},
            )
            replay = self._replayed_job(uow, scope, command)
            if replay is not None:
                return replay
            job = self._versioned_job(uow, scope, request.job_id, request.expected_version)
            now = self._clock()
            if operation == "cancel":
                changed = replace(
                    job,
                    status="cancelled",
                    version=job.version + 1,
                    updated_at=now,
                    cancelled_at=now,
                )
                event_type = "review_job.cancelled"
            else:
                if job.status not in {"cancelled", "failed"}:
                    raise Forbidden()
                changed = replace(
                    job,
                    stage="queued",
                    status="queued",
                    version=job.version + 1,
                    attempt=job.attempt + 1,
                    updated_at=now,
                    cancelled_at=None,
                    safe_error_code=None,
                )
                event_type = "review_job.retried"
            uow.review_jobs.save(scope, changed, job.version)
            self._append_event(uow, scope, changed, event_type)
            if operation == "retry":
                self._enqueue(uow, scope, changed)
            self._audit(uow, actor, scope, command, changed, request.request_id, action=action)
            self._outbox(uow, scope, changed, event_type)
            self._complete(uow, scope, command, changed, 200)
            uow.commit()
            return changed

    @staticmethod
    def _require_project_action(
        uow: UnitOfWork, actor: Actor, project_id: UUID, action: Action
    ):
        if actor.kind is not ActorKind.USER:
            raise Forbidden()
        project = None
        membership = None
        membership_scope = None
        for candidate in uow.organizations.list_for_user(actor.actor_id):
            if candidate.status != "active":
                continue
            found = uow.projects.get(TenantScope(candidate.organization_id, project_id))
            if found is not None:
                project, membership = found, candidate
                membership_scope = TenantScope(found.organization_id)
                break
        if project is None:
            for candidate in uow.projects.list_for_user(actor.actor_id):
                if candidate.project_id != project_id or candidate.status != "active":
                    continue
                found = uow.projects.get(TenantScope(candidate.organization_id, project_id))
                if found is not None:
                    project, membership, membership_scope = found, candidate, found.scope
                    break
        if project is None or membership is None or membership_scope is None:
            raise NotFound()
        decision = decide_permission(
            role=membership.role,
            membership_scope=membership_scope,
            resource_scope=project.scope,
            action=action,
        )
        if decision is Decision.NOT_FOUND:
            raise NotFound()
        if decision is not Decision.ALLOW:
            raise Forbidden()
        return project

    @staticmethod
    def _versioned_job(
        uow: UnitOfWork, scope: TenantScope, job_id: UUID, expected_version: int
    ) -> ReviewJob:
        job = uow.review_jobs.get(scope, job_id)
        if job is None:
            raise NotFound()
        if job.version != expected_version:
            raise StaleVersion(
                details={"expected_version": expected_version, "current_version": job.version}
            )
        return job

    def _reserve(
        self,
        uow: UnitOfWork,
        actor: Actor,
        scope: TenantScope,
        operation: str,
        key: str,
        payload: dict[str, object],
    ) -> CommandRecord:
        command = CommandRecord(
            uuid5(
                NAMESPACE_URL,
                f"peerassist:{scope.organization_id}:{scope.project_id}:"
                f"{actor.actor_id}:{operation}:{key}",
            ),
            scope.organization_id,
            actor.actor_id,
            operation,
            key,
            canonical_json_digest(payload),
            self._clock(),
            project_id=scope.project_id,
        )
        reserved = uow.commands.reserve_or_replay(scope, command)
        if reserved.payload_digest != command.payload_digest:
            raise IdempotencyConflict()
        return reserved

    @staticmethod
    def _replayed_job(
        uow: UnitOfWork, scope: TenantScope, command: CommandRecord
    ) -> ReviewJob | None:
        if command.completed_at is None or not isinstance(command.response_body, Mapping):
            return None
        raw_job_id = command.response_body.get("job_id")
        if not isinstance(raw_job_id, str):
            return None
        job = uow.review_jobs.get(scope, UUID(raw_job_id))
        if job is None:
            raise NotFound()
        return job

    def _append_event(
        self,
        uow: UnitOfWork,
        scope: TenantScope,
        job: ReviewJob,
        event_type: str,
        *,
        payload: dict[str, object] | None = None,
    ) -> None:
        sequence = len(uow.review_jobs.list_events(scope, job.id)) + 1
        uow.review_jobs.append_event(
            scope,
            ReviewEvent(
                uuid5(job.id, f"event:{sequence}:{event_type}"),
                scope.organization_id,
                job.project_id,
                job.id,
                sequence,
                event_type,
                1,
                payload
                or {"attempt": job.attempt, "stage": job.stage, "status": job.status},
                self._clock(),
            ),
        )

    def _enqueue(self, uow: UnitOfWork, scope: TenantScope, job: ReviewJob) -> None:
        attempt_id = uuid5(job.id, f"attempt:{job.attempt}")
        uow.work_items.enqueue(
            scope,
            WorkItem(
                uuid5(attempt_id, "work:prepare:0"),
                scope.organization_id,
                job.project_id,
                job.id,
                attempt_id,
                "prepare",
                0,
                0,
                3,
                self._clock(),
                self._clock(),
            ),
        )

    def _audit(
        self,
        uow: UnitOfWork,
        actor: Actor,
        scope: TenantScope,
        command: CommandRecord,
        job: ReviewJob,
        request_id: str,
        *,
        action: Action = Action.REVIEW_JOB_CREATE,
    ) -> None:
        uow.audit.append(
            scope,
            AuditEvent(
                uuid5(command.id, "audit"),
                actor.actor_id,
                scope.organization_id,
                action,
                "review_job",
                job.id,
                "succeeded",
                request_id,
                self._clock(),
                project_id=scope.project_id,
                identity_id=actor.identity_id,
                command_id=command.id,
                safe_metadata={"attempt": job.attempt, "status": job.status},
            ),
        )

    def _outbox(
        self, uow: UnitOfWork, scope: TenantScope, job: ReviewJob, event_type: str
    ) -> None:
        sequence = len(uow.review_jobs.list_events(scope, job.id))
        uow.outbox.append(
            scope,
            OutboxEvent(
                uuid5(job.id, f"outbox:{sequence}:{event_type}"),
                scope.organization_id,
                "review_job",
                job.id,
                sequence,
                event_type,
                1,
                {"job_id": str(job.id)},
                self._clock(),
                project_id=scope.project_id,
            ),
        )

    def _complete(
        self,
        uow: UnitOfWork,
        scope: TenantScope,
        command: CommandRecord,
        job: ReviewJob,
        status: int,
    ) -> None:
        uow.commands.complete(
            scope,
            replace(
                command,
                response_status=status,
                response_body={"job_id": str(job.id)},
                completed_at=self._clock(),
            ),
        )

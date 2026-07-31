"""Authorized consent, review document, and finding workspace commands."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid5

from ..errors import DependencyUnavailable, NotFound, StaleVersion
from ..models import (
    Action,
    Actor,
    Artifact,
    CommandRecord,
    ExternalServiceConsent,
    JsonValue,
    ReportVersion,
    ReviewDocument,
    ReviewDocumentBlock,
    ReviewEvent,
    ReviewJob,
    TenantScope,
    WorkItem,
    mutable_json,
)
from ..ports import ObjectStore, UnitOfWork, UnitOfWorkFactory
from .reviews import ReviewService


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ChangeExternalServiceConsent:
    project_id: UUID
    job_id: UUID
    paper_version_id: UUID
    service: str
    provider_config_revision: int
    policy_version: str
    data_scope: Mapping[str, JsonValue]
    action: str
    expected_consent_version: int
    idempotency_key: str
    request_id: str
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SaveReviewDocument:
    project_id: UUID
    job_id: UUID
    blocks: tuple[ReviewDocumentBlock, ...]
    expected_document_version: int
    idempotency_key: str
    request_id: str


@dataclass(frozen=True, slots=True)
class DecideFinding:
    project_id: UUID
    job_id: UUID
    finding_lineage_id: str
    finding_id: str
    finding_revision: int
    action: str
    expected_review_version: int
    expected_document_version: int
    last_decision_event_id: UUID | None
    idempotency_key: str
    request_id: str
    rewrite_text: str | None = None


@dataclass(frozen=True, slots=True)
class FindingDecisionResult:
    event: ReviewEvent
    review_version: int
    document_id: UUID
    document_version: int
    base_decision_event_id: UUID


@dataclass(frozen=True, slots=True)
class SubmitReviewExport:
    project_id: UUID
    job_id: UUID
    expected_review_version: int
    expected_document_version: int
    expected_decision_event_id: UUID | None
    format: str
    idempotency_key: str
    request_id: str


@dataclass(frozen=True, slots=True)
class ExportSubmission:
    export_id: UUID
    job_id: UUID
    status: str
    document_version: int
    format: str


@dataclass(frozen=True, slots=True)
class ExportRecord:
    report: ReportVersion
    artifact: Artifact


class ReviewWorkspaceService:
    """Coordinate authorized workspace changes inside one application transaction."""

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
        self._reviews = ReviewService(uow_factory, clock=clock)

    def change_consent(
        self,
        actor: Actor,
        request: ChangeExternalServiceConsent,
    ) -> ExternalServiceConsent:
        if request.action not in {"grant", "deny", "revoke", "reapply", "expire"}:
            raise ValueError("action must be grant, deny, revoke, reapply, or expire")
        if request.expected_consent_version < 0:
            raise ValueError("expected_consent_version must be nonnegative")
        with self._uow_factory(actor) as uow:
            project = self._reviews._require_project_action(
                uow,
                actor,
                request.project_id,
                Action.CONCERN_DECIDE,
            )
            scope = project.scope
            job = self._job(uow, scope, request.job_id)
            if request.paper_version_id != job.paper_version_id:
                raise ValueError("consent paper version must match the review job paper version")
            if uow.papers.get_version(scope, request.paper_version_id) is None:
                raise NotFound()
            command = self._reviews._reserve(
                uow,
                actor,
                scope,
                "review_workspace.consent",
                request.idempotency_key,
                self._consent_command_payload(request),
            )
            current = uow.consents.get_current(
                scope,
                job.id,
                request.paper_version_id,
                request.service,
            )
            replay = self._replayed_consent(scope, request, command)
            if replay is not None:
                return replay
            now = self._clock()
            if request.action == "reapply":
                changed = self._reapply_consent(uow, scope, request, current, now)
            else:
                changed = self._transition_consent(uow, scope, actor, request, current, now)
            self._reviews._append_event(
                uow,
                scope,
                job,
                "review_workspace.consent_changed",
                payload={
                    "action": request.action,
                    "consent_id": str(changed.id),
                    "consent_version": changed.version,
                    "generation": changed.generation,
                    "paper_version_id": str(changed.paper_version_id),
                    "provider_config_revision": changed.provider_config_revision,
                    "service": changed.service,
                    "status": changed.status,
                },
            )
            self._reviews._audit(
                uow,
                actor,
                scope,
                command,
                job,
                request.request_id,
                action=Action.CONCERN_DECIDE,
            )
            self._complete_command(
                uow,
                scope,
                command,
                self._consent_response(changed, job.id),
            )
            uow.commit()
            return changed

    def get_document(
        self,
        actor: Actor,
        project_id: UUID,
        job_id: UUID,
    ) -> ReviewDocument:
        with self._uow_factory(actor) as uow:
            project = self._reviews._require_project_action(
                uow,
                actor,
                project_id,
                Action.REPORT_DRAFT,
            )
            scope = project.scope
            job = self._job(uow, scope, job_id)
            document = uow.review_documents.get(scope, job.id)
            if document is not None:
                return document
            document = ReviewDocument.empty(
                id=self._document_id(scope, job.id),
                organization_id=scope.organization_id,
                project_id=job.project_id,
                review_job_id=job.id,
                last_edited_by=actor.actor_id,
                created_at=self._clock(),
            )
            uow.review_documents.add(scope, document)
            uow.commit()
            return document

    def get_evidence(
        self,
        actor: Actor,
        project_id: UUID,
        job_id: UUID,
    ) -> Mapping[str, JsonValue]:
        """Return the validated review-result projection for the teacher workspace."""

        with self._uow_factory(actor) as uow:
            project = self._reviews._require_project_action(
                uow,
                actor,
                project_id,
                Action.ARTIFACT_READ,
            )
            job = self._job(uow, project.scope, job_id)
            return self._review_result(uow, project.scope, job)

    def save_document(
        self,
        actor: Actor,
        request: SaveReviewDocument,
    ) -> ReviewDocument:
        with self._uow_factory(actor) as uow:
            project = self._reviews._require_project_action(
                uow,
                actor,
                request.project_id,
                Action.REPORT_DRAFT,
            )
            scope = project.scope
            job = self._job(uow, scope, request.job_id)
            command = self._reviews._reserve(
                uow,
                actor,
                scope,
                "review_workspace.document_save",
                request.idempotency_key,
                {
                    "blocks": [self._block_payload(block) for block in request.blocks],
                    "expected_document_version": request.expected_document_version,
                    "job_id": str(request.job_id),
                },
            )
            document = uow.review_documents.get(scope, job.id)
            replay = self._replayed_document(document, request, command)
            if replay is not None:
                return replay
            if document is None:
                raise NotFound()
            if document.document_version != request.expected_document_version:
                raise StaleVersion(
                    details={
                        "expected_version": request.expected_document_version,
                        "current_version": document.document_version,
                    }
                )
            self._validate_projected_findings(uow, scope, job, document, request.blocks)
            changed = replace(
                document,
                blocks=request.blocks,
                document_version=document.document_version + 1,
                last_edited_by=actor.actor_id,
                updated_at=self._clock(),
            )
            uow.review_documents.save(
                scope,
                changed,
                expected_document_version=document.document_version,
            )
            self._reviews._audit(
                uow,
                actor,
                scope,
                command,
                job,
                request.request_id,
                action=Action.REPORT_DRAFT,
            )
            self._complete_command(
                uow,
                scope,
                command,
                {
                    "base_decision_event_id": (
                        str(changed.base_decision_event_id)
                        if changed.base_decision_event_id is not None
                        else None
                    ),
                    "document_id": str(changed.id),
                    "document_version": changed.document_version,
                    "job_id": str(job.id),
                    "updated_at": changed.updated_at.isoformat(),
                },
            )
            uow.commit()
            return changed

    def decide_finding(
        self,
        actor: Actor,
        request: DecideFinding,
    ) -> FindingDecisionResult:
        if request.action not in {"accept", "rewrite", "downgrade", "delete"}:
            raise ValueError("action must be accept, rewrite, downgrade, or delete")
        if request.action == "rewrite" and (
            request.rewrite_text is None or not request.rewrite_text.strip()
        ):
            raise ValueError("rewrite_text must not be blank for rewrite")
        with self._uow_factory(actor) as uow:
            project = self._reviews._require_project_action(
                uow,
                actor,
                request.project_id,
                Action.CONCERN_DECIDE,
            )
            scope = project.scope
            command = self._reviews._reserve(
                uow,
                actor,
                scope,
                "review_workspace.finding_decision",
                request.idempotency_key,
                self._finding_command_payload(request),
            )
            replay = self._replayed_decision(uow, scope, command)
            if replay is not None:
                return replay
            job = self._reviews._versioned_job(
                uow,
                scope,
                request.job_id,
                request.expected_review_version,
            )
            document = uow.review_documents.get(scope, job.id)
            if document is None:
                raise NotFound()
            if document.document_version != request.expected_document_version:
                raise StaleVersion(
                    details={
                        "expected_version": request.expected_document_version,
                        "current_version": document.document_version,
                    }
                )
            if document.base_decision_event_id != request.last_decision_event_id:
                raise StaleVersion()
            concern = self._find_concern(
                uow,
                scope,
                job,
                request.finding_lineage_id,
                request.finding_id,
                request.finding_revision,
            )
            now = self._clock()
            changed_job = replace(job, version=job.version + 1, updated_at=now)
            uow.review_jobs.save(scope, changed_job, job.version)
            self._reviews._append_event(
                uow,
                scope,
                changed_job,
                "review_workspace.finding_decided",
                payload={
                    "action": request.action,
                    "finding_id": request.finding_id,
                    "finding_lineage_id": request.finding_lineage_id,
                    "finding_revision": request.finding_revision,
                    **(
                        {"rewrite_text": request.rewrite_text}
                        if request.action == "rewrite"
                        else {}
                    ),
                },
            )
            event = uow.review_jobs.list_events(scope, job.id)[-1]
            blocks = self._project_finding(document, concern, request)
            changed_document = replace(
                document,
                blocks=blocks,
                document_version=document.document_version + 1,
                base_decision_event_id=event.id,
                last_edited_by=actor.actor_id,
                updated_at=now,
            )
            uow.review_documents.save(
                scope,
                changed_document,
                expected_document_version=document.document_version,
            )
            self._reviews._audit(
                uow,
                actor,
                scope,
                command,
                changed_job,
                request.request_id,
                action=Action.CONCERN_DECIDE,
            )
            self._complete_command(
                uow,
                scope,
                command,
                {
                    "base_decision_event_id": str(event.id),
                    "document_id": str(changed_document.id),
                    "document_version": changed_document.document_version,
                    "event_id": str(event.id),
                    "job_id": str(changed_job.id),
                    "review_version": changed_job.version,
                },
            )
            uow.commit()
            return FindingDecisionResult(
                event=event,
                review_version=changed_job.version,
                document_id=changed_document.id,
                document_version=changed_document.document_version,
                base_decision_event_id=event.id,
            )

    def submit_export(
        self,
        actor: Actor,
        request: SubmitReviewExport,
    ) -> ExportSubmission:
        if request.format != "markdown":
            raise ValueError("format must be markdown")
        with self._uow_factory(actor) as uow:
            project = self._reviews._require_project_action(
                uow,
                actor,
                request.project_id,
                Action.REPORT_FINALIZE,
            )
            scope = project.scope
            command = self._reviews._reserve(
                uow,
                actor,
                scope,
                "review_workspace.export",
                request.idempotency_key,
                {
                    "expected_decision_event_id": (
                        str(request.expected_decision_event_id)
                        if request.expected_decision_event_id is not None
                        else None
                    ),
                    "expected_document_version": request.expected_document_version,
                    "expected_review_version": request.expected_review_version,
                    "format": request.format,
                    "job_id": str(request.job_id),
                },
            )
            replay = self._replayed_export(command)
            if replay is not None:
                return replay
            job = self._reviews._versioned_job(
                uow, scope, request.job_id, request.expected_review_version
            )
            if job.status not in {"blocked", "awaiting_human_confirmation"}:
                raise ValueError("review job is not ready for export")
            document = uow.review_documents.get(scope, job.id)
            if document is None:
                raise NotFound()
            if document.document_version != request.expected_document_version:
                raise StaleVersion(
                    details={
                        "expected_version": request.expected_document_version,
                        "current_version": document.document_version,
                    }
                )
            if document.base_decision_event_id != request.expected_decision_event_id:
                raise StaleVersion()
            now = self._clock()
            changed = replace(
                job,
                stage="export",
                status="exporting_report",
                version=job.version + 1,
                updated_at=now,
            )
            uow.review_jobs.save(scope, changed, job.version)
            self._reviews._append_event(
                uow,
                scope,
                changed,
                "review_export.requested",
                payload={
                    "blocks": [self._block_payload(block) for block in document.blocks],
                    "command_id": str(command.id),
                    "decision_event_id": (
                        str(document.base_decision_event_id)
                        if document.base_decision_event_id is not None
                        else None
                    ),
                    "document_id": str(document.id),
                    "document_version": document.document_version,
                    "finding_revisions": [
                        {
                            "finding_id": block.finding_id,
                            "finding_lineage_id": block.finding_lineage_id,
                            "finding_revision": block.finding_revision,
                        }
                        for block in document.blocks
                        if block.finding_lineage_id is not None
                    ],
                    "format": request.format,
                },
            )
            sequence = uow.review_jobs.list_events(scope, job.id)[-1].aggregate_sequence
            export_id = uuid5(command.id, "work:export")
            uow.work_items.enqueue(
                scope,
                WorkItem(
                    export_id,
                    scope.organization_id,
                    job.project_id,
                    job.id,
                    uuid5(job.id, f"attempt:{job.attempt}"),
                    "export",
                    sequence,
                    0,
                    3,
                    now,
                    now,
                ),
            )
            self._reviews._audit(
                uow,
                actor,
                scope,
                command,
                changed,
                request.request_id,
                action=Action.REPORT_FINALIZE,
            )
            self._reviews._outbox(uow, scope, changed, "review_export.requested")
            submission = ExportSubmission(
                export_id,
                job.id,
                "queued",
                document.document_version,
                request.format,
            )
            self._complete_command(
                uow,
                scope,
                command,
                {
                    "document_version": submission.document_version,
                    "export_id": str(submission.export_id),
                    "format": submission.format,
                    "job_id": str(submission.job_id),
                    "status": submission.status,
                },
                response_status=202,
            )
            uow.commit()
            return submission

    def list_exports(
        self,
        actor: Actor,
        project_id: UUID,
        job_id: UUID,
    ) -> tuple[ExportRecord, ...]:
        with self._uow_factory(actor) as uow:
            project = self._reviews._require_project_action(
                uow, actor, project_id, Action.ARTIFACT_READ
            )
            scope = project.scope
            self._job(uow, scope, job_id)
            records: list[ExportRecord] = []
            for report in uow.report_versions.list_for_job(scope, job_id):
                artifact = uow.artifacts.get(scope, self.export_artifact_id(report.id))
                if artifact is None or artifact.job_id != job_id or artifact.status != "available":
                    continue
                records.append(ExportRecord(report, artifact))
            return tuple(records)

    def get_export(
        self,
        actor: Actor,
        project_id: UUID,
        job_id: UUID,
        report_version_id: UUID,
    ) -> ExportRecord:
        records = self.list_exports(actor, project_id, job_id)
        record = next(
            (item for item in records if item.report.id == report_version_id),
            None,
        )
        if record is None:
            raise NotFound()
        return record

    @staticmethod
    def export_artifact_id(report_version_id: UUID) -> UUID:
        return uuid5(report_version_id, "artifact:review-export")

    def _reapply_consent(
        self,
        uow: UnitOfWork,
        scope: TenantScope,
        request: ChangeExternalServiceConsent,
        current: ExternalServiceConsent | None,
        now: datetime,
    ) -> ExternalServiceConsent:
        if request.expires_at is not None:
            raise ValueError("expires_at is valid only when granting consent")
        if current is None:
            if request.expected_consent_version != 0:
                raise StaleVersion(
                    details={
                        "expected_version": request.expected_consent_version,
                        "current_version": 0,
                    }
                )
            replacement = self._pending_consent(
                request,
                organization_id=scope.organization_id,
                generation=1,
                now=now,
            )
            uow.consents.add(scope, replacement)
            return replacement
        self._check_consent_version(current, request.expected_consent_version)
        configuration_changed = (
            current.provider_config_revision != request.provider_config_revision
            or current.policy_version != request.policy_version
            or current.data_scope != request.data_scope
        )
        if current.status not in {"denied", "revoked", "expired"} and not configuration_changed:
            raise ValueError("only terminal consent or changed configuration may be reapplied")
        superseded = replace(
            current,
            version=current.version + 1,
            superseded_at=now,
            updated_at=now,
        )
        replacement = self._pending_consent(
            request,
            organization_id=scope.organization_id,
            generation=current.generation + 1,
            now=now,
        )
        uow.consents.supersede_and_add(
            scope,
            superseded,
            replacement,
            expected_version=current.version,
        )
        return replacement

    def _transition_consent(
        self,
        uow: UnitOfWork,
        scope: TenantScope,
        actor: Actor,
        request: ChangeExternalServiceConsent,
        current: ExternalServiceConsent | None,
        now: datetime,
    ) -> ExternalServiceConsent:
        if current is None:
            raise NotFound()
        self._check_consent_version(current, request.expected_consent_version)
        if (
            current.provider_config_revision != request.provider_config_revision
            or current.policy_version != request.policy_version
            or current.data_scope != request.data_scope
        ):
            raise ValueError("changed consent configuration requires reapply")
        if request.action in {"grant", "deny"} and current.status != "pending":
            raise ValueError("grant and deny require pending consent")
        if request.action in {"revoke", "expire"} and current.status != "granted":
            raise ValueError(
                f"cannot {request.action} {current.status} consent; granted consent is required"
            )
        if request.action == "grant":
            if request.expires_at is not None and request.expires_at <= now:
                raise ValueError("expires_at must be in the future")
            expires_at = request.expires_at
        else:
            if request.expires_at is not None:
                raise ValueError("expires_at is valid only when granting consent")
            expires_at = current.expires_at
        if request.action == "expire" and (
            current.expires_at is None or now < current.expires_at
        ):
            raise ValueError("granted consent is not expired")
        status = {
            "grant": "granted",
            "deny": "denied",
            "revoke": "revoked",
            "expire": "expired",
        }[request.action]
        changed = replace(
            current,
            status=status,
            version=current.version + 1,
            decided_by=actor.actor_id,
            decided_at=now,
            expires_at=expires_at,
            updated_at=now,
        )
        uow.consents.save(scope, changed, expected_version=current.version)
        return changed

    def _pending_consent(
        self,
        request: ChangeExternalServiceConsent,
        *,
        organization_id: UUID,
        generation: int,
        now: datetime,
    ) -> ExternalServiceConsent:
        return ExternalServiceConsent(
            id=uuid5(
                request.job_id,
                f"consent:{organization_id}:{request.paper_version_id}:"
                f"{request.service}:{generation}",
            ),
            organization_id=organization_id,
            project_id=request.project_id,
            review_job_id=request.job_id,
            paper_version_id=request.paper_version_id,
            service=request.service,
            provider_config_revision=request.provider_config_revision,
            policy_version=request.policy_version,
            data_scope=request.data_scope,
            status="pending",
            generation=generation,
            version=1,
            decided_by=None,
            decided_at=None,
            expires_at=None,
            superseded_at=None,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _document_id(scope: TenantScope, job_id: UUID) -> UUID:
        if scope.project_id is None:
            raise NotFound()
        return uuid5(job_id, f"review-document:{scope.organization_id}:{scope.project_id}")

    @staticmethod
    def _check_consent_version(
        consent: ExternalServiceConsent,
        expected_version: int,
    ) -> None:
        if consent.version != expected_version:
            raise StaleVersion(
                details={
                    "expected_version": expected_version,
                    "current_version": consent.version,
                }
            )

    @staticmethod
    def _job(uow: UnitOfWork, scope: TenantScope, job_id: UUID) -> ReviewJob:
        job = uow.review_jobs.get(scope, job_id)
        if job is None:
            raise NotFound()
        return job

    def _validate_projected_findings(
        self,
        uow: UnitOfWork,
        scope: TenantScope,
        job: ReviewJob,
        document: ReviewDocument,
        blocks: tuple[ReviewDocumentBlock, ...],
    ) -> None:
        finding_blocks = tuple(
            block for block in blocks if block.finding_lineage_id is not None
        )
        if not finding_blocks:
            if any(block.finding_lineage_id is not None for block in document.blocks):
                raise StaleVersion()
            return
        payload = self._review_result(uow, scope, job)
        current_by_lineage = {
            block.finding_lineage_id: block
            for block in document.blocks
            if block.finding_lineage_id is not None
        }
        submitted_by_lineage: dict[str, ReviewDocumentBlock] = {}
        for block in finding_blocks:
            lineage = block.finding_lineage_id
            if lineage is None or lineage in submitted_by_lineage:
                raise StaleVersion()
            submitted_by_lineage[lineage] = block
        if set(current_by_lineage) != set(submitted_by_lineage):
            raise StaleVersion()
        if any(
            submitted_by_lineage[lineage] != current
            for lineage, current in current_by_lineage.items()
        ):
            raise StaleVersion()
        for block in finding_blocks:
            self._find_concern_in_payload(
                payload,
                block.finding_lineage_id,
                block.finding_id or "",
                block.finding_revision or 0,
            )

    def _find_concern(
        self,
        uow: UnitOfWork,
        scope: TenantScope,
        job: ReviewJob,
        lineage_id: str,
        finding_id: str,
        revision: int,
    ) -> Mapping[str, JsonValue]:
        payload = self._review_result(uow, scope, job)
        return self._find_concern_in_payload(
            payload,
            lineage_id,
            finding_id,
            revision,
        )

    @staticmethod
    def _find_concern_in_payload(
        payload: Mapping[str, JsonValue],
        lineage_id: str,
        finding_id: str,
        revision: int,
    ) -> Mapping[str, JsonValue]:
        concerns = payload.get("concerns")
        if not isinstance(concerns, list):
            raise ValueError("review_result.json concerns must be an array")
        lineage_found = False
        matched_item: Mapping[str, JsonValue] | None = None
        identities: set[tuple[str, str, int]] = set()
        for item in concerns:
            if not isinstance(item, Mapping):
                raise ValueError("review_result.json concerns must contain objects")
            item_lineage = item.get("finding_lineage_id")
            item_id = item.get("finding_id")
            item_revision = item.get("revision")
            if (
                not isinstance(item_lineage, str)
                or not item_lineage.strip()
                or not isinstance(item_id, str)
                or not item_id.strip()
                or not isinstance(item_revision, int)
                or isinstance(item_revision, bool)
                or item_revision <= 0
            ):
                raise ValueError("finding identity must contain lineage, ID, and positive revision")
            identity = (item_lineage, item_id, item_revision)
            if identity in identities:
                raise ValueError("finding identity must be unique")
            identities.add(identity)
            if item_lineage != lineage_id:
                continue
            lineage_found = True
            if item_id == finding_id and item_revision == revision:
                matched_item = item
        if matched_item is not None:
            return matched_item
        if lineage_found:
            raise StaleVersion()
        raise NotFound()

    def _review_result(
        self,
        uow: UnitOfWork,
        scope: TenantScope,
        job: ReviewJob,
    ) -> Mapping[str, JsonValue]:
        artifact = next(
            (
                item
                for item in uow.artifacts.list_for_job(scope, job.id)
                if item.logical_name == "review_result.json" and item.status == "available"
            ),
            None,
        )
        if artifact is None:
            raise NotFound()
        descriptor = self._object_store.metadata(scope, artifact.object.object_id)
        if (
            descriptor is None
            or descriptor.object_id != artifact.object.object_id
            or descriptor.size_bytes != artifact.object.size_bytes
            or descriptor.sha256 != artifact.object.sha256
        ):
            raise DependencyUnavailable()
        stream = self._object_store.open_immutable(scope, artifact.object.object_id)
        try:
            raw = stream.read(8 * 1024 * 1024 + 1)
        finally:
            stream.close()
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("review_result.json exceeds maximum size")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("review_result.json must contain valid UTF-8 JSON") from error
        if not isinstance(payload, Mapping):
            raise ValueError("review_result.json must contain a JSON object")
        if payload.get("schema_version") != "peerassist.review_result.v1":
            raise ValueError("review_result.json schema_version is not supported")
        return payload

    @staticmethod
    def _project_finding(
        document: ReviewDocument,
        concern: Mapping[str, JsonValue],
        request: DecideFinding,
    ) -> tuple[ReviewDocumentBlock, ...]:
        blocks = tuple(
            block
            for block in document.blocks
            if block.finding_lineage_id != request.finding_lineage_id
        )
        if request.action == "delete":
            return blocks
        evidence_ids, locator = ReviewWorkspaceService._validated_evidence(concern)
        if not evidence_ids:
            raise ValueError("finding evidence must not be empty")
        section = ReviewWorkspaceService._finding_section(concern, request.action)
        raw_text = (
            request.rewrite_text
            if request.action == "rewrite"
            else concern.get("author_action") or concern.get("text") or concern.get("title")
        )
        if not isinstance(raw_text, str) or not raw_text.strip():
            raise ValueError("finding must contain projection text")
        projected = ReviewDocumentBlock(
            id=uuid5(document.id, f"finding:{request.finding_lineage_id}"),
            section=section,
            text=raw_text.strip(),
            source_type="finding_decision",
            finding_lineage_id=request.finding_lineage_id,
            finding_id=request.finding_id,
            finding_revision=request.finding_revision,
            evidence_ids=evidence_ids,
            evidence_locator=locator,
        )
        return (*blocks, projected)

    @staticmethod
    def _validated_evidence(
        concern: Mapping[str, JsonValue],
    ) -> tuple[tuple[str, ...], Mapping[str, JsonValue] | None]:
        rows = concern.get("evidence")
        if not isinstance(rows, list):
            raise ValueError("evidence must be a list")
        row_ids: list[str] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError("evidence rows must be objects")
            evidence_id = row.get("id")
            if not isinstance(evidence_id, str) or not evidence_id.strip():
                raise ValueError("evidence rows must contain a string id")
            if evidence_id in row_ids:
                raise ValueError("evidence row IDs must be unique")
            row_ids.append(evidence_id)
        raw_ids = concern.get("evidence_ids")
        if raw_ids is None:
            evidence_ids = tuple(row_ids)
        else:
            if not isinstance(raw_ids, list) or any(
                not isinstance(item, str) or not item.strip() for item in raw_ids
            ):
                raise ValueError("evidence_ids must be a list of strings")
            if len(set(raw_ids)) != len(raw_ids):
                raise ValueError("evidence_ids must not contain duplicates")
            if set(raw_ids) != set(row_ids):
                raise ValueError("evidence_ids must match evidence row IDs")
            evidence_ids = tuple(raw_ids)
        locator = rows[0] if rows else None
        return evidence_ids, locator

    @staticmethod
    def _finding_section(concern: Mapping[str, JsonValue], action: str) -> str:
        if action == "downgrade":
            return "minor_issues"
        level = concern.get("level")
        if level in {"minor", "minor_concern"}:
            return "minor_issues"
        if level == "editor_note":
            return "revision_suggestions"
        return "major_issues"

    @staticmethod
    def _consent_command_payload(
        request: ChangeExternalServiceConsent,
    ) -> dict[str, object]:
        return {
            "action": request.action,
            "data_scope": dict(request.data_scope),
            "expected_consent_version": request.expected_consent_version,
            "expires_at": request.expires_at.isoformat() if request.expires_at else None,
            "job_id": str(request.job_id),
            "paper_version_id": str(request.paper_version_id),
            "policy_version": request.policy_version,
            "provider_config_revision": request.provider_config_revision,
            "service": request.service,
        }

    @staticmethod
    def _finding_command_payload(request: DecideFinding) -> dict[str, object]:
        return {
            "action": request.action,
            "expected_document_version": request.expected_document_version,
            "expected_review_version": request.expected_review_version,
            "finding_id": request.finding_id,
            "finding_lineage_id": request.finding_lineage_id,
            "finding_revision": request.finding_revision,
            "job_id": str(request.job_id),
            "last_decision_event_id": (
                str(request.last_decision_event_id)
                if request.last_decision_event_id is not None
                else None
            ),
            "rewrite_text": request.rewrite_text,
        }

    @staticmethod
    def _block_payload(block: ReviewDocumentBlock) -> dict[str, object]:
        return {
            "evidence_ids": list(block.evidence_ids),
            "evidence_locator": (
                mutable_json(block.evidence_locator)
                if block.evidence_locator is not None
                else None
            ),
            "finding_id": block.finding_id,
            "finding_lineage_id": block.finding_lineage_id,
            "finding_revision": block.finding_revision,
            "id": str(block.id),
            "section": block.section,
            "source_type": block.source_type,
            "text": block.text,
        }

    @staticmethod
    def _replayed_consent(
        scope: TenantScope,
        request: ChangeExternalServiceConsent,
        command: CommandRecord,
    ) -> ExternalServiceConsent | None:
        if command.completed_at is None:
            return None
        if not isinstance(command.response_body, Mapping):
            raise NotFound()
        response = command.response_body
        required = {
            "consent_id",
            "status",
            "generation",
            "version",
            "decided_by",
            "decided_at",
            "expires_at",
            "superseded_at",
            "created_at",
            "updated_at",
        }
        if not required.issubset(response):
            raise NotFound()
        if scope.project_id is None:
            raise NotFound()
        decided_by = response["decided_by"]
        if not isinstance(decided_by, str) and decided_by is not None:
            raise NotFound()
        return ExternalServiceConsent(
            id=UUID(str(response["consent_id"])),
            organization_id=scope.organization_id,
            project_id=scope.project_id,  # type: ignore[arg-type]
            review_job_id=request.job_id,
            paper_version_id=request.paper_version_id,
            service=request.service,
            provider_config_revision=request.provider_config_revision,
            policy_version=request.policy_version,
            data_scope=request.data_scope,
            status=str(response["status"]),
            generation=int(response["generation"]),
            version=int(response["version"]),
            decided_by=UUID(decided_by) if decided_by is not None else None,
            decided_at=ReviewWorkspaceService._parse_datetime(response["decided_at"]),
            expires_at=ReviewWorkspaceService._parse_datetime(response["expires_at"]),
            superseded_at=ReviewWorkspaceService._parse_datetime(response["superseded_at"]),
            created_at=ReviewWorkspaceService._parse_datetime(response["created_at"]),
            updated_at=ReviewWorkspaceService._parse_datetime(response["updated_at"]),
        )

    @staticmethod
    def _replayed_document(
        document: ReviewDocument | None,
        request: SaveReviewDocument,
        command: CommandRecord,
    ) -> ReviewDocument | None:
        if command.completed_at is None:
            return None
        if document is None or not isinstance(command.response_body, Mapping):
            raise NotFound()
        response = command.response_body
        if response.get("document_id") != str(document.id):
            raise NotFound()
        raw_base_event_id = response.get("base_decision_event_id")
        if raw_base_event_id is not None and not isinstance(raw_base_event_id, str):
            raise NotFound()
        raw_updated_at = response.get("updated_at")
        if not isinstance(raw_updated_at, str):
            raise NotFound()
        return replace(
            document,
            blocks=request.blocks,
            document_version=int(response["document_version"]),
            base_decision_event_id=(
                UUID(raw_base_event_id) if raw_base_event_id is not None else None
            ),
            last_edited_by=command.actor_id,
            updated_at=ReviewWorkspaceService._parse_datetime(raw_updated_at),
        )

    @staticmethod
    def _replayed_decision(
        uow: UnitOfWork,
        scope: TenantScope,
        command: CommandRecord,
    ) -> FindingDecisionResult | None:
        if command.completed_at is None:
            return None
        if not isinstance(command.response_body, Mapping):
            raise NotFound()
        raw_event_id = command.response_body.get("event_id")
        raw_job_id = command.response_body.get("job_id")
        if not isinstance(raw_event_id, str) or not isinstance(raw_job_id, str):
            raise NotFound()
        event = next(
            (
                item
                for item in uow.review_jobs.list_events(
                    scope, UUID(raw_job_id)
                )
                if str(item.id) == raw_event_id
            ),
            None,
        )
        if event is None:
            raise NotFound()
        document_id = command.response_body.get("document_id")
        if not isinstance(document_id, str):
            raise NotFound()
        base_event_id = command.response_body.get("base_decision_event_id")
        if not isinstance(base_event_id, str):
            raise NotFound()
        return FindingDecisionResult(
            event=event,
            review_version=int(command.response_body["review_version"]),
            document_id=UUID(document_id),
            document_version=int(command.response_body["document_version"]),
            base_decision_event_id=UUID(base_event_id),
        )

    @staticmethod
    def _replayed_export(command: CommandRecord) -> ExportSubmission | None:
        if command.completed_at is None:
            return None
        if not isinstance(command.response_body, Mapping):
            raise NotFound()
        response = command.response_body
        required = {"export_id", "job_id", "status", "document_version", "format"}
        if not required.issubset(response):
            raise NotFound()
        return ExportSubmission(
            UUID(str(response["export_id"])),
            UUID(str(response["job_id"])),
            str(response["status"]),
            int(response["document_version"]),
            str(response["format"]),
        )

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise NotFound()
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise NotFound()
        return parsed

    @staticmethod
    def _consent_response(
        consent: ExternalServiceConsent,
        job_id: UUID,
    ) -> dict[str, JsonValue]:
        return {
            "consent_id": str(consent.id),
            "created_at": consent.created_at.isoformat(),
            "decided_at": consent.decided_at.isoformat() if consent.decided_at else None,
            "decided_by": str(consent.decided_by) if consent.decided_by else None,
            "expires_at": consent.expires_at.isoformat() if consent.expires_at else None,
            "generation": consent.generation,
            "job_id": str(job_id),
            "status": consent.status,
            "superseded_at": consent.superseded_at.isoformat() if consent.superseded_at else None,
            "updated_at": consent.updated_at.isoformat(),
            "version": consent.version,
        }

    def _complete_command(
        self,
        uow: UnitOfWork,
        scope: TenantScope,
        command: CommandRecord,
        response_body: dict[str, JsonValue],
        *,
        response_status: int = 200,
    ) -> None:
        uow.commands.complete(
            scope,
            replace(
                command,
                response_status=response_status,
                response_body=response_body,
                completed_at=self._clock(),
            ),
        )

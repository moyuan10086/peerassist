"""Project-scoped review job commands and event replay."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from peerassist.platform.errors import DependencyUnavailable, NotFound
from peerassist.platform.models import ReviewEvent, ReviewJob, TenantScope
from peerassist.platform.services.review_workspace import (
    ExportSubmission,
    ReviewWorkspaceService,
    SaveReviewDocument,
    SubmitReviewExport,
)
from peerassist.platform.services.reviews import (
    ChangeReviewJob,
    CreateReviewJob,
    DeleteReviewJob,
    RecordReviewDecision,
    ReviewService,
    SaveReviewDraft,
)

from .organizations import IdempotencyKey, ManagementActor

router = APIRouter(prefix="/api/v1", tags=["review-jobs"])


class ReviewJobView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    organization_id: UUID
    project_id: UUID
    paper_version_id: UUID
    mode: str
    stage: str
    status: str
    version: int
    attempt: int
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    cancelled_at: datetime | None
    safe_error_code: str | None


class ReviewEventView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    job_id: UUID
    aggregate_sequence: int
    event_type: str
    schema_version: int
    payload: dict[str, object]
    created_at: datetime


class CreateReviewBody(BaseModel):
    paper_version_id: UUID
    mode: Literal["fast", "full"] = "full"


class ChangeReviewBody(BaseModel):
    expected_version: int


class ReviewDecisionBody(BaseModel):
    decision_type: Literal["consent", "concern"]
    subject_id: str
    decision: str
    expected_version: int


class ReviewDraftBody(BaseModel):
    draft: str = Field(default="", max_length=200_000)
    expected_version: int


class ReviewDraftView(BaseModel):
    draft: str
    version: int


class ReviewExportSubmissionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    export_id: UUID
    job_id: UUID
    status: str
    document_version: int
    format: str


def _service(request: Request) -> ReviewService:
    return ReviewService(request.app.state.dependencies.uow_factory)


@router.post(
    "/projects/{project_id}/review-jobs",
    operation_id="v1_create_review_job",
    response_model=ReviewJobView,
    status_code=status.HTTP_201_CREATED,
)
def create_review(
    project_id: UUID,
    body: CreateReviewBody,
    request: Request,
    actor: ManagementActor,
    idempotency_key: IdempotencyKey,
) -> ReviewJob:
    return _service(request).create(
        actor,
        CreateReviewJob(
            project_id,
            body.paper_version_id,
            body.mode,
            idempotency_key,
            request.state.request_id,
        ),
    )


@router.get(
    "/projects/{project_id}/review-jobs",
    operation_id="v1_list_review_jobs",
    response_model=list[ReviewJobView],
)
def list_reviews(
    project_id: UUID, request: Request, actor: ManagementActor
) -> tuple[ReviewJob, ...]:
    return _service(request).list(actor, project_id)


@router.get(
    "/projects/{project_id}/review-jobs/{job_id}",
    operation_id="v1_get_review_job",
    response_model=ReviewJobView,
)
def get_review(
    project_id: UUID, job_id: UUID, request: Request, actor: ManagementActor
) -> ReviewJob:
    return _service(request).get(actor, project_id, job_id)


@router.get(
    "/projects/{project_id}/review-jobs/{job_id}/events",
    operation_id="v1_list_review_events",
    response_model=list[ReviewEventView],
)
def list_events(
    project_id: UUID, job_id: UUID, request: Request, actor: ManagementActor
) -> tuple[ReviewEvent, ...]:
    return _service(request).events(actor, project_id, job_id)


@router.get(
    "/projects/{project_id}/review-jobs/{job_id}/draft",
    operation_id="v1_get_review_draft",
    response_model=ReviewDraftView,
)
def get_draft(
    project_id: UUID, job_id: UUID, request: Request, actor: ManagementActor
) -> ReviewDraftView:
    job, draft = _service(request).get_draft(actor, project_id, job_id)
    return ReviewDraftView(draft=draft, version=job.version)


@router.patch(
    "/projects/{project_id}/review-jobs/{job_id}/draft",
    operation_id="v1_save_review_draft",
    response_model=ReviewJobView,
)
def save_draft(
    project_id: UUID,
    job_id: UUID,
    body: ReviewDraftBody,
    request: Request,
    actor: ManagementActor,
    idempotency_key: IdempotencyKey,
) -> ReviewJob:
    return _service(request).save_draft(
        actor,
        SaveReviewDraft(
            project_id,
            job_id,
            body.draft,
            body.expected_version,
            idempotency_key,
            request.state.request_id,
        ),
    )


@router.get(
    "/projects/{project_id}/review-jobs/{job_id}/event-stream",
    operation_id="v1_stream_review_events",
)
def stream_events(
    project_id: UUID, job_id: UUID, request: Request, actor: ManagementActor
) -> StreamingResponse:
    events = _service(request).events(actor, project_id, job_id)

    def replay():
        for event in events:
            payload = json.dumps(dict(event.payload), ensure_ascii=False, separators=(",", ":"))
            yield (
                f"id: {event.aggregate_sequence}\n"
                f"event: {event.event_type}\n"
                f"data: {payload}\n\n"
            )

    return StreamingResponse(replay(), media_type="text/event-stream")


def _change(
    operation: str,
    project_id: UUID,
    job_id: UUID,
    body: ChangeReviewBody,
    request: Request,
    actor: ManagementActor,
    idempotency_key: str,
) -> ReviewJob:
    change = ChangeReviewJob(
        project_id,
        job_id,
        body.expected_version,
        idempotency_key,
        request.state.request_id,
    )
    service = _service(request)
    return service.cancel(actor, change) if operation == "cancel" else service.retry(actor, change)


@router.post(
    "/projects/{project_id}/review-jobs/{job_id}/cancel",
    operation_id="v1_cancel_review_job",
    response_model=ReviewJobView,
)
def cancel_review(
    project_id: UUID,
    job_id: UUID,
    body: ChangeReviewBody,
    request: Request,
    actor: ManagementActor,
    idempotency_key: IdempotencyKey,
) -> ReviewJob:
    return _change("cancel", project_id, job_id, body, request, actor, idempotency_key)


@router.post(
    "/projects/{project_id}/review-jobs/{job_id}/retry",
    operation_id="v1_retry_review_job",
    response_model=ReviewJobView,
)
def retry_review(
    project_id: UUID,
    job_id: UUID,
    body: ChangeReviewBody,
    request: Request,
    actor: ManagementActor,
    idempotency_key: IdempotencyKey,
) -> ReviewJob:
    return _change("retry", project_id, job_id, body, request, actor, idempotency_key)


@router.delete(
    "/projects/{project_id}/review-jobs/{job_id}",
    operation_id="v1_delete_review_job",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_review(
    project_id: UUID,
    job_id: UUID,
    request: Request,
    actor: ManagementActor,
    idempotency_key: IdempotencyKey,
) -> Response:
    deleted, object_ids = _service(request).delete(
        actor,
        DeleteReviewJob(project_id, job_id, idempotency_key, request.state.request_id),
    )
    object_store = request.app.state.dependencies.object_store
    scope = TenantScope(deleted.organization_id, deleted.project_id)
    for object_id in object_ids:
        try:
            object_store.tombstone(scope, object_id)
        except (DependencyUnavailable, NotFound):
            # The database record is already gone; an unavailable object store
            # must not make a failed task reappear in the teacher's list.
            continue
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/projects/{project_id}/review-jobs/{job_id}/decisions",
    operation_id="v1_record_review_decision",
    response_model=ReviewJobView,
)
def record_decision(
    project_id: UUID,
    job_id: UUID,
    body: ReviewDecisionBody,
    request: Request,
    actor: ManagementActor,
    idempotency_key: IdempotencyKey,
) -> ReviewJob:
    return _service(request).record_decision(
        actor,
        RecordReviewDecision(
            project_id,
            job_id,
            body.decision_type,
            body.subject_id,
            body.decision,
            body.expected_version,
            idempotency_key,
            request.state.request_id,
        ),
    )


@router.post(
    "/projects/{project_id}/review-jobs/{job_id}/finalize",
    operation_id="v1_finalize_review_job",
    response_model=ReviewExportSubmissionView,
    status_code=status.HTTP_202_ACCEPTED,
)
def finalize_review(
    project_id: UUID,
    job_id: UUID,
    body: ChangeReviewBody,
    request: Request,
    actor: ManagementActor,
    idempotency_key: IdempotencyKey,
) -> ExportSubmission:
    service = _service(request)
    job = service.get(actor, project_id, job_id)
    if job.version != body.expected_version:
        from peerassist.platform.errors import StaleVersion

        raise StaleVersion(
            details={
                "expected_version": body.expected_version,
                "current_version": job.version,
            }
        )
    workspace = ReviewWorkspaceService(
        request.app.state.dependencies.uow_factory,
        request.app.state.dependencies.object_store,
    )
    document = workspace.get_document(actor, project_id, job_id)
    _, draft = service.get_draft(actor, project_id, job_id)
    overall = next(block for block in document.blocks if block.section == "overall_assessment")
    if draft.strip() and overall.text != draft:
        document = workspace.save_document(
            actor,
            SaveReviewDocument(
                project_id,
                job_id,
                tuple(
                    replace(block, text=draft, source_type="legacy_draft")
                    if block.section == "overall_assessment"
                    else block
                    for block in document.blocks
                ),
                document.document_version,
                f"{idempotency_key}:legacy-draft",
                request.state.request_id,
            ),
        )
    return workspace.submit_export(
        actor,
        SubmitReviewExport(
            project_id=project_id,
            job_id=job_id,
            expected_review_version=body.expected_version,
            expected_document_version=document.document_version,
            expected_decision_event_id=document.base_decision_event_id,
            format="markdown",
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
        ),
    )

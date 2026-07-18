"""Project-scoped review job commands and event replay."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from peerassist.platform.models import ReviewEvent, ReviewJob
from peerassist.platform.services.reviews import (
    ChangeReviewJob,
    CreateReviewJob,
    RecordReviewDecision,
    ReviewService,
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
    response_model=ReviewJobView,
)
def finalize_review(
    project_id: UUID,
    job_id: UUID,
    body: ChangeReviewBody,
    request: Request,
    actor: ManagementActor,
    idempotency_key: IdempotencyKey,
) -> ReviewJob:
    return _service(request).finalize(
        actor,
        ChangeReviewJob(
            project_id,
            job_id,
            body.expected_version,
            idempotency_key,
            request.state.request_id,
        ),
    )

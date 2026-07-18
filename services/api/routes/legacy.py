"""Authorized read-only compatibility routes for registered M0 jobs."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel

from peerassist.platform.services.legacy import LegacyService

from .organizations import ManagementActor
from .review_jobs import ReviewJobView

router = APIRouter(prefix="/api/v1", tags=["legacy-compatibility"])


class LegacyCompatibilityView(BaseModel):
    registration_id: UUID
    read_only: bool
    job: ReviewJobView


@router.get(
    "/projects/{project_id}/compat/{registration_id}",
    operation_id="v1_get_legacy_compatibility_job",
    response_model=LegacyCompatibilityView,
)
def get_legacy_job(
    project_id: UUID,
    registration_id: UUID,
    request: Request,
    actor: ManagementActor,
) -> LegacyCompatibilityView:
    reader = request.app.state.dependencies.legacy_reader
    if reader is None:
        from peerassist.platform.errors import NotFound

        raise NotFound()
    result = LegacyService(request.app.state.dependencies.uow_factory, reader).get_job(
        actor, project_id, registration_id
    )
    return LegacyCompatibilityView(
        registration_id=result.registration.id,
        read_only=True,
        job=ReviewJobView.model_validate(result.job),
    )

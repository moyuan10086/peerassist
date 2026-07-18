"""Project-scoped membership management routes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Request, status
from pydantic import BaseModel, ConfigDict

from peerassist.platform.models import ProjectMembership, Role
from peerassist.platform.services.memberships import (
    GrantProjectMembership,
    MembershipService,
    UpdateProjectMembership,
)

from .organizations import ManagementActor, ProjectView

router = APIRouter(prefix="/api/v1", tags=["projects"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)]


class ProjectMembershipView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    organization_id: UUID
    project_id: UUID
    user_id: UUID
    role: str
    status: str
    version: int
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None


class GrantProjectMembershipBody(BaseModel):
    user_id: UUID
    role: Literal["project_owner", "reviewer", "viewer"]
    expected_version: int | None = None


class UpdateProjectMembershipBody(BaseModel):
    role: Literal["project_owner", "reviewer", "viewer"]
    status: Literal["active", "revoked"]
    expected_version: int


def _request_id(request: Request) -> str:
    return request.state.request_id


@router.get("/projects/{project_id}", operation_id="v1_get_project", response_model=ProjectView)
def get_project(project_id: UUID, request: Request, actor: ManagementActor) -> object:
    return MembershipService(request.app.state.dependencies.uow_factory).get_project(actor, project_id)


@router.get(
    "/projects/{project_id}/members",
    operation_id="v1_list_project_members",
    response_model=list[ProjectMembershipView],
)
def list_members(project_id: UUID, request: Request, actor: ManagementActor) -> tuple[ProjectMembership, ...]:
    return MembershipService(request.app.state.dependencies.uow_factory).list_project_memberships(
        actor, project_id
    )


@router.post(
    "/projects/{project_id}/members",
    operation_id="v1_grant_project_member",
    response_model=ProjectMembershipView,
    status_code=status.HTTP_201_CREATED,
)
def grant_member(
    project_id: UUID,
    body: GrantProjectMembershipBody,
    request: Request,
    actor: ManagementActor,
    idempotency_key: IdempotencyKey,
) -> ProjectMembership:
    return MembershipService(request.app.state.dependencies.uow_factory).grant_project_membership(
        actor,
        GrantProjectMembership(
            project_id,
            body.user_id,
            Role(body.role),
            body.expected_version,
            idempotency_key,
            _request_id(request),
        ),
    )


@router.patch(
    "/projects/{project_id}/members/{membership_id}",
    operation_id="v1_update_project_member",
    response_model=ProjectMembershipView,
)
def update_member(
    project_id: UUID,
    membership_id: UUID,
    body: UpdateProjectMembershipBody,
    request: Request,
    actor: ManagementActor,
    idempotency_key: IdempotencyKey,
) -> ProjectMembership:
    return MembershipService(request.app.state.dependencies.uow_factory).update_project_membership(
        actor,
        UpdateProjectMembership(
            project_id,
            membership_id,
            Role(body.role),
            body.status,
            body.expected_version,
            idempotency_key,
            _request_id(request),
        ),
    )

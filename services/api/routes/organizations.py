"""Organization-scoped management routes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from peerassist.platform.errors import AuthenticationRequired
from peerassist.platform.models import (
    Action,
    Actor,
    ActorKind,
    AuditEvent,
    Organization,
    OrganizationMembership,
)
from peerassist.platform.services.audit import AuditFilter, AuditService
from peerassist.platform.services.memberships import (
    CreateProject,
    GrantOrganizationMembership,
    MembershipService,
    UpdateOrganizationMembership,
)
from services.api.dependencies import require_request_actor

router = APIRouter(prefix="/api/v1", tags=["organizations"])


def require_management_actor(request: Request) -> Actor:
    """Resolve a verified public user actor for management operations."""
    actor = require_request_actor(request)
    if not isinstance(actor, Actor) or actor.kind is not ActorKind.USER:
        raise AuthenticationRequired()
    return actor


ManagementActor = Annotated[Actor, Depends(require_management_actor)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)]


class OrganizationView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    slug: str
    name: str
    status: str
    version: int
    created_at: datetime
    updated_at: datetime


class ProjectView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    organization_id: UUID
    name: str
    status: str
    version: int
    created_at: datetime
    updated_at: datetime


class OrganizationMembershipView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    organization_id: UUID
    user_id: UUID
    role: str
    status: str
    version: int
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None


class CreateProjectBody(BaseModel):
    name: str = Field(min_length=1, max_length=255)

    @field_validator("name")
    @classmethod
    def reject_blank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        return value


class GrantOrganizationMembershipBody(BaseModel):
    user_id: UUID
    expected_version: int | None = None


class UpdateOrganizationMembershipBody(BaseModel):
    status: Literal["active", "revoked"]
    expected_version: int


class AuditEventView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    actor_id: UUID
    organization_id: UUID
    project_id: UUID | None
    action: str
    resource_type: str
    resource_id: UUID
    outcome: str
    request_id: str
    created_at: datetime
    identity_id: UUID | None
    command_id: UUID | None
    safe_metadata: dict[str, object]


def _request_id(request: Request) -> str:
    return request.state.request_id


@router.get("/organizations", operation_id="v1_list_organizations", response_model=list[OrganizationView])
def list_organizations(request: Request, actor: ManagementActor) -> tuple[Organization, ...]:
    return MembershipService(request.app.state.dependencies.uow_factory).list_organizations(actor)


@router.get(
    "/organizations/{organization_id}/projects",
    operation_id="v1_list_organization_projects",
    response_model=list[ProjectView],
)
def list_projects(organization_id: UUID, request: Request, actor: ManagementActor) -> tuple[object, ...]:
    return MembershipService(request.app.state.dependencies.uow_factory).list_projects(actor, organization_id)


@router.post(
    "/organizations/{organization_id}/projects",
    operation_id="v1_create_project",
    response_model=ProjectView,
    status_code=status.HTTP_201_CREATED,
)
def create_project(
    organization_id: UUID,
    body: CreateProjectBody,
    request: Request,
    actor: ManagementActor,
    idempotency_key: IdempotencyKey,
) -> object:
    return MembershipService(request.app.state.dependencies.uow_factory).create_project(
        actor,
        CreateProject(organization_id, body.name, idempotency_key, _request_id(request)),
    )


@router.get(
    "/organizations/{organization_id}/members",
    operation_id="v1_list_organization_members",
    response_model=list[OrganizationMembershipView],
)
def list_members(
    organization_id: UUID, request: Request, actor: ManagementActor
) -> tuple[OrganizationMembership, ...]:
    return MembershipService(request.app.state.dependencies.uow_factory).list_organization_memberships(
        actor, organization_id
    )


@router.post(
    "/organizations/{organization_id}/members",
    operation_id="v1_grant_organization_member",
    response_model=OrganizationMembershipView,
    status_code=status.HTTP_201_CREATED,
)
def grant_member(
    organization_id: UUID,
    body: GrantOrganizationMembershipBody,
    request: Request,
    actor: ManagementActor,
    idempotency_key: IdempotencyKey,
) -> OrganizationMembership:
    return MembershipService(request.app.state.dependencies.uow_factory).grant_organization_membership(
        actor,
        GrantOrganizationMembership(
            organization_id,
            body.user_id,
            body.expected_version,
            idempotency_key,
            _request_id(request),
        ),
    )


@router.patch(
    "/organizations/{organization_id}/members/{membership_id}",
    operation_id="v1_update_organization_member",
    response_model=OrganizationMembershipView,
)
def update_member(
    organization_id: UUID,
    membership_id: UUID,
    body: UpdateOrganizationMembershipBody,
    request: Request,
    actor: ManagementActor,
    idempotency_key: IdempotencyKey,
) -> OrganizationMembership:
    return MembershipService(request.app.state.dependencies.uow_factory).update_organization_membership(
        actor,
        UpdateOrganizationMembership(
            organization_id,
            membership_id,
            body.status,
            body.expected_version,
            idempotency_key,
            _request_id(request),
        ),
    )


@router.get(
    "/organizations/{organization_id}/audit-events",
    operation_id="v1_list_organization_audit_events",
    response_model=list[AuditEventView],
)
def list_audit_events(
    organization_id: UUID,
    request: Request,
    actor: ManagementActor,
    project_id: UUID | None = Query(default=None),
    action: Action | None = Query(default=None),
    outcome: str | None = Query(default=None),
    resource_type: str | None = Query(default=None),
) -> tuple[AuditEvent, ...]:
    return AuditService(request.app.state.dependencies.uow_factory).list_events(
        actor,
        organization_id,
        AuditFilter(
            project_id=project_id,
            action=action,
            outcome=outcome,
            resource_type=resource_type,
        ),
    )

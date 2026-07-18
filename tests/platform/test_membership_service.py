from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from peerassist.platform.adapters.memory import MemoryUnitOfWorkFactory
from peerassist.platform.errors import Forbidden, NotFound, StaleVersion
from peerassist.platform.models import (
    Actor,
    ActorKind,
    Organization,
    OrganizationMembership,
    Project,
    ProjectMembership,
    Role,
    TenantScope,
    User,
)
from peerassist.platform.services.audit import AuditFilter, AuditService
from peerassist.platform.services.memberships import (
    CreateProject,
    GrantOrganizationMembership,
    GrantProjectMembership,
    MembershipService,
    UpdateOrganizationMembership,
    UpdateProjectMembership,
)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def _seed() -> tuple[Clock, MemoryUnitOfWorkFactory, Actor, Actor, Actor, Organization, Project]:
    clock = Clock()
    factory = MemoryUnitOfWorkFactory(clock=clock)
    admin = Actor(uuid4(), ActorKind.USER)
    member = Actor(uuid4(), ActorKind.USER)
    outsider = Actor(uuid4(), ActorKind.USER)
    organization = Organization(uuid4(), "alpha", "Alpha", "active", 1, clock(), clock())
    project = Project(uuid4(), organization.id, "Initial", "active", 1, clock(), clock())
    admin_membership = OrganizationMembership(
        uuid4(), organization.id, admin.actor_id, Role.ORGANIZATION_ADMIN, "active", 1, clock(), clock()
    )
    with factory(admin) as uow:
        for actor in (admin, member, outsider):
            uow.users.add(User(actor.actor_id, "active", f"User {actor.actor_id.hex[:6]}", clock(), clock()))
        uow.organizations.add(TenantScope(organization.id), organization)
        uow.organizations.save_membership(TenantScope(organization.id), admin_membership, None)
        uow.projects.add(project.scope, project)
        uow.commit()
    return clock, factory, admin, member, outsider, organization, project


def test_user_with_no_membership_sees_no_organizations_or_projects() -> None:
    clock, factory, _, _, outsider, _, _ = _seed()
    service = MembershipService(factory, clock=clock)

    assert service.list_organizations(outsider) == ()
    assert service.list_projects(outsider) == ()


def test_organization_and_project_grant_change_revoke_use_expected_versions_immediately() -> None:
    clock, factory, admin, member, _, organization, project = _seed()
    service = MembershipService(factory, clock=clock)

    org_membership = service.grant_organization_membership(
        admin,
        GrantOrganizationMembership(
            organization.id,
            member.actor_id,
            expected_version=None,
            idempotency_key="grant-org-member",
            request_id="req-grant-org",
        ),
    )
    assert org_membership.version == 1

    project_membership = service.grant_project_membership(
        admin,
        GrantProjectMembership(
            project.id,
            member.actor_id,
            Role.REVIEWER,
            expected_version=None,
            idempotency_key="grant-project-member",
            request_id="req-grant-project",
        ),
    )
    assert service.get_project(member, project.id) == project

    changed = service.update_project_membership(
        admin,
        UpdateProjectMembership(
            project.id,
            project_membership.id,
            role=Role.VIEWER,
            status="active",
            expected_version=1,
            idempotency_key="change-project-member",
            request_id="req-change-project",
        ),
    )
    assert changed.role is Role.VIEWER
    assert changed.version == 2

    replayed_grant = service.grant_project_membership(
        admin,
        GrantProjectMembership(
            project.id,
            member.actor_id,
            Role.REVIEWER,
            expected_version=None,
            idempotency_key="grant-project-member",
            request_id="req-grant-project-replay",
        ),
    )
    assert replayed_grant == project_membership

    with pytest.raises(StaleVersion):
        service.update_project_membership(
            admin,
            UpdateProjectMembership(
                project.id,
                project_membership.id,
                role=Role.REVIEWER,
                status="active",
                expected_version=1,
                idempotency_key="stale-project-member",
                request_id="req-stale-project",
            ),
        )

    clock.now += timedelta(seconds=1)
    revoked = service.update_project_membership(
        admin,
        UpdateProjectMembership(
            project.id,
            project_membership.id,
            role=Role.VIEWER,
            status="revoked",
            expected_version=2,
            idempotency_key="revoke-project-member",
            request_id="req-revoke-project",
        ),
    )
    assert revoked.revoked_at == clock()
    service.update_organization_membership(
        admin,
        UpdateOrganizationMembership(
            organization.id,
            org_membership.id,
            status="revoked",
            expected_version=1,
            idempotency_key="revoke-org-member",
            request_id="req-revoke-org",
        ),
    )
    with pytest.raises(NotFound):
        service.get_project(member, project.id)


def test_cross_tenant_is_not_found_visible_denial_is_forbidden_and_audit_filters() -> None:
    clock, factory, admin, member, outsider, organization, project = _seed()
    service = MembershipService(factory, clock=clock)
    viewer = ProjectMembership(
        uuid4(), organization.id, project.id, member.actor_id, Role.VIEWER, "active", 1, clock(), clock()
    )
    with factory(admin) as uow:
        uow.projects.save_membership(project.scope, viewer, None)
        uow.commit()

    with pytest.raises(Forbidden):
        service.list_project_memberships(member, project.id)
    with pytest.raises(NotFound):
        service.list_project_memberships(outsider, project.id)
    assert service.list_project_memberships(admin, project.id) == (viewer,)

    with pytest.raises(Forbidden):
        service.grant_project_membership(
            member,
            GrantProjectMembership(
                project.id, outsider.actor_id, Role.VIEWER, None, "viewer-denied", "req-viewer-denied"
            ),
        )
    with pytest.raises(NotFound):
        service.grant_project_membership(
            outsider,
            GrantProjectMembership(
                project.id, outsider.actor_id, Role.VIEWER, None, "outsider-hidden", "req-outsider-hidden"
            ),
        )

    created = service.create_project(
        admin,
        CreateProject(organization.id, "Audited", "create-audited", "req-create-audited"),
    )
    service.grant_project_membership(
        admin,
        GrantProjectMembership(
            created.id, member.actor_id, Role.REVIEWER, None, "audited-grant", "req-audited-grant"
        ),
    )
    audit = AuditService(factory).list_events(
        admin,
        organization.id,
        AuditFilter(project_id=created.id, outcome="succeeded"),
    )
    assert audit
    assert all(event.project_id == created.id and event.outcome == "succeeded" for event in audit)

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from peerassist.platform.adapters.memory import MemoryUnitOfWorkFactory
from peerassist.platform.errors import Forbidden, NotFound
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
    MembershipService,
    UpdateProjectMembership,
)


def _matrix() -> tuple[
    MemoryUnitOfWorkFactory,
    dict[str, Actor],
    dict[str, Organization],
    dict[str, Project],
    dict[str, ProjectMembership],
]:
    now = datetime(2026, 7, 18, 14, 0, tzinfo=UTC)
    factory = MemoryUnitOfWorkFactory(clock=lambda: now)
    actors = {
        name: Actor(uuid4(), ActorKind.USER)
        for name in ("admin_a", "owner_a", "reviewer_a", "viewer_a", "admin_b")
    }
    organizations = {
        "a": Organization(uuid4(), "tenant-a", "Tenant A", "active", 1, now, now),
        "b": Organization(uuid4(), "tenant-b", "Tenant B", "active", 1, now, now),
    }
    projects = {
        key: Project(uuid4(), organizations[key].id, "Review", "active", 1, now, now)
        for key in organizations
    }
    project_memberships = {
        role: ProjectMembership(
            uuid4(),
            organizations["a"].id,
            projects["a"].id,
            actors[f"{role}_a"].actor_id,
            Role.PROJECT_OWNER if role == "owner" else Role(role),
            "active",
            1,
            now,
            now,
        )
        for role in ("owner", "reviewer", "viewer")
    }
    with factory(actors["admin_a"]) as uow:
        for name, actor in actors.items():
            uow.users.add(User(actor.actor_id, "active", name, now, now))
        for key, organization in organizations.items():
            scope = TenantScope(organization.id)
            uow.organizations.add(scope, organization)
            admin = actors[f"admin_{key}"]
            uow.organizations.save_membership(
                scope,
                OrganizationMembership(
                    uuid4(), organization.id, admin.actor_id,
                    Role.ORGANIZATION_ADMIN, "active", 1, now, now,
                ),
                None,
            )
            uow.projects.add(projects[key].scope, projects[key])
        for membership in project_memberships.values():
            uow.projects.save_membership(projects["a"].scope, membership, None)
        uow.commit()
    return factory, actors, organizations, projects, project_memberships


def test_role_matrix_hides_other_tenants_and_distinguishes_visible_denials() -> None:
    factory, actors, organizations, projects, _ = _matrix()
    service = MembershipService(factory)

    for name in ("admin_a", "owner_a", "reviewer_a", "viewer_a"):
        assert service.list_organizations(actors[name]) == (organizations["a"],)
        assert service.get_project(actors[name], projects["a"].id) == projects["a"]
        with pytest.raises(NotFound):
            service.get_project(actors[name], projects["b"].id)

    assert service.list_organizations(actors["admin_b"]) == (organizations["b"],)
    assert service.list_project_memberships(actors["admin_a"], projects["a"].id)
    assert service.list_project_memberships(actors["owner_a"], projects["a"].id)
    for name in ("reviewer_a", "viewer_a"):
        with pytest.raises(Forbidden):
            service.list_project_memberships(actors[name], projects["a"].id)
        with pytest.raises(Forbidden):
            service.create_project(
                actors[name],
                CreateProject(organizations["a"].id, "Denied", name, f"request-{name}"),
            )
    with pytest.raises(NotFound):
        service.list_project_memberships(actors["admin_b"], projects["a"].id)
    with pytest.raises(NotFound):
        AuditService(factory).list_events(
            actors["admin_b"], organizations["a"].id, AuditFilter()
        )


def test_repository_scope_and_membership_revocation_fail_closed_immediately() -> None:
    factory, actors, organizations, projects, memberships = _matrix()
    wrong_scope = TenantScope(organizations["b"].id, projects["a"].id)
    with factory(actors["admin_b"]) as uow:
        assert uow.projects.get(wrong_scope) is None
        assert uow.projects.list(wrong_scope) == ()
        assert uow.projects.list_memberships(wrong_scope) == ()

    service = MembershipService(factory)
    reviewer = memberships["reviewer"]
    revoked = service.update_project_membership(
        actors["admin_a"],
        UpdateProjectMembership(
            projects["a"].id,
            reviewer.id,
            reviewer.role,
            "revoked",
            reviewer.version,
            "revoke-reviewer",
            "request-revoke-reviewer",
        ),
    )
    assert revoked.status == "revoked"
    with pytest.raises(NotFound):
        service.get_project(actors["reviewer_a"], projects["a"].id)

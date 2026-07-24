from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from peerassist.platform.adapters.memory import MemoryUnitOfWorkFactory
from peerassist.platform.errors import NotFound
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
from peerassist.platform.services.workspace_bootstrap import WorkspaceBootstrapService

NOW = datetime(2026, 7, 24, 10, 0, tzinfo=UTC)


def _seed(*, projects: int = 0):
    factory = MemoryUnitOfWorkFactory(clock=lambda: NOW)
    actor = Actor(uuid4(), ActorKind.USER)
    organization = Organization(uuid4(), "teacher-org", "Teacher Org", "active", 1, NOW, NOW)
    with factory(actor) as uow:
        uow.users.add(User(actor.actor_id, "active", "Teacher", NOW, NOW))
        uow.organizations.add(TenantScope(organization.id), organization)
        uow.organizations.save_membership(
            TenantScope(organization.id),
            OrganizationMembership(
                uuid4(), organization.id, actor.actor_id, Role.ORGANIZATION_ADMIN,
                "active", 1, NOW, NOW,
            ),
            None,
        )
        for index in range(projects):
            project = Project(uuid4(), organization.id, f"Project {index}", "active", 1, NOW, NOW)
            uow.projects.add(project.scope, project)
            uow.projects.save_membership(
                project.scope,
                ProjectMembership(
                    uuid4(), organization.id, project.id, actor.actor_id,
                    Role.PROJECT_OWNER, "active", 1, NOW, NOW,
                ),
                None,
            )
        uow.commit()
    return factory, actor, organization


def test_zero_projects_bootstraps_one_default_personal_workspace() -> None:
    factory, actor, organization = _seed()

    state = WorkspaceBootstrapService(factory, clock=lambda: NOW).get(actor)

    assert state.status == "ready"
    assert state.project is not None
    assert state.project.name == "我的审稿"
    assert state.membership is not None and state.membership.is_default is True
    with factory(actor) as uow:
        projects = tuple(uow.projects.list_for_organization(TenantScope(organization.id)))
        assert len(projects) == 1
        assert uow.projects.get_membership(state.project.scope, actor.actor_id).is_default is True


def test_single_project_becomes_default_without_using_list_order() -> None:
    factory, actor, _ = _seed(projects=1)

    state = WorkspaceBootstrapService(factory, clock=lambda: NOW).get(actor)

    assert state.status == "ready"
    assert state.project is not None
    assert state.membership is not None and state.membership.is_default is True


def test_multiple_projects_without_default_require_explicit_selection() -> None:
    factory, actor, _ = _seed(projects=2)

    state = WorkspaceBootstrapService(factory, clock=lambda: NOW).get(actor)

    assert state.status == "workspace_selection_required"
    assert state.project is None
    assert len(state.projects) == 2


def test_explicit_selection_persists_one_default_project() -> None:
    factory, actor, _ = _seed(projects=2)
    service = WorkspaceBootstrapService(factory, clock=lambda: NOW)
    available = service.get(actor).projects

    selected = service.select(actor, available[1].id)
    restored = service.get(actor)

    assert selected.status == "ready"
    assert selected.project == available[1]
    assert restored.project == available[1]
    with factory(actor) as uow:
        defaults = [
            membership
            for membership in uow.projects.list_for_user(actor.actor_id)
            if membership.is_default
        ]
        assert len(defaults) == 1 and defaults[0].project_id == available[1].id


def test_unknown_user_does_not_create_workspace() -> None:
    factory, _, _ = _seed()
    unknown = Actor(uuid4(), ActorKind.USER)

    with pytest.raises(NotFound):
        WorkspaceBootstrapService(factory, clock=lambda: NOW).get(unknown)

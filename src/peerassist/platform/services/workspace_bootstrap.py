"""Resolve the teacher's default project without relying on browser state."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid5

from ..errors import NotFound
from ..models import Actor, Organization, Project, ProjectMembership, Role, TenantScope
from ..ports import UnitOfWork, UnitOfWorkFactory


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class WorkspaceState:
    status: str
    organization: Organization | None
    project: Project | None
    projects: tuple[Project, ...]
    membership: ProjectMembership | None


class WorkspaceBootstrapService:
    """Materialize one default workspace, or return an explicit selection state."""

    def __init__(self, uow_factory: UnitOfWorkFactory, *, clock=_utc_now) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def get(self, actor: Actor) -> WorkspaceState:
        with self._uow_factory(actor) as uow:
            user = uow.users.get(actor.actor_id)
            if user is None or user.status != "active":
                raise NotFound()
            organizations = tuple(
                membership
                for membership in uow.organizations.list_for_user(actor.actor_id)
                if membership.status == "active"
            )
            project_memberships = tuple(
                membership
                for membership in uow.projects.list_for_user(actor.actor_id)
                if membership.status == "active"
            )
            project_map: dict[UUID, Project] = {}
            organization_map: dict[UUID, Organization] = {}
            for membership in organizations:
                organization = uow.organizations.get(TenantScope(membership.organization_id))
                if organization is None:
                    continue
                organization_map[organization.id] = organization
                for project in uow.projects.list_for_organization(TenantScope(organization.id)):
                    if project.status == "active":
                        project_map[project.id] = project
            for membership in project_memberships:
                project = uow.projects.get(
                    TenantScope(membership.organization_id, membership.project_id)
                )
                if project is not None and project.status == "active":
                    project_map[project.id] = project
                    organization = uow.organizations.get(TenantScope(project.organization_id))
                    if organization is not None:
                        organization_map[organization.id] = organization

            projects = tuple(sorted(project_map.values(), key=lambda item: item.id.int))
            if not projects:
                if len(organizations) != 1:
                    return WorkspaceState("organization_selection_required", None, None, (), None)
                organization = organization_map.get(organizations[0].organization_id)
                if organization is None:
                    raise NotFound()
                project, membership = self._create_personal_workspace(
                    uow, organization, actor.actor_id
                )
                uow.commit()
                return WorkspaceState("ready", organization, project, (project,), membership)

            memberships_by_project = {
                membership.project_id: membership
                for membership in project_memberships
                if membership.project_id in project_map
            }
            defaults = tuple(
                memberships_by_project[project.id]
                for project in projects
                if memberships_by_project.get(project.id) is not None
                and memberships_by_project[project.id].is_default
            )
            if len(projects) > 1 and len(defaults) != 1:
                return WorkspaceState(
                    "workspace_selection_required",
                    self._single_organization(organization_map),
                    None,
                    projects,
                    None,
                )
            project = projects[0] if len(projects) == 1 else next(
                project for project in projects if project.id == defaults[0].project_id
            )
            organization = organization_map.get(project.organization_id)
            if organization is None:
                raise NotFound()
            membership = memberships_by_project.get(project.id)
            if membership is None or not membership.is_default:
                membership = self._set_default_membership(uow, project, actor.actor_id, membership)
                uow.commit()
            return WorkspaceState("ready", organization, project, projects, membership)

    def select(self, actor: Actor, project_id: UUID) -> WorkspaceState:
        """Persist an explicitly selected accessible project as the user's default."""

        state = self.get(actor)
        selected = next((project for project in state.projects if project.id == project_id), None)
        if selected is None:
            raise NotFound()
        with self._uow_factory(actor) as uow:
            project = uow.projects.get(selected.scope)
            if project is None or project.status != "active":
                raise NotFound()
            current = uow.projects.get_membership(project.scope, actor.actor_id)
            if current is not None and current.status != "active":
                raise NotFound()
            self._set_default_membership(uow, project, actor.actor_id, current)
            uow.commit()
        return self.get(actor)

    def _create_personal_workspace(
        self,
        uow: UnitOfWork,
        organization: Organization,
        user_id: UUID,
    ) -> tuple[Project, ProjectMembership]:
        now = self._clock()
        project = Project(
            uuid5(organization.id, f"peerassist:personal-workspace:{user_id}"),
            organization.id,
            "我的审稿",
            "active",
            1,
            now,
            now,
        )
        membership = ProjectMembership(
            uuid5(project.id, f"member:{user_id}"),
            organization.id,
            project.id,
            user_id,
            Role.PROJECT_OWNER,
            "active",
            1,
            now,
            now,
            None,
            True,
        )
        uow.projects.add(project.scope, project)
        uow.projects.save_membership(project.scope, membership, None)
        return project, membership

    def _set_default_membership(
        self,
        uow: UnitOfWork,
        project: Project,
        user_id: UUID,
        current: ProjectMembership | None,
    ) -> ProjectMembership:
        now = self._clock()
        if current is None:
            membership = ProjectMembership(
                uuid5(project.id, f"member:{user_id}"),
                project.organization_id,
                project.id,
                user_id,
                Role.PROJECT_OWNER,
                "active",
                1,
                now,
                now,
                None,
                True,
            )
            uow.projects.save_membership(project.scope, membership, None)
            return membership
        membership = replace(
            current,
            status="active",
            revoked_at=None,
            is_default=True,
            version=current.version + 1,
            updated_at=now,
        )
        uow.projects.save_membership(project.scope, membership, current.version)
        return membership

    @staticmethod
    def _single_organization(organizations: dict[UUID, Organization]) -> Organization | None:
        return next(iter(organizations.values())) if len(organizations) == 1 else None

"""Tenant and membership administration with fresh database authorization checks."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from ..errors import Forbidden, IdempotencyConflict, NotFound
from ..idempotency import canonical_json_digest
from ..models import (
    Action,
    Actor,
    AuditEvent,
    CommandRecord,
    Organization,
    OrganizationMembership,
    Project,
    ProjectMembership,
    Role,
    TenantScope,
)
from ..ports import UnitOfWork, UnitOfWorkFactory

Clock = Callable[[], datetime]


def _now() -> datetime:
    return datetime.now(UTC)


def _active(status: str) -> bool:
    return status == "active"


def _time(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _parsed_time(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise IdempotencyConflict()
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise IdempotencyConflict() from None


def _project_response(project: Project) -> dict[str, object]:
    return {
        "id": str(project.id),
        "organization_id": str(project.organization_id),
        "name": project.name,
        "status": project.status,
        "version": project.version,
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
    }


def _project_from_response(response: object) -> Project:
    if not isinstance(response, Mapping):
        raise IdempotencyConflict()
    try:
        return Project(
            UUID(str(response["id"])),
            UUID(str(response["organization_id"])),
            str(response["name"]),
            str(response["status"]),
            int(response["version"]),
            _parsed_time(response["created_at"]),
            _parsed_time(response["updated_at"]),
        )
    except (KeyError, TypeError, ValueError):
        raise IdempotencyConflict() from None


def _membership_response(
    membership: OrganizationMembership | ProjectMembership,
) -> dict[str, object]:
    response: dict[str, object] = {
        "id": str(membership.id),
        "organization_id": str(membership.organization_id),
        "user_id": str(membership.user_id),
        "role": membership.role.value,
        "status": membership.status,
        "version": membership.version,
        "created_at": membership.created_at.isoformat(),
        "updated_at": membership.updated_at.isoformat(),
        "revoked_at": _time(membership.revoked_at),
    }
    if isinstance(membership, ProjectMembership):
        response["project_id"] = str(membership.project_id)
    return response


def _organization_membership_from_response(response: object) -> OrganizationMembership:
    if not isinstance(response, Mapping):
        raise IdempotencyConflict()
    try:
        return OrganizationMembership(
            UUID(str(response["id"])),
            UUID(str(response["organization_id"])),
            UUID(str(response["user_id"])),
            Role(str(response["role"])),
            str(response["status"]),
            int(response["version"]),
            _parsed_time(response["created_at"]),
            _parsed_time(response["updated_at"]),
            _parsed_time(response.get("revoked_at")),
        )
    except (KeyError, TypeError, ValueError):
        raise IdempotencyConflict() from None


def _project_membership_from_response(response: object) -> ProjectMembership:
    if not isinstance(response, Mapping):
        raise IdempotencyConflict()
    try:
        return ProjectMembership(
            UUID(str(response["id"])),
            UUID(str(response["organization_id"])),
            UUID(str(response["project_id"])),
            UUID(str(response["user_id"])),
            Role(str(response["role"])),
            str(response["status"]),
            int(response["version"]),
            _parsed_time(response["created_at"]),
            _parsed_time(response["updated_at"]),
            _parsed_time(response.get("revoked_at")),
        )
    except (KeyError, TypeError, ValueError):
        raise IdempotencyConflict() from None


@dataclass(frozen=True, slots=True)
class CreateProject:
    organization_id: UUID
    name: str
    idempotency_key: str
    request_id: str


@dataclass(frozen=True, slots=True)
class GrantOrganizationMembership:
    organization_id: UUID
    user_id: UUID
    expected_version: int | None
    idempotency_key: str
    request_id: str


@dataclass(frozen=True, slots=True)
class UpdateOrganizationMembership:
    organization_id: UUID
    membership_id: UUID
    status: str
    expected_version: int
    idempotency_key: str
    request_id: str


@dataclass(frozen=True, slots=True)
class GrantProjectMembership:
    project_id: UUID
    user_id: UUID
    role: Role
    expected_version: int | None
    idempotency_key: str
    request_id: str


@dataclass(frozen=True, slots=True)
class UpdateProjectMembership:
    project_id: UUID
    membership_id: UUID
    role: Role
    status: str
    expected_version: int
    idempotency_key: str
    request_id: str


class MembershipService:
    def __init__(self, uow_factory: UnitOfWorkFactory, *, clock: Clock = _now) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def list_organizations(self, actor: Actor) -> tuple[Organization, ...]:
        with self._uow_factory(actor) as uow:
            organization_ids = {
                membership.organization_id
                for membership in uow.organizations.list_for_user(actor.actor_id)
                if _active(membership.status)
            }
            organization_ids.update(
                membership.organization_id
                for membership in uow.projects.list_for_user(actor.actor_id)
                if _active(membership.status)
            )
            result = tuple(
                organization
                for organization_id in sorted(organization_ids, key=lambda value: value.int)
                if (organization := uow.organizations.get(TenantScope(organization_id))) is not None
            )
            return result

    def list_projects(self, actor: Actor, organization_id: UUID | None = None) -> tuple[Project, ...]:
        with self._uow_factory(actor) as uow:
            projects: dict[UUID, Project] = {}
            for membership in uow.organizations.list_for_user(actor.actor_id):
                if _active(membership.status) and (
                    organization_id is None or membership.organization_id == organization_id
                ):
                    scope = TenantScope(membership.organization_id)
                    projects.update(
                        (project.id, project) for project in uow.projects.list_for_organization(scope)
                    )
            for membership in uow.projects.list_for_user(actor.actor_id):
                if not _active(membership.status) or (
                    organization_id is not None and membership.organization_id != organization_id
                ):
                    continue
                project = uow.projects.get(TenantScope(membership.organization_id, membership.project_id))
                if project is not None:
                    projects[project.id] = project
            if organization_id is not None and not self._organization_visible(uow, actor, organization_id):
                raise NotFound()
            return tuple(projects[key] for key in sorted(projects, key=lambda value: value.int))

    def get_project(self, actor: Actor, project_id: UUID) -> Project:
        with self._uow_factory(actor) as uow:
            return self._visible_project(uow, actor, project_id)

    def list_organization_memberships(
        self, actor: Actor, organization_id: UUID
    ) -> tuple[OrganizationMembership, ...]:
        with self._uow_factory(actor) as uow:
            self._require_organization_admin(uow, actor, organization_id)
            return tuple(uow.organizations.list_memberships(TenantScope(organization_id)))

    def list_project_memberships(self, actor: Actor, project_id: UUID) -> tuple[ProjectMembership, ...]:
        with self._uow_factory(actor) as uow:
            project = self._require_project_manager(uow, actor, project_id)
            return tuple(uow.projects.list_memberships(project.scope))

    def create_project(self, actor: Actor, request: CreateProject) -> Project:
        with self._uow_factory(actor) as uow:
            self._require_organization_admin(uow, actor, request.organization_id)
            scope = TenantScope(request.organization_id)
            payload = {"name": request.name}
            reserved = self._reserve(
                uow,
                actor,
                scope,
                "project.create",
                request.idempotency_key,
                payload,
            )
            project_id = uuid5(reserved.id, "project")
            project_scope = TenantScope(request.organization_id, project_id)
            if reserved.completed_at is not None:
                return _project_from_response(reserved.response_body)
            now = self._clock()
            project = Project(project_id, request.organization_id, request.name, "active", 1, now, now)
            uow.projects.add(project_scope, project)
            self._audit(
                uow,
                actor,
                project_scope,
                reserved,
                Action.PROJECT_CREATE,
                "project",
                project.id,
                request.request_id,
                {},
            )
            self._complete(uow, scope, reserved, 201, _project_response(project))
            uow.commit()
            return project

    def grant_organization_membership(
        self, actor: Actor, request: GrantOrganizationMembership
    ) -> OrganizationMembership:
        scope = TenantScope(request.organization_id)
        with self._uow_factory(actor) as uow:
            self._require_organization_admin(uow, actor, request.organization_id)
            if uow.users.get(request.user_id) is None:
                raise NotFound()
            payload = {
                "user_id": str(request.user_id),
                "role": Role.ORGANIZATION_ADMIN.value,
                "expected_version": request.expected_version,
            }
            reserved = self._reserve(
                uow, actor, scope, "organization_membership.grant", request.idempotency_key, payload
            )
            current = uow.organizations.get_membership(scope, request.user_id)
            if reserved.completed_at is not None:
                return _organization_membership_from_response(reserved.response_body)
            now = self._clock()
            membership = OrganizationMembership(
                current.id if current else uuid5(scope.organization_id, f"member:{request.user_id}"),
                scope.organization_id,
                request.user_id,
                Role.ORGANIZATION_ADMIN,
                "active",
                1 if current is None else current.version + 1,
                now if current is None else current.created_at,
                now,
                None,
            )
            uow.organizations.save_membership(scope, membership, request.expected_version)
            self._audit(
                uow,
                actor,
                scope,
                reserved,
                Action.ORGANIZATION_MEMBERSHIP_GRANT,
                "organization_membership",
                membership.id,
                request.request_id,
                {"role": membership.role.value, "version": membership.version},
            )
            self._complete(uow, scope, reserved, 201, _membership_response(membership))
            uow.commit()
            return membership

    def update_organization_membership(
        self, actor: Actor, request: UpdateOrganizationMembership
    ) -> OrganizationMembership:
        if request.status not in {"active", "revoked"}:
            raise ValueError("membership status must be active or revoked")
        scope = TenantScope(request.organization_id)
        with self._uow_factory(actor) as uow:
            self._require_organization_admin(uow, actor, request.organization_id)
            current = uow.organizations.get_membership_by_id(scope, request.membership_id)
            if current is None:
                raise NotFound()
            payload = {
                "membership_id": str(request.membership_id),
                "status": request.status,
                "expected_version": request.expected_version,
            }
            operation = (
                "organization_membership.revoke"
                if request.status == "revoked"
                else "organization_membership.change"
            )
            reserved = self._reserve(uow, actor, scope, operation, request.idempotency_key, payload)
            if reserved.completed_at is not None:
                return _organization_membership_from_response(reserved.response_body)
            now = self._clock()
            updated = replace(
                current,
                status=request.status,
                version=current.version + 1,
                updated_at=now,
                revoked_at=now if request.status == "revoked" else None,
            )
            uow.organizations.save_membership(scope, updated, request.expected_version)
            action = (
                Action.ORGANIZATION_MEMBERSHIP_REVOKE
                if request.status == "revoked"
                else Action.ORGANIZATION_MEMBERSHIP_CHANGE
            )
            self._audit(
                uow,
                actor,
                scope,
                reserved,
                action,
                "organization_membership",
                updated.id,
                request.request_id,
                {"status": updated.status, "version": updated.version},
            )
            self._complete(uow, scope, reserved, 200, _membership_response(updated))
            uow.commit()
            return updated

    def grant_project_membership(self, actor: Actor, request: GrantProjectMembership) -> ProjectMembership:
        if request.role not in {Role.PROJECT_OWNER, Role.REVIEWER, Role.VIEWER}:
            raise ValueError("invalid project role")
        with self._uow_factory(actor) as uow:
            project = self._require_project_manager(uow, actor, request.project_id)
            if uow.users.get(request.user_id) is None:
                raise NotFound()
            scope = project.scope
            payload = {
                "user_id": str(request.user_id),
                "role": request.role.value,
                "expected_version": request.expected_version,
            }
            reserved = self._reserve(
                uow, actor, scope, "project_membership.grant", request.idempotency_key, payload
            )
            current = uow.projects.get_membership(scope, request.user_id)
            if reserved.completed_at is not None:
                return _project_membership_from_response(reserved.response_body)
            now = self._clock()
            membership = ProjectMembership(
                current.id if current else uuid5(project.id, f"member:{request.user_id}"),
                project.organization_id,
                project.id,
                request.user_id,
                request.role,
                "active",
                1 if current is None else current.version + 1,
                now if current is None else current.created_at,
                now,
                None,
            )
            uow.projects.save_membership(scope, membership, request.expected_version)
            self._audit(
                uow,
                actor,
                scope,
                reserved,
                Action.PROJECT_MEMBERSHIP_GRANT,
                "project_membership",
                membership.id,
                request.request_id,
                {"role": membership.role.value, "version": membership.version},
            )
            self._complete(uow, scope, reserved, 201, _membership_response(membership))
            uow.commit()
            return membership

    def update_project_membership(self, actor: Actor, request: UpdateProjectMembership) -> ProjectMembership:
        if request.status not in {"active", "revoked"}:
            raise ValueError("membership status must be active or revoked")
        if request.role not in {Role.PROJECT_OWNER, Role.REVIEWER, Role.VIEWER}:
            raise ValueError("invalid project role")
        with self._uow_factory(actor) as uow:
            project = self._require_project_manager(uow, actor, request.project_id)
            scope = project.scope
            current = uow.projects.get_membership_by_id(scope, request.membership_id)
            if current is None:
                raise NotFound()
            payload = {
                "membership_id": str(request.membership_id),
                "role": request.role.value,
                "status": request.status,
                "expected_version": request.expected_version,
            }
            operation = (
                "project_membership.revoke" if request.status == "revoked" else "project_membership.change"
            )
            reserved = self._reserve(uow, actor, scope, operation, request.idempotency_key, payload)
            if reserved.completed_at is not None:
                return _project_membership_from_response(reserved.response_body)
            now = self._clock()
            updated = replace(
                current,
                role=request.role,
                status=request.status,
                version=current.version + 1,
                updated_at=now,
                revoked_at=now if request.status == "revoked" else None,
            )
            uow.projects.save_membership(scope, updated, request.expected_version)
            action = (
                Action.PROJECT_MEMBERSHIP_REVOKE
                if request.status == "revoked"
                else Action.PROJECT_MEMBERSHIP_CHANGE
            )
            self._audit(
                uow,
                actor,
                scope,
                reserved,
                action,
                "project_membership",
                updated.id,
                request.request_id,
                {"role": updated.role.value, "status": updated.status, "version": updated.version},
            )
            self._complete(uow, scope, reserved, 200, _membership_response(updated))
            uow.commit()
            return updated

    def _organization_visible(self, uow: UnitOfWork, actor: Actor, organization_id: UUID) -> bool:
        return any(
            membership.organization_id == organization_id and _active(membership.status)
            for membership in (
                *uow.organizations.list_for_user(actor.actor_id),
                *uow.projects.list_for_user(actor.actor_id),
            )
        )

    def _require_organization_admin(
        self, uow: UnitOfWork, actor: Actor, organization_id: UUID
    ) -> Organization:
        scope = TenantScope(organization_id)
        membership = uow.organizations.get_membership(scope, actor.actor_id)
        if membership is not None and _active(membership.status):
            organization = uow.organizations.get(scope)
            if organization is None:
                raise NotFound()
            return organization
        if self._organization_visible(uow, actor, organization_id):
            raise Forbidden()
        raise NotFound()

    def _visible_project(self, uow: UnitOfWork, actor: Actor, project_id: UUID) -> Project:
        for membership in uow.organizations.list_for_user(actor.actor_id):
            if not _active(membership.status):
                continue
            project = uow.projects.get(TenantScope(membership.organization_id, project_id))
            if project is not None:
                return project
        for membership in uow.projects.list_for_user(actor.actor_id):
            if membership.project_id != project_id or not _active(membership.status):
                continue
            project = uow.projects.get(TenantScope(membership.organization_id, project_id))
            if project is not None:
                return project
        raise NotFound()

    def _require_project_manager(self, uow: UnitOfWork, actor: Actor, project_id: UUID) -> Project:
        project = self._visible_project(uow, actor, project_id)
        organization_membership = uow.organizations.get_membership(
            TenantScope(project.organization_id), actor.actor_id
        )
        if organization_membership is not None and _active(organization_membership.status):
            return project
        membership = uow.projects.get_membership(project.scope, actor.actor_id)
        if membership is not None and _active(membership.status) and membership.role is Role.PROJECT_OWNER:
            return project
        raise Forbidden()

    def _reserve(
        self,
        uow: UnitOfWork,
        actor: Actor,
        scope: TenantScope,
        operation: str,
        idempotency_key: str,
        payload: dict[str, object],
    ) -> CommandRecord:
        command_id = uuid5(
            NAMESPACE_URL,
            f"peerassist:{scope.organization_id}:{scope.project_id}:{actor.actor_id}:{operation}:{idempotency_key}",
        )
        candidate = CommandRecord(
            command_id,
            scope.organization_id,
            actor.actor_id,
            operation,
            idempotency_key,
            canonical_json_digest(payload),
            self._clock(),
            project_id=scope.project_id,
        )
        reserved = uow.commands.reserve_or_replay(scope, candidate)
        if reserved.payload_digest != candidate.payload_digest:
            raise IdempotencyConflict()
        return reserved

    def _complete(
        self,
        uow: UnitOfWork,
        scope: TenantScope,
        command: CommandRecord,
        status: int,
        body: dict[str, object],
    ) -> None:
        uow.commands.complete(
            scope,
            replace(command, response_status=status, response_body=body, completed_at=self._clock()),
        )

    def _audit(
        self,
        uow: UnitOfWork,
        actor: Actor,
        scope: TenantScope,
        command: CommandRecord,
        action: Action,
        resource_type: str,
        resource_id: UUID,
        request_id: str,
        metadata: dict[str, object],
    ) -> None:
        uow.audit.append(
            scope,
            AuditEvent(
                uuid5(command.id, "audit"),
                actor.actor_id,
                scope.organization_id,
                action,
                resource_type,
                resource_id,
                "succeeded",
                request_id,
                self._clock(),
                project_id=scope.project_id,
                identity_id=actor.identity_id,
                command_id=command.id,
                safe_metadata=metadata,
            ),
        )

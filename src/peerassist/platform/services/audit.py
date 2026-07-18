"""Authorized tenant audit queries with explicit safe filters."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from ..errors import Forbidden, NotFound
from ..models import Action, Actor, ActorKind, AuditEvent, Decision, TenantScope
from ..permissions import decide_permission
from ..ports import UnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class AuditFilter:
    project_id: UUID | None = None
    action: Action | None = None
    outcome: str | None = None
    actor_id: UUID | None = None
    resource_type: str | None = None


class AuditService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def list_events(
        self,
        actor: Actor,
        organization_id: UUID,
        filters: AuditFilter | None = None,
    ) -> tuple[AuditEvent, ...]:
        selected = filters or AuditFilter()
        if actor.kind is not ActorKind.USER:
            raise Forbidden()
        scope = TenantScope(organization_id)
        with self._uow_factory(actor) as uow:
            membership = uow.organizations.get_membership(scope, actor.actor_id)
            if membership is None or membership.status != "active":
                visible = any(
                    item.organization_id == organization_id and item.status == "active"
                    for item in uow.projects.list_for_user(actor.actor_id)
                )
                if visible:
                    raise Forbidden()
                raise NotFound()
            decision = decide_permission(
                role=membership.role,
                membership_scope=scope,
                resource_scope=scope,
                action=Action.ORGANIZATION_READ_AUDIT,
            )
            if decision is Decision.NOT_FOUND:
                raise NotFound()
            if decision is not Decision.ALLOW:
                raise Forbidden()
            if selected.project_id is not None:
                project_scope = TenantScope(organization_id, selected.project_id)
                if uow.projects.get(project_scope) is None:
                    raise NotFound()
            events = uow.audit.list_for_organization(scope)
            return tuple(
                event
                for event in events
                if (selected.project_id is None or event.project_id == selected.project_id)
                and (selected.action is None or event.action is selected.action)
                and (selected.outcome is None or event.outcome == selected.outcome)
                and (selected.actor_id is None or event.actor_id == selected.actor_id)
                and (selected.resource_type is None or event.resource_type == selected.resource_type)
            )

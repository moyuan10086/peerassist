"""Explicit role/action policy with tenant-safe visibility decisions."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from .models import Action, Actor, ActorKind, Decision, InternalServiceGrant, Role, TenantScope

_PROJECT_READ_ACTIONS = {
    Action.PROJECT_READ,
    Action.PAPER_READ,
    Action.REVIEW_JOB_READ,
    Action.REVIEW_EVENT_READ,
    Action.ARTIFACT_READ,
    Action.LEGACY_READ,
}

_PROJECT_OWNER_ACTIONS = _PROJECT_READ_ACTIONS | {
    Action.PROJECT_MANAGE_SETTINGS,
    Action.PROJECT_MANAGE_MEMBERS,
    Action.PAPER_UPLOAD,
    Action.PAPER_ADD_VERSION,
    Action.REVIEW_JOB_CREATE,
    Action.REVIEW_JOB_CANCEL,
    Action.REVIEW_JOB_RETRY,
    Action.CONCERN_DECIDE,
    Action.REPORT_DRAFT,
    Action.REPORT_FINALIZE,
    Action.RETENTION_MANAGE,
    Action.PUBLICATION_GRANT,
    Action.PROJECT_MEMBERSHIP_GRANT,
    Action.PROJECT_MEMBERSHIP_CHANGE,
    Action.PROJECT_MEMBERSHIP_REVOKE,
}

_ROLE_ACTIONS: dict[Role, frozenset[Action]] = {
    Role.ORGANIZATION_ADMIN: frozenset(
        _PROJECT_OWNER_ACTIONS
        | {
            Action.ORGANIZATION_READ,
            Action.ORGANIZATION_MANAGE_POLICY,
            Action.ORGANIZATION_MANAGE_MEMBERS,
            Action.ORGANIZATION_READ_AUDIT,
            Action.PROJECT_CREATE,
            Action.ORGANIZATION_MEMBERSHIP_GRANT,
            Action.ORGANIZATION_MEMBERSHIP_CHANGE,
            Action.ORGANIZATION_MEMBERSHIP_REVOKE,
        }
    ),
    Role.PROJECT_OWNER: frozenset(_PROJECT_OWNER_ACTIONS),
    Role.REVIEWER: frozenset(
        _PROJECT_READ_ACTIONS
        | {
            Action.PAPER_UPLOAD,
            Action.PAPER_ADD_VERSION,
            Action.REVIEW_JOB_CREATE,
            Action.REVIEW_JOB_CANCEL,
            Action.REVIEW_JOB_RETRY,
            Action.CONCERN_DECIDE,
            Action.REPORT_DRAFT,
        }
    ),
    Role.VIEWER: frozenset(_PROJECT_READ_ACTIONS),
    Role.SERVICE_AGENT: frozenset(),
}

_INTERNAL_ACTIONS = frozenset(
    {Action.INTERNAL_TOOL_EXECUTE, Action.INTERNAL_WORK_CLAIM, Action.INTERNAL_WORK_COMPLETE}
)


class ActionScope(StrEnum):
    ORGANIZATION = "organization"
    PROJECT = "project"


_ACTION_SCOPE: dict[Action, ActionScope] = {
    Action.ORGANIZATION_READ: ActionScope.ORGANIZATION,
    Action.ORGANIZATION_MANAGE_POLICY: ActionScope.ORGANIZATION,
    Action.ORGANIZATION_MANAGE_MEMBERS: ActionScope.ORGANIZATION,
    Action.ORGANIZATION_READ_AUDIT: ActionScope.ORGANIZATION,
    Action.PROJECT_CREATE: ActionScope.ORGANIZATION,
    Action.ORGANIZATION_MEMBERSHIP_GRANT: ActionScope.ORGANIZATION,
    Action.ORGANIZATION_MEMBERSHIP_CHANGE: ActionScope.ORGANIZATION,
    Action.ORGANIZATION_MEMBERSHIP_REVOKE: ActionScope.ORGANIZATION,
    Action.PROJECT_READ: ActionScope.PROJECT,
    Action.PROJECT_MANAGE_SETTINGS: ActionScope.PROJECT,
    Action.PROJECT_MANAGE_MEMBERS: ActionScope.PROJECT,
    Action.PROJECT_MEMBERSHIP_GRANT: ActionScope.PROJECT,
    Action.PROJECT_MEMBERSHIP_CHANGE: ActionScope.PROJECT,
    Action.PROJECT_MEMBERSHIP_REVOKE: ActionScope.PROJECT,
    Action.PAPER_READ: ActionScope.PROJECT,
    Action.PAPER_UPLOAD: ActionScope.PROJECT,
    Action.PAPER_ADD_VERSION: ActionScope.PROJECT,
    Action.REVIEW_JOB_READ: ActionScope.PROJECT,
    Action.REVIEW_JOB_CREATE: ActionScope.PROJECT,
    Action.REVIEW_JOB_CANCEL: ActionScope.PROJECT,
    Action.REVIEW_JOB_RETRY: ActionScope.PROJECT,
    Action.REVIEW_EVENT_READ: ActionScope.PROJECT,
    Action.ARTIFACT_READ: ActionScope.PROJECT,
    Action.CONCERN_DECIDE: ActionScope.PROJECT,
    Action.REPORT_DRAFT: ActionScope.PROJECT,
    Action.REPORT_FINALIZE: ActionScope.PROJECT,
    Action.RETENTION_MANAGE: ActionScope.PROJECT,
    Action.PUBLICATION_GRANT: ActionScope.PROJECT,
    Action.LEGACY_READ: ActionScope.PROJECT,
    Action.INTERNAL_TOOL_EXECUTE: ActionScope.PROJECT,
    Action.INTERNAL_WORK_CLAIM: ActionScope.PROJECT,
    Action.INTERNAL_WORK_COMPLETE: ActionScope.PROJECT,
}


def action_scope(action: Action) -> ActionScope | None:
    """Return the explicit resource scope classification; unknown actions stay denied."""
    return _ACTION_SCOPE.get(action)


def _scope_is_compatible(action: Action, resource_scope: TenantScope) -> bool:
    required = action_scope(action)
    if required is None:
        return False
    if required is ActionScope.ORGANIZATION:
        return resource_scope.project_id is None
    return resource_scope.project_id is not None


def decide_permission(
    *,
    role: Role,
    membership_scope: TenantScope,
    resource_scope: TenantScope,
    action: Action,
    actor: Actor | None = None,
    grant: InternalServiceGrant | None = None,
    audience: str | None = None,
    now: datetime | None = None,
) -> Decision:
    """Decide visibility before permissions so tenant existence is never leaked."""
    if membership_scope.organization_id != resource_scope.organization_id:
        return Decision.NOT_FOUND
    if not _scope_is_compatible(action, resource_scope):
        return Decision.FORBIDDEN
    if not membership_scope.contains(resource_scope):
        return Decision.NOT_FOUND
    if role is Role.SERVICE_AGENT:
        if (
            actor is None
            or actor.kind is not ActorKind.SERVICE
            or grant is None
            or audience is None
            or now is None
            or now.tzinfo is None
            or now.utcoffset() is None
            or now.utcoffset().total_seconds() != 0
            or actor.actor_id != grant.service_actor_id
            or audience != grant.audience
            or grant.scope != resource_scope
            or action not in _INTERNAL_ACTIONS
            or action not in grant.actions
            or now < grant.issued_at
            or now >= grant.expires_at
        ):
            return Decision.FORBIDDEN
        return Decision.ALLOW
    if action in _INTERNAL_ACTIONS:
        return Decision.FORBIDDEN
    return Decision.ALLOW if action in _ROLE_ACTIONS[role] else Decision.FORBIDDEN


def allowed_actions(role: Role) -> frozenset[Action]:
    """Expose the static non-service policy for diagnostics and contract tests."""
    return _ROLE_ACTIONS[role]

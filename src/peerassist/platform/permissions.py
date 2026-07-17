"""Explicit role/action policy with tenant-safe visibility decisions."""

from __future__ import annotations

from .models import Action, Decision, Role, TenantScope

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
}

_ROLE_ACTIONS: dict[Role, frozenset[Action]] = {
    Role.ORGANIZATION_ADMIN: frozenset(
        _PROJECT_OWNER_ACTIONS
        | {
            Action.ORGANIZATION_READ,
            Action.ORGANIZATION_MANAGE_POLICY,
            Action.ORGANIZATION_MANAGE_MEMBERS,
            Action.ORGANIZATION_READ_AUDIT,
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
            Action.PUBLICATION_GRANT,
        }
    ),
    Role.VIEWER: frozenset(_PROJECT_READ_ACTIONS),
    Role.SERVICE_AGENT: frozenset(),
}

_INTERNAL_ACTIONS = frozenset(
    {Action.INTERNAL_TOOL_EXECUTE, Action.INTERNAL_WORK_CLAIM, Action.INTERNAL_WORK_COMPLETE}
)
_ORGANIZATION_ACTIONS = frozenset(
    {
        Action.ORGANIZATION_READ,
        Action.ORGANIZATION_MANAGE_POLICY,
        Action.ORGANIZATION_MANAGE_MEMBERS,
        Action.ORGANIZATION_READ_AUDIT,
    }
)


def decide_permission(
    *,
    role: Role,
    membership_scope: TenantScope,
    resource_scope: TenantScope,
    action: Action,
    internal_grant: frozenset[Action] = frozenset(),
) -> Decision:
    """Decide visibility before permissions so tenant existence is never leaked."""
    if not membership_scope.contains(resource_scope):
        return Decision.NOT_FOUND
    if role is Role.SERVICE_AGENT:
        return Decision.ALLOW if action in _INTERNAL_ACTIONS and action in internal_grant else Decision.FORBIDDEN
    if action in _INTERNAL_ACTIONS:
        return Decision.FORBIDDEN
    return Decision.ALLOW if action in _ROLE_ACTIONS[role] else Decision.FORBIDDEN


def allowed_actions(role: Role) -> frozenset[Action]:
    """Expose the static non-service policy for diagnostics and contract tests."""
    return _ROLE_ACTIONS[role]

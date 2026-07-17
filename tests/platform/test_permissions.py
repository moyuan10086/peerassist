from uuid import uuid4

import pytest

from peerassist.platform.models import Action, Decision, Role, TenantScope
from peerassist.platform.permissions import decide_permission

ORG_ID = uuid4()
PROJECT_ID = uuid4()
OTHER_ORG_ID = uuid4()
OTHER_PROJECT_ID = uuid4()
ORG_SCOPE = TenantScope(organization_id=ORG_ID)
PROJECT_SCOPE = TenantScope(organization_id=ORG_ID, project_id=PROJECT_ID)


@pytest.mark.parametrize(
    ("role", "membership_scope", "allowed", "denied"),
    [
        (
            Role.ORGANIZATION_ADMIN,
            ORG_SCOPE,
            {Action.ORGANIZATION_MANAGE_MEMBERS, Action.PROJECT_MANAGE_MEMBERS, Action.REPORT_FINALIZE},
            {Action.INTERNAL_TOOL_EXECUTE},
        ),
        (
            Role.PROJECT_OWNER,
            PROJECT_SCOPE,
            {Action.PROJECT_MANAGE_SETTINGS, Action.PROJECT_MANAGE_MEMBERS, Action.REPORT_FINALIZE},
            {
                Action.ORGANIZATION_READ,
                Action.ORGANIZATION_MANAGE_MEMBERS,
                Action.INTERNAL_TOOL_EXECUTE,
            },
        ),
        (
            Role.REVIEWER,
            PROJECT_SCOPE,
            {Action.PAPER_UPLOAD, Action.REVIEW_JOB_CREATE, Action.CONCERN_DECIDE, Action.REPORT_DRAFT},
            {Action.PROJECT_MANAGE_MEMBERS, Action.REPORT_FINALIZE},
        ),
        (
            Role.VIEWER,
            PROJECT_SCOPE,
            {Action.PROJECT_READ, Action.PAPER_READ, Action.REVIEW_JOB_READ, Action.ARTIFACT_READ},
            {Action.PAPER_UPLOAD, Action.REVIEW_JOB_CREATE, Action.CONCERN_DECIDE},
        ),
        (
            Role.SERVICE_AGENT,
            PROJECT_SCOPE,
            {Action.INTERNAL_TOOL_EXECUTE},
            {Action.PROJECT_READ, Action.PAPER_UPLOAD, Action.REVIEW_JOB_CREATE},
        ),
    ],
)
def test_roles_use_an_explicit_action_policy(
    role: Role,
    membership_scope: TenantScope,
    allowed: set[Action],
    denied: set[Action],
) -> None:
    for action in allowed:
        decision = decide_permission(
            role=role,
            membership_scope=membership_scope,
            resource_scope=PROJECT_SCOPE,
            action=action,
            internal_grant=frozenset({Action.INTERNAL_TOOL_EXECUTE}),
        )
        assert decision is Decision.ALLOW, (role, action)
    for action in denied:
        decision = decide_permission(
            role=role,
            membership_scope=membership_scope,
            resource_scope=PROJECT_SCOPE,
            action=action,
            internal_grant=frozenset({Action.INTERNAL_TOOL_EXECUTE}),
        )
        assert decision is Decision.FORBIDDEN, (role, action)


def test_service_agent_requires_a_short_lived_internal_grant() -> None:
    assert (
        decide_permission(
            role=Role.SERVICE_AGENT,
            membership_scope=PROJECT_SCOPE,
            resource_scope=PROJECT_SCOPE,
            action=Action.INTERNAL_TOOL_EXECUTE,
        )
        is Decision.FORBIDDEN
    )


def test_cross_tenant_resources_are_hidden_before_action_policy_is_checked() -> None:
    assert (
        decide_permission(
            role=Role.ORGANIZATION_ADMIN,
            membership_scope=ORG_SCOPE,
            resource_scope=TenantScope(organization_id=OTHER_ORG_ID, project_id=OTHER_PROJECT_ID),
            action=Action.PROJECT_READ,
        )
        is Decision.NOT_FOUND
    )
    assert (
        decide_permission(
            role=Role.PROJECT_OWNER,
            membership_scope=PROJECT_SCOPE,
            resource_scope=TenantScope(organization_id=ORG_ID, project_id=OTHER_PROJECT_ID),
            action=Action.PROJECT_READ,
        )
        is Decision.NOT_FOUND
    )


def test_visible_resource_without_action_permission_is_forbidden() -> None:
    assert (
        decide_permission(
            role=Role.VIEWER,
            membership_scope=PROJECT_SCOPE,
            resource_scope=PROJECT_SCOPE,
            action=Action.PAPER_UPLOAD,
        )
        is Decision.FORBIDDEN
    )


def test_organization_actions_require_organization_scope() -> None:
    assert (
        decide_permission(
            role=Role.PROJECT_OWNER,
            membership_scope=PROJECT_SCOPE,
            resource_scope=ORG_SCOPE,
            action=Action.ORGANIZATION_READ,
        )
        is Decision.NOT_FOUND
    )

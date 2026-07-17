from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from peerassist.platform.models import (
    Action,
    Actor,
    ActorKind,
    Decision,
    InternalServiceGrant,
    Role,
    TenantScope,
)
from peerassist.platform.permissions import decide_permission

ORG_ID = uuid4()
PROJECT_ID = uuid4()
OTHER_ORG_ID = uuid4()
OTHER_PROJECT_ID = uuid4()
ORG_SCOPE = TenantScope(organization_id=ORG_ID)
PROJECT_SCOPE = TenantScope(organization_id=ORG_ID, project_id=PROJECT_ID)
NOW = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)
SERVICE_ACTOR = Actor(actor_id=uuid4(), kind=ActorKind.SERVICE)


def service_grant(
    *,
    actor_id: UUID = SERVICE_ACTOR.actor_id,
    scope: TenantScope = PROJECT_SCOPE,
    audience: str = "peerassist-worker",
    actions: frozenset[Action] = frozenset({Action.INTERNAL_TOOL_EXECUTE}),
    issued_at: datetime = NOW - timedelta(minutes=1),
    expires_at: datetime = NOW + timedelta(minutes=4),
) -> InternalServiceGrant:
    return InternalServiceGrant(
        service_actor_id=actor_id,
        scope=scope,
        audience=audience,
        actions=actions,
        issued_at=issued_at,
        expires_at=expires_at,
    )


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
            {Action.PROJECT_MANAGE_MEMBERS, Action.REPORT_FINALIZE, Action.PUBLICATION_GRANT},
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
        grant = service_grant() if role is Role.SERVICE_AGENT else None
        decision = decide_permission(
            role=role,
            membership_scope=membership_scope,
            resource_scope=PROJECT_SCOPE,
            action=action,
            actor=SERVICE_ACTOR if grant else None,
            grant=grant,
            audience="peerassist-worker" if grant else None,
            now=NOW if grant else None,
        )
        assert decision is Decision.ALLOW, (role, action)
    for action in denied:
        decision = decide_permission(
            role=role,
            membership_scope=membership_scope,
            resource_scope=PROJECT_SCOPE,
            action=action,
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


def test_only_project_owner_or_organization_admin_may_pass_publication_gate() -> None:
    assert (
        decide_permission(
            role=Role.PROJECT_OWNER,
            membership_scope=PROJECT_SCOPE,
            resource_scope=PROJECT_SCOPE,
            action=Action.PUBLICATION_GRANT,
        )
        is Decision.ALLOW
    )
    assert (
        decide_permission(
            role=Role.ORGANIZATION_ADMIN,
            membership_scope=ORG_SCOPE,
            resource_scope=PROJECT_SCOPE,
            action=Action.PUBLICATION_GRANT,
        )
        is Decision.ALLOW
    )
    assert (
        decide_permission(
            role=Role.REVIEWER,
            membership_scope=PROJECT_SCOPE,
            resource_scope=PROJECT_SCOPE,
            action=Action.PUBLICATION_GRANT,
        )
        is Decision.FORBIDDEN
    )


@pytest.mark.parametrize(
    ("actor", "grant", "audience", "now"),
    [
        (Actor(uuid4(), ActorKind.USER), service_grant(), "peerassist-worker", NOW),
        (Actor(uuid4(), ActorKind.SERVICE), service_grant(), "peerassist-worker", NOW),
        (
            SERVICE_ACTOR,
            service_grant(scope=TenantScope(OTHER_ORG_ID, OTHER_PROJECT_ID)),
            "peerassist-worker",
            NOW,
        ),
        (SERVICE_ACTOR, service_grant(scope=ORG_SCOPE), "peerassist-worker", NOW),
        (SERVICE_ACTOR, service_grant(scope=TenantScope(ORG_ID, OTHER_PROJECT_ID)), "peerassist-worker", NOW),
        (SERVICE_ACTOR, service_grant(audience="peerassist-legacy"), "peerassist-worker", NOW),
        (
            SERVICE_ACTOR,
            service_grant(actions=frozenset({Action.INTERNAL_WORK_CLAIM})),
            "peerassist-worker",
            NOW,
        ),
        (SERVICE_ACTOR, service_grant(), "peerassist-worker", NOW + timedelta(minutes=5)),
        (SERVICE_ACTOR, service_grant(), "peerassist-worker", NOW - timedelta(minutes=2)),
    ],
)
def test_service_agent_denies_invalid_internal_grants(
    actor: Actor,
    grant: InternalServiceGrant,
    audience: str,
    now: datetime,
) -> None:
    assert (
        decide_permission(
            role=Role.SERVICE_AGENT,
            membership_scope=PROJECT_SCOPE,
            resource_scope=PROJECT_SCOPE,
            action=Action.INTERNAL_TOOL_EXECUTE,
            actor=actor,
            grant=grant,
            audience=audience,
            now=now,
        )
        is Decision.FORBIDDEN
    )


def test_service_agent_allows_only_a_valid_bound_internal_grant() -> None:
    assert (
        decide_permission(
            role=Role.SERVICE_AGENT,
            membership_scope=PROJECT_SCOPE,
            resource_scope=PROJECT_SCOPE,
            action=Action.INTERNAL_TOOL_EXECUTE,
            actor=SERVICE_ACTOR,
            grant=service_grant(),
            audience="peerassist-worker",
            now=NOW,
        )
        is Decision.ALLOW
    )


def test_internal_grant_does_not_elevate_a_non_service_agent_role() -> None:
    assert (
        decide_permission(
            role=Role.VIEWER,
            membership_scope=PROJECT_SCOPE,
            resource_scope=PROJECT_SCOPE,
            action=Action.INTERNAL_TOOL_EXECUTE,
            actor=SERVICE_ACTOR,
            grant=service_grant(),
            audience="peerassist-worker",
            now=NOW,
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

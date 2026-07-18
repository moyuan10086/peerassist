from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from peerassist.platform.adapters.memory import MemoryUnitOfWorkFactory
from peerassist.platform.cli import run_cli
from peerassist.platform.errors import IdempotencyConflict
from peerassist.platform.models import (
    Actor,
    ActorKind,
    BrowserSession,
    ExternalIdentity,
    TenantScope,
    User,
)
from peerassist.platform.services.bootstrap import (
    BootstrapOrganization,
    BootstrapOrganizationService,
    ChangeIdentity,
    IdentityOperatorService,
)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def _seed_identity(factory: MemoryUnitOfWorkFactory, clock: Clock) -> tuple[User, ExternalIdentity]:
    user = User(uuid4(), "active", "Bootstrap Admin", clock(), clock())
    identity = ExternalIdentity(
        uuid4(),
        user.id,
        "https://identity.example/realms/peerassist",
        "bootstrap-admin",
        {"sub": "bootstrap-admin", "email": "admin@example.test"},
        clock(),
        clock(),
    )
    with factory(Actor(uuid4(), ActorKind.OPERATOR)) as uow:
        uow.users.add(user)
        uow.users.add_identity(identity)
        uow.commit()
    return user, identity


def test_concurrent_operator_bootstrap_converges_and_changed_payload_conflicts() -> None:
    clock = Clock()
    factory = MemoryUnitOfWorkFactory(clock=clock)
    user, identity = _seed_identity(factory, clock)
    operator = Actor(uuid4(), ActorKind.OPERATOR)
    service = BootstrapOrganizationService(factory, clock=clock)
    command = BootstrapOrganization(
        issuer=identity.issuer,
        subject=identity.subject,
        slug="first-org",
        name="First Organization",
        idempotency_key="bootstrap-001",
        request_id="req-bootstrap",
    )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = tuple(pool.map(lambda _: service.execute(operator, command), range(8)))

    assert len({result.organization.id for result in results}) == 1
    assert len({result.membership.id for result in results}) == 1
    organization = results[0].organization
    with factory(operator) as uow:
        scope = TenantScope(organization.id)
        assert uow.organizations.get(scope) == organization
        assert uow.organizations.get_membership(scope, user.id) == results[0].membership
        assert len(tuple(uow.audit.list(scope))) == 1

    with pytest.raises(IdempotencyConflict):
        service.execute(
            operator,
            BootstrapOrganization(
                issuer=identity.issuer,
                subject=identity.subject,
                slug="first-org",
                name="Changed Name",
                idempotency_key=command.idempotency_key,
                request_id="req-bootstrap-changed",
            ),
        )


def test_identity_disable_and_unlink_are_versioned_and_revoke_sessions() -> None:
    clock = Clock()
    factory = MemoryUnitOfWorkFactory(clock=clock)
    user, identity = _seed_identity(factory, clock)
    operator = Actor(uuid4(), ActorKind.OPERATOR)
    bootstrap = BootstrapOrganizationService(factory, clock=clock).execute(
        operator,
        BootstrapOrganization(
            identity.issuer,
            identity.subject,
            "identity-audit",
            "Identity Audit",
            "bootstrap-identity-audit",
            "req-bootstrap-identity-audit",
        ),
    )
    session = BrowserSession(
        uuid4(),
        user.id,
        identity.id,
        "a" * 64,
        "b" * 64,
        "provider-reference",
        clock() + timedelta(hours=1),
        clock() + timedelta(minutes=30),
        clock(),
    )
    with factory(operator) as uow:
        uow.browser_sessions.save(session)
        uow.commit()

    service = IdentityOperatorService(factory, clock=clock)
    disabled = service.disable(
        operator,
        ChangeIdentity(
            identity.issuer,
            identity.subject,
            expected_version=1,
            idempotency_key="disable-identity",
            request_id="req-disable",
            audit_organization_id=bootstrap.organization.id,
        ),
    )
    assert disabled.version == 2
    assert disabled.disabled_at == clock()
    with factory(operator) as uow:
        assert uow.browser_sessions.get_by_digest(session.session_digest).revoked_at == clock()

    clock.now += timedelta(seconds=1)
    unlinked = service.unlink(
        operator,
        ChangeIdentity(
            identity.issuer,
            identity.subject,
            expected_version=2,
            idempotency_key="unlink-identity",
            request_id="req-unlink",
            audit_organization_id=bootstrap.organization.id,
        ),
    )
    assert unlinked.version == 3
    assert dict(unlinked.verified_claims) == {}
    assert unlinked.disabled_at is not None


def test_cli_never_exposes_protected_inputs_or_persists_them_in_audit() -> None:
    clock = Clock()
    factory = MemoryUnitOfWorkFactory(clock=clock)
    _, identity = _seed_identity(factory, clock)
    operator_id = uuid4()
    stdout = io.StringIO()
    stderr = io.StringIO()
    protected = {
        "PEERASSIST_OPERATOR_ID": str(operator_id),
        "PEERASSIST_OPERATOR_PASSWORD": "password-do-not-print",
        "PEERASSIST_OPERATOR_CLIENT_SECRET": "client-secret-do-not-print",
        "PEERASSIST_OPERATOR_RAW_TOKEN": "raw-token-do-not-print",
        "PEERASSIST_PRIVATE_ROOT": "/private/customer/manuscripts",
    }

    status = run_cli(
        [
            "bootstrap-organization",
            "--issuer",
            identity.issuer,
            "--subject",
            identity.subject,
            "--slug",
            "cli-org",
            "--name",
            "CLI Organization",
            "--idempotency-key",
            "cli-bootstrap",
        ],
        uow_factory=factory,
        clock=clock,
        environ=protected,
        stdin=io.StringIO("stdin-password-do-not-print\n"),
        stdout=stdout,
        stderr=stderr,
    )

    assert status == 0
    rendered = stdout.getvalue() + stderr.getvalue()
    for secret in (*protected.values(), "stdin-password-do-not-print"):
        if secret != str(operator_id):
            assert secret not in rendered
    organization_id = __import__("json").loads(stdout.getvalue())["organization_id"]
    with factory(Actor(operator_id, ActorKind.OPERATOR)) as uow:
        audit_text = repr(tuple(uow.audit.list(TenantScope(__import__("uuid").UUID(organization_id)))))
    for secret in (*protected.values(), "stdin-password-do-not-print"):
        if secret != str(operator_id):
            assert secret not in audit_text

    failed_stdout = io.StringIO()
    failed_stderr = io.StringIO()
    failed = run_cli(
        [
            "bootstrap-organization",
            "--issuer",
            identity.issuer,
            "--subject",
            identity.subject,
            "--slug",
            "cli-org",
            "--name",
            "Changed CLI Organization",
            "--idempotency-key",
            "cli-bootstrap",
        ],
        uow_factory=factory,
        clock=clock,
        environ=protected,
        stdin=io.StringIO("stdin-password-do-not-print\n"),
        stdout=failed_stdout,
        stderr=failed_stderr,
    )
    assert failed == 1
    assert failed_stdout.getvalue() == ""
    assert __import__("json").loads(failed_stderr.getvalue()) == {"error": {"code": "idempotency_conflict"}}
    for secret in (*protected.values(), "stdin-password-do-not-print"):
        if secret != str(operator_id):
            assert secret not in failed_stderr.getvalue()

    rejected_stdout = io.StringIO()
    rejected_stderr = io.StringIO()
    rejected = run_cli(
        [
            "bootstrap-organization",
            "--issuer",
            identity.issuer,
            "--subject",
            identity.subject,
            "--slug",
            "rejected-org",
            "--name",
            "Rejected Organization",
            "--idempotency-key",
            "rejected-secret-argument",
            "--password",
            "password-do-not-print",
        ],
        uow_factory=factory,
        clock=clock,
        environ=protected,
        stdin=io.StringIO(),
        stdout=rejected_stdout,
        stderr=rejected_stderr,
    )
    assert rejected == 2
    assert rejected_stdout.getvalue() == ""
    assert "password-do-not-print" not in rejected_stderr.getvalue()

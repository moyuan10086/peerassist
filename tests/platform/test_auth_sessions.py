from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from peerassist.platform.adapters.memory import FakeIdentityProvider, MemoryUnitOfWorkFactory
from peerassist.platform.errors import AuthenticationRequired
from peerassist.platform.services.sessions import BrowserSessionService
from peerassist.platform.models import Actor, ActorKind


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def test_pkce_login_provisions_identity_resolves_actor_and_logs_out() -> None:
    clock = Clock()
    factory = MemoryUnitOfWorkFactory(clock=clock)
    provider = FakeIdentityProvider(
        issuer="https://issuer.example",
        audience="peerassist-api",
        accepted_algorithms=frozenset({"RS256"}),
        clock=clock,
    )
    service = BrowserSessionService(
        factory,
        provider,
        key_ring={"key-1": bytes(range(32))},
        redirect_uri="https://peerassist.example/api/v1/auth/callback",
        clock=clock,
    )

    started = service.begin("/admin")
    code = provider.issue_callback(
        started.transaction_id,
        {
            "iss": "https://issuer.example",
            "sub": "admin-1",
            "aud": "peerassist-api",
            "alg": "RS256",
            "exp": int((clock.now + timedelta(minutes=5)).timestamp()),
            "nbf": int((clock.now - timedelta(seconds=1)).timestamp()),
            "email": "Admin@Example.com",
            "email_verified": True,
            "name": "Admin User",
        },
    )
    completed = service.complete(started.state, code)

    assert completed.return_path == "/admin"
    assert service.resolve_session(completed.session_token) == completed.actor
    assert service.resolve_session(
        completed.session_token,
        csrf_token=completed.csrf_token,
        require_csrf=True,
    ) == completed.actor
    assert completed.session_token not in repr(completed)
    assert completed.csrf_token not in repr(completed)

    with pytest.raises(AuthenticationRequired):
        service.complete(started.state, code)

    service.logout(completed.session_token, completed.csrf_token)
    with pytest.raises(AuthenticationRequired):
        service.resolve_session(completed.session_token)


def test_first_valid_bearer_provisions_the_trusted_external_identity() -> None:
    clock = Clock()
    factory = MemoryUnitOfWorkFactory(clock=clock)
    provider = FakeIdentityProvider(
        issuer="https://issuer.example",
        audience="peerassist-api",
        accepted_algorithms=frozenset({"RS256"}),
        clock=clock,
    )
    service = BrowserSessionService(
        factory,
        provider,
        key_ring={"key-1": bytes(range(32))},
        redirect_uri="https://peerassist.example/api/v1/auth/callback",
        clock=clock,
    )
    bearer = provider.issue(
        {
            "iss": provider.issuer,
            "sub": "bootstrap-admin",
            "aud": provider.audience,
            "alg": "RS256",
            "exp": int((clock.now + timedelta(minutes=5)).timestamp()),
            "email": "Admin@Example.com",
            "email_verified": True,
            "name": "Bootstrap Admin",
        }
    )

    actor = service.resolve_bearer(bearer)
    replay = service.resolve_bearer(bearer)

    assert actor == replay
    assert actor.kind is ActorKind.USER
    with factory(Actor(uuid4(), ActorKind.OPERATOR)) as uow:
        identity = uow.users.get_identity(provider.issuer, "bootstrap-admin")
        assert identity is not None and identity.user_id == actor.actor_id
        user = uow.users.get(actor.actor_id)
        assert user is not None and user.display_name == "Bootstrap Admin"


@pytest.mark.parametrize("return_path", ["https://evil.example", "//evil.example", "admin", "/../admin"])
def test_login_rejects_non_application_return_paths(return_path: str) -> None:
    clock = Clock()
    service = BrowserSessionService(
        MemoryUnitOfWorkFactory(clock=clock),
        FakeIdentityProvider(
            issuer="https://issuer.example",
            audience="peerassist-api",
            accepted_algorithms=frozenset({"RS256"}),
            clock=clock,
        ),
        key_ring={"key-1": bytes(range(32))},
        redirect_uri="https://peerassist.example/api/v1/auth/callback",
        clock=clock,
    )

    with pytest.raises(ValueError):
        service.begin(return_path)

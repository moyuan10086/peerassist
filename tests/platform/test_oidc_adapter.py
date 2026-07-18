from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from tests.platform.fixtures.oidc_server import FixtureIdentityProvider, OidcFixtureServer

from peerassist.platform.adapters.oidc import OidcIdentityProvider
from peerassist.platform.errors import AuthenticationRequired


class MutableClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock()


def _claims(clock, **overrides: object) -> dict[str, object]:
    claims: dict[str, object] = {
        "iss": "https://issuer.example",
        "sub": "user-1",
        "aud": "peerassist",
        "exp": int((clock() + timedelta(minutes=5)).timestamp()),
        "nbf": int((clock() - timedelta(seconds=1)).timestamp()),
        "email": " User@Example.COM ",
        "email_verified": True,
        "name": "  Ada   Lovelace  ",
    }
    claims.update(overrides)
    return claims


def _provider(clock, **overrides: object) -> FixtureIdentityProvider:
    values: dict[str, object] = {
        "issuer": "https://issuer.example",
        "audience": "peerassist",
        "accepted_algorithms": frozenset({"RS256"}),
        "clock": clock,
    }
    values.update(overrides)
    return FixtureIdentityProvider(**values)


@pytest.mark.parametrize(
    ("claims_override", "issue_override"),
    [
        ({"iss": "https://wrong.example"}, {}),
        ({"aud": "wrong"}, {}),
        ({"exp": 0}, {}),
        ({"nbf": 9_999_999_999}, {}),
        ({"sub": ""}, {}),
        ({}, {"algorithm": "HS256"}),
        ({}, {"typ": "not-an-access-token"}),
    ],
)
def test_rejects_invalid_standard_claims_and_headers(
    clock, claims_override: dict[str, object], issue_override: dict[str, object]
) -> None:
    provider = _provider(clock)
    token = provider.issue(_claims(clock, **claims_override), **issue_override)

    with pytest.raises(AuthenticationRequired) as captured:
        provider.validate_bearer(token)

    assert captured.value.code == "authentication_required"
    assert captured.value.__cause__ is None
    assert token not in str(captured.value)
    assert token not in repr(captured.value)


def test_rejects_unsigned_tokens(clock) -> None:
    provider = _provider(clock)
    token = jwt.encode(_claims(clock), key="", algorithm="none", headers={"typ": "JWT"})

    with pytest.raises(AuthenticationRequired):
        provider.validate_bearer(token)


def test_unknown_kid_refreshes_jwks_only_once(clock) -> None:
    provider = _provider(clock)
    provider.issue_and_validate(_claims(clock))
    unpublished_kid = provider.server.generate_key(publish=False)
    token = provider.issue(_claims(clock), kid=unpublished_kid)
    requests_before = provider.server.jwks_requests

    with pytest.raises(AuthenticationRequired):
        provider.validate_bearer(token)

    assert provider.server.jwks_requests == requests_before + 1


def test_rotated_key_is_loaded_by_single_forced_refresh(clock) -> None:
    provider = _provider(clock)
    provider.issue_and_validate(_claims(clock))
    provider.server.rotate()
    requests_before = provider.server.jwks_requests

    identity = provider.issue_and_validate(_claims(clock))

    assert identity.subject == "user-1"
    assert provider.server.jwks_requests == requests_before + 1


def test_expired_cache_fails_closed_when_provider_is_unavailable(clock) -> None:
    provider = _provider(clock, cache_ttl_seconds=30)
    token = provider.issue(_claims(clock))
    provider.validate_bearer(token)
    provider.server.available = False
    clock.now += timedelta(seconds=31)

    with pytest.raises(AuthenticationRequired) as captured:
        provider.validate_bearer(token)

    assert captured.value.code == "authentication_required"


def test_valid_cache_does_not_require_provider_availability(clock) -> None:
    provider = _provider(clock, cache_ttl_seconds=30)
    token = provider.issue(_claims(clock))
    provider.validate_bearer(token)
    provider.server.available = False
    clock.now += timedelta(seconds=29)

    assert provider.validate_bearer(token).subject == "user-1"


def test_disabled_identity_and_unverified_email_are_not_returned(clock) -> None:
    provider = _provider(clock)
    identity = provider.issue_and_validate(
        _claims(clock, email="private@example.com", email_verified=False)
    )
    assert "email" not in identity.claims
    assert "email_verified" not in identity.claims

    disabled = _provider(
        clock, disabled_identities={("https://issuer.example", "user-1")}
    )
    with pytest.raises(AuthenticationRequired):
        disabled.issue_and_validate(_claims(clock))


def test_provider_errors_are_normalized_without_raw_token(clock) -> None:
    server = OidcFixtureServer()
    server.discovery_status = 503
    provider = _provider(clock, server=server)
    token = provider.issue(_claims(clock))

    with pytest.raises(AuthenticationRequired) as captured:
        provider.validate_bearer(token)

    assert captured.value.code == "authentication_required"
    assert captured.value.__cause__ is None
    assert token not in str(captured.value)
    assert token not in repr(captured.value)


def test_production_mode_requires_https_for_issuer_and_discovered_urls(clock) -> None:
    with pytest.raises(ValueError):
        OidcIdentityProvider(
            issuer="http://issuer.example",
            audience="peerassist",
            accepted_algorithms=frozenset({"RS256"}),
            require_https=True,
            clock=clock,
        )

    server = OidcFixtureServer()

    def insecure_discovery(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(
                200,
                request=request,
                json={
                    "issuer": server.issuer,
                    "jwks_uri": "http://issuer.example/jwks",
                    "authorization_endpoint": f"{server.issuer}/authorize",
                },
            )
        return httpx.Response(404, request=request)

    provider = OidcIdentityProvider(
        issuer=server.issuer,
        audience="peerassist",
        accepted_algorithms=frozenset({"RS256"}),
        require_https=True,
        clock=clock,
        http_client=httpx.Client(transport=httpx.MockTransport(insecure_discovery)),
    )
    with pytest.raises(AuthenticationRequired):
        provider.validate_bearer(server.issue(_claims(clock)))


def test_configuration_bounds_timeout_cache_and_algorithm_allowlist(clock) -> None:
    common = {
        "issuer": "https://issuer.example",
        "audience": "peerassist",
        "clock": clock,
    }
    with pytest.raises(ValueError):
        OidcIdentityProvider(**common, accepted_algorithms=frozenset({"HS256"}))
    with pytest.raises(ValueError):
        OidcIdentityProvider(
            **common, accepted_algorithms=frozenset({"RS256"}), http_timeout_seconds=0
        )
    with pytest.raises(ValueError):
        OidcIdentityProvider(
            **common, accepted_algorithms=frozenset({"RS256"}), cache_ttl_seconds=86_401
        )

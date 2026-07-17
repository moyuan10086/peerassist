from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from peerassist.platform.errors import AuthenticationRequired


def provider(identity_provider_factory, clock, **overrides):
    settings = {
        "issuer": "https://issuer.example",
        "audience": "peerassist",
        "accepted_algorithms": frozenset({"RS256"}),
    }
    settings.update(overrides)
    return identity_provider_factory(**settings)


def claims(clock, **overrides):
    value = {
        "iss": "https://issuer.example",
        "sub": "user-1",
        "aud": "peerassist",
        "exp": int((clock() + timedelta(minutes=5)).timestamp()),
        "nbf": int((clock() - timedelta(seconds=1)).timestamp()),
        "alg": "RS256",
        "email": "User@Example.COM",
        "email_verified": True,
        "name": "  Ada   Lovelace  ",
    }
    value.update(overrides)
    return value


def test_validated_identity_contains_only_normalized_verified_claims(
    identity_provider_factory, clock
) -> None:
    identity = provider(identity_provider_factory, clock).issue_and_validate(claims(clock))
    assert identity.issuer == "https://issuer.example"
    assert identity.subject == "user-1"
    assert dict(identity.claims) == {
        "aud": "peerassist",
        "email": "user@example.com",
        "email_verified": True,
        "iss": "https://issuer.example",
        "name": "Ada Lovelace",
        "sub": "user-1",
    }


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"iss": "https://other.example"}, "issuer"),
        ({"sub": ""}, "subject"),
        ({"aud": "other"}, "audience"),
        ({"exp": 0}, "expired"),
        ({"nbf": 9_999_999_999}, "not active"),
        ({"alg": "none"}, "algorithm"),
    ],
)
def test_validation_requires_issuer_subject_audience_time_and_algorithm(
    identity_provider_factory,
    clock,
    change,
    expected,
) -> None:
    with pytest.raises(AuthenticationRequired) as caught:
        provider(identity_provider_factory, clock).issue_and_validate(claims(clock, **change))
    assert expected not in str(caught.value).casefold()
    assert caught.value.code == "authentication_required"


def test_disabled_external_identity_is_rejected(identity_provider_factory, clock) -> None:
    adapter = provider(
        identity_provider_factory, clock, disabled_identities={("https://issuer.example", "user-1")}
    )
    with pytest.raises(AuthenticationRequired):
        adapter.issue_and_validate(claims(clock))


def test_raw_token_is_never_retained_or_exposed(identity_provider_factory, clock) -> None:
    adapter = provider(identity_provider_factory, clock)
    token = adapter.issue(claims(clock))
    identity = adapter.validate_bearer(token)
    assert token not in repr(adapter)
    assert token not in repr(identity)
    assert token not in str(identity)
    assert all(token not in repr(value) for value in vars(adapter).values())
    with pytest.raises(AuthenticationRequired) as caught:
        adapter.validate_bearer("raw-secret-bearer")
    assert "raw-secret-bearer" not in str(caught.value)
    assert "raw-secret-bearer" not in repr(caught.value)


def test_authorization_and_callback_use_one_time_transaction(identity_provider_factory, clock) -> None:
    adapter = provider(identity_provider_factory, clock)
    transaction_id = uuid4()
    url = adapter.build_authorization_url(transaction_id)
    code = adapter.issue_callback(transaction_id, claims(clock))
    assert str(transaction_id) in url
    assert adapter.exchange_callback(transaction_id, code).subject == "user-1"
    with pytest.raises(AuthenticationRequired):
        adapter.exchange_callback(transaction_id, code)

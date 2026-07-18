from __future__ import annotations

import os

import httpx
import jwt
import pytest

from peerassist.platform.adapters.oidc import OidcIdentityProvider

pytestmark = pytest.mark.requires_docker


def test_keycloak_discovery_and_jwks_validate_a_real_access_token() -> None:
    issuer = os.environ.get("PEERASSIST_TEST_OIDC_ISSUER") or os.environ.get("M1_TEST_OIDC_ISSUER")
    if not issuer:
        pytest.skip("PEERASSIST_TEST_OIDC_ISSUER is not configured")
    client_id = os.environ["M1_TEST_OIDC_AUTOMATION_CLIENT_ID"]
    response = httpx.post(
        f"{issuer}/protocol/openid-connect/token",
        data={
            "client_id": client_id,
            "client_secret": os.environ["M1_TEST_OIDC_AUTOMATION_CLIENT_SECRET"],
            "grant_type": "password",
            "username": os.environ["M1_TEST_OIDC_USERNAME"],
            "password": os.environ["M1_TEST_OIDC_USER_PASSWORD"],
        },
        timeout=5,
    )
    response.raise_for_status()
    token = response.json()["access_token"]
    audience = jwt.decode(token, options={"verify_signature": False})["aud"]
    provider = OidcIdentityProvider(
        issuer=issuer,
        audience=audience,
        accepted_algorithms=frozenset({"RS256"}),
        require_https=False,
    )

    identity = provider.validate_bearer(token)

    assert identity.issuer == issuer
    assert identity.subject
    assert token not in repr(provider)
    assert token not in repr(identity)

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from services.api.app import create_app
from services.api.composition import PlatformDependencies

from common.config import PlatformSettings


def _settings() -> PlatformSettings:
    return PlatformSettings(
        environment="test",
        database_url="postgresql+psycopg://test:test@db/peerassist",
        oidc_issuer="http://identity.test/realms/peerassist",
        oidc_audience="peerassist-api",
        oidc_client_id="peerassist-browser",
        s3_endpoint="http://objects.test",
        s3_bucket="peerassist-test",
        public_base_url="http://testserver",
        allowed_origins=["http://testserver"],
        internal_legacy_audience="peerassist-legacy",
    )


def test_browser_login_installs_user_actor_for_management_routes() -> None:
    dependencies = PlatformDependencies.for_test()
    app = create_app(_settings(), dependencies)
    with TestClient(app) as client:
        login = client.get("/api/v1/auth/login", params={"return_path": "/admin"}, follow_redirects=False)
        assert login.status_code == 307
        query = parse_qs(urlsplit(login.headers["location"]).query)
        transaction_id = query["transaction_id"][0]
        state = query["state"][0]
        code = dependencies.identity_provider.issue_callback(
            UUID(transaction_id),
            {
                "iss": dependencies.identity_provider.issuer,
                "sub": "admin-1",
                "aud": dependencies.identity_provider.audience,
                "alg": "RS256",
                "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
                "email": "admin@example.com",
                "email_verified": True,
                "name": "Admin User",
            },
        )
        callback = client.get(
            "/api/v1/auth/callback",
            params={"state": state, "code": code},
            follow_redirects=False,
        )
        assert callback.status_code == 303
        assert callback.headers["location"] == "/admin"
        session = client.get("/api/v1/auth/session")
        organizations = client.get("/api/v1/organizations")
        organization_id = uuid4()
        missing_csrf = client.post(
            f"/api/v1/organizations/{organization_id}/projects",
            headers={"Idempotency-Key": "csrf-missing"},
            json={"name": "Rejected project"},
        )
        csrf_token = client.cookies.get("peerassist_csrf")
        accepted_csrf = client.post(
            f"/api/v1/organizations/{organization_id}/projects",
            headers={
                "Idempotency-Key": "csrf-present",
                "X-CSRF-Token": csrf_token,
            },
            json={"name": "Authorized request"},
        )

    assert session.status_code == 200
    assert session.json()["authenticated"] is True
    assert session.json()["user"]["display_name"] == "Admin User"
    assert organizations.status_code == 200
    assert organizations.json() == []
    assert missing_csrf.status_code == 401
    assert accepted_csrf.status_code == 404

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from starlette.exceptions import StarletteDeprecationWarning

with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message="Using `httpx` with `starlette.testclient` is deprecated.*",
        category=StarletteDeprecationWarning,
    )
    from starlette.testclient import TestClient

from common.config import PlatformSettings
from peerassist.platform.errors import DependencyUnavailable


def test_health_is_live_and_readiness_reports_safe_dependency_status() -> None:
    from services.api.app import create_app
    from services.api.composition import PlatformDependencies

    dependencies = PlatformDependencies.for_test(
        readiness_checks=(
            _Readiness("database", True),
            _Readiness("identity", False, RuntimeError("postgres://user:secret@private/db")),
        )
    )
    app = create_app(_settings(), dependencies)

    with TestClient(app, raise_server_exceptions=False) as client:
        health = client.get("/api/v1/health")
        ready = client.get("/api/v1/ready")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready.status_code == 503
    assert ready.json() == {
        "status": "unavailable",
        "dependencies": {"database": "ready", "identity": "unavailable"},
    }
    assert "secret" not in ready.text
    assert "private" not in ready.text


def test_request_id_is_propagated_or_generated_without_reflecting_invalid_input() -> None:
    from services.api.app import create_app
    from services.api.composition import PlatformDependencies

    client = TestClient(create_app(_settings(), PlatformDependencies.for_test()))

    supplied = client.get("/api/v1/health", headers={"X-Request-ID": "req-1"})
    generated = client.get("/api/v1/health")
    invalid = client.get("/api/v1/health", headers={"X-Request-ID": "token-private-value"})

    assert supplied.headers["X-Request-ID"] == "req-1"
    assert generated.headers["X-Request-ID"]
    assert generated.headers["X-Request-ID"] != "req-1"
    assert invalid.headers["X-Request-ID"] != "token-private-value"
    assert "token-private-value" not in invalid.text


def test_platform_and_unexpected_errors_have_safe_stable_envelopes() -> None:
    from services.api.app import create_app
    from services.api.composition import PlatformDependencies

    app = create_app(_settings(), PlatformDependencies.for_test())

    def platform_failure() -> None:
        raise DependencyUnavailable(cause=RuntimeError("raw provider body bearer-private-token"))

    def unexpected_failure() -> None:
        raise RuntimeError("/srv/private/paper.pdf bearer-private-token provider response")

    app.add_api_route("/api/v1/_test/platform-failure", platform_failure)
    app.add_api_route("/api/v1/_test/unexpected-failure", unexpected_failure)

    with TestClient(app, raise_server_exceptions=False) as client:
        platform = client.get(
            "/api/v1/_test/platform-failure", headers={"X-Request-ID": "req-platform"}
        )
        unexpected = client.get(
            "/api/v1/_test/unexpected-failure", headers={"X-Request-ID": "req-unexpected"}
        )

    assert platform.status_code == 503
    assert platform.json() == {
        "error": {
            "code": "dependency_unavailable",
            "message": "A required service is temporarily unavailable.",
            "request_id": "req-platform",
            "details": {},
            "retryable": True,
        }
    }
    assert unexpected.status_code == 500
    assert unexpected.json() == {
        "error": {
            "code": "platform_error",
            "message": "The operation could not be completed.",
            "request_id": "req-unexpected",
            "details": {},
            "retryable": False,
        }
    }
    combined = platform.text + unexpected.text
    assert "provider body" not in combined
    assert "private-token" not in combined
    assert "/srv/private" not in combined
    assert "traceback" not in combined.casefold()


def test_unknown_route_and_auth_placeholders_use_the_stable_error_envelope() -> None:
    from services.api.app import create_app
    from services.api.composition import PlatformDependencies

    with TestClient(create_app(_settings(), PlatformDependencies.for_test())) as client:
        missing = client.get("/api/v1/does-not-exist", headers={"X-Request-ID": "req-404"})
        auth_responses = (
            client.get("/api/v1/auth/login"),
            client.get("/api/v1/auth/callback"),
            client.get("/api/v1/auth/session"),
            client.post("/api/v1/auth/logout"),
            client.get("/api/v1/me"),
        )

    assert missing.status_code == 404
    assert missing.json() == {
        "error": {
            "code": "not_found",
            "message": "The requested resource was not found.",
            "request_id": "req-404",
            "details": {},
            "retryable": False,
        }
    }
    for response in auth_responses:
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "not_configured"
        assert response.json()["error"]["details"] == {}
        assert response.json()["error"]["retryable"] is False


def test_fixed_router_registry_and_lifespan_include_all_platform_components() -> None:
    from services.api.app import create_app
    from services.api.composition import PlatformDependencies
    from services.api.routes import platform_routers

    lifecycle = _Lifecycle()
    dependencies = PlatformDependencies.for_test(lifecycle_resources=(lifecycle,))
    app = create_app(_settings(), dependencies)

    with TestClient(app):
        assert lifecycle.started is True
        paths = set(app.openapi()["paths"])
        assert {route.prefix for route in platform_routers()} == {"/api/v1", "/api/v1"}
        assert "/api/v1/health" in paths
        assert "/api/v1/auth/login" in paths

    assert lifecycle.stopped is True


def test_untrusted_forwarded_and_browser_identity_headers_are_ignored() -> None:
    from services.api.app import create_app
    from services.api.composition import PlatformDependencies

    app = create_app(_settings(trusted_proxy_cidrs=["10.0.0.0/8"]), PlatformDependencies.for_test())

    def boundary(request: Request) -> dict[str, Any]:
        return {
            "scheme": request.url.scheme,
            "host": request.url.hostname,
            "identity": request.headers.get("x-user-id"),
            "tenant": request.headers.get("x-tenant-id"),
        }

    app.add_api_route("/api/v1/_test/boundary", boundary)
    headers = {
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "attacker.example",
        "X-User-ID": "browser-admin",
        "X-Tenant-ID": "other-tenant",
    }

    with TestClient(app, base_url="http://api.test", client=("192.0.2.5", 1234)) as untrusted:
        untrusted_view = untrusted.get("/api/v1/_test/boundary", headers=headers).json()
    with TestClient(app, base_url="http://api.test", client=("10.1.2.3", 1234)) as trusted:
        trusted_view = trusted.get("/api/v1/_test/boundary", headers=headers).json()

    assert untrusted_view == {
        "scheme": "http",
        "host": "api.test",
        "identity": None,
        "tenant": None,
    }
    assert trusted_view == {
        "scheme": "https",
        "host": "attacker.example",
        "identity": None,
        "tenant": None,
    }


def _settings(**overrides: object) -> PlatformSettings:
    values: dict[str, object] = {
        "environment": "test",
        "database_url": "postgresql+psycopg://test:test@db/peerassist",
        "oidc_issuer": "http://identity.test/realms/peerassist",
        "oidc_audience": "peerassist-api",
        "s3_endpoint": "http://objects.test",
        "s3_bucket": "peerassist-test",
        "public_base_url": "http://api.test",
        "allowed_origins": ["http://api.test"],
        "trusted_proxy_cidrs": [],
        "internal_legacy_audience": "peerassist-legacy",
    }
    values.update(overrides)
    return PlatformSettings(**values)


@dataclass(frozen=True)
class _Readiness:
    name: str
    available: bool
    failure: Exception | None = None

    async def check(self) -> bool:
        if self.failure is not None:
            raise self.failure
        return self.available


class _Lifecycle:
    started = False
    stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

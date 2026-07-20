from __future__ import annotations

import asyncio
import threading
import time
import warnings
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import HTTPException, Request
from starlette.exceptions import StarletteDeprecationWarning

with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message="Using `httpx` with `starlette.testclient` is deprecated.*",
        category=StarletteDeprecationWarning,
    )
    from starlette.testclient import TestClient

from common.config import PlatformSettings
from peerassist.platform.errors import AuthenticationRequired, DependencyUnavailable


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


def test_readiness_checks_run_concurrently_and_cancel_hung_checks_at_the_bound() -> None:
    from services.api.app import create_app
    from services.api.composition import PlatformDependencies

    first = _DelayedReadiness("database", 0.06)
    second = _DelayedReadiness("identity", 0.06)
    hung = _HungReadiness("object_store")
    dependencies = PlatformDependencies.for_test(
        readiness_checks=(first, second, hung),
        readiness_check_timeout_seconds=0.10,
        readiness_overall_timeout_seconds=0.12,
    )

    with TestClient(create_app(_settings(), dependencies)) as client:
        started = time.monotonic()
        response = client.get("/api/v1/ready")
        elapsed = time.monotonic() - started

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "dependencies": {
            "database": "ready",
            "identity": "ready",
            "object_store": "unavailable",
        },
    }
    assert elapsed < 0.15
    assert hung.cancelled is False
    assert any(thread.name == "readiness-object_store" for thread in threading.enumerate())


def test_readiness_isolates_unstoppable_probe_and_reuses_one_inflight_thread(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from services.api.app import create_app
    from services.api.composition import PlatformDependencies

    resistant = _UnstoppableReadiness("database")
    dependencies = PlatformDependencies.for_test(
        readiness_checks=(resistant,),
        readiness_check_timeout_seconds=0.03,
        readiness_overall_timeout_seconds=0.03,
    )
    app = create_app(_settings(), dependencies)
    assert len(app.state.readiness_probe_slots) == 1

    started = time.monotonic()
    with TestClient(app) as client:
        responses = [client.get("/api/v1/ready") for _ in range(3)]
    elapsed = time.monotonic() - started

    assert all(response.status_code == 503 for response in responses)
    assert all(
        response.json()["dependencies"] == {"database": "unavailable"}
        for response in responses
    )
    assert elapsed < 0.15
    assert resistant.calls == 1
    assert len(
        [thread for thread in threading.enumerate() if thread.name == "readiness-database"]
    ) == 1
    assert "Task exception was never retrieved" not in caplog.text


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


def test_browser_navigation_authentication_failure_returns_to_the_requested_page() -> None:
    from services.api.app import create_app
    from services.api.composition import PlatformDependencies

    app = create_app(_settings(), PlatformDependencies.for_test())

    def protected_file() -> None:
        raise AuthenticationRequired()

    app.add_api_route("/api/v1/_test/protected-file", protected_file)
    with TestClient(app, raise_server_exceptions=False) as client:
        browser = client.get(
            "/api/v1/_test/protected-file?disposition=inline",
            headers={"Accept": "text/html,application/xhtml+xml"},
            follow_redirects=False,
        )
        api = client.get(
            "/api/v1/_test/protected-file?disposition=inline",
            headers={"Accept": "application/json"},
        )

    assert browser.status_code == 303
    assert browser.headers["location"] == (
        "/api/v1/auth/login?return_path=%2Fapi%2Fv1%2F_test%2Fprotected-file"
        "%3Fdisposition%3Dinline"
    )
    assert api.status_code == 401
    assert api.json()["error"]["code"] == "authentication_required"


def test_http_exceptions_preserve_only_safe_standard_headers() -> None:
    from services.api.app import create_app
    from services.api.composition import PlatformDependencies

    app = create_app(_settings(), PlatformDependencies.for_test())

    def authentication_failure() -> None:
        raise HTTPException(
            401,
            detail="raw private provider detail",
            headers={"WWW-Authenticate": 'Bearer realm="peerassist"'},
        )

    def rate_limited() -> None:
        raise HTTPException(429, headers={"Retry-After": "30"})

    def unsafe_headers() -> None:
        raise HTTPException(
            401,
            headers={
                "WWW-Authenticate": "Bearer\r\nSet-Cookie: private=token",
                "Set-Cookie": "session=private-token",
                "Location": "https://attacker.example/private-token",
                "X-Provider-Body": "private-token",
            },
        )

    app.add_api_route("/api/v1/_test/authentication-failure", authentication_failure)
    app.add_api_route("/api/v1/_test/rate-limited", rate_limited)
    app.add_api_route("/api/v1/_test/unsafe-headers", unsafe_headers)

    with TestClient(app, raise_server_exceptions=False) as client:
        authentication = client.get("/api/v1/_test/authentication-failure")
        method_not_allowed = client.post("/api/v1/health")
        limited = client.get("/api/v1/_test/rate-limited")
        unsafe = client.get("/api/v1/_test/unsafe-headers")

    assert authentication.status_code == 401
    assert authentication.headers["WWW-Authenticate"] == 'Bearer realm="peerassist"'
    assert authentication.json()["error"]["message"] == "The operation could not be completed."
    assert "private provider detail" not in authentication.text
    assert method_not_allowed.status_code == 405
    assert method_not_allowed.headers["Allow"] == "GET"
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "30"
    assert "www-authenticate" not in unsafe.headers
    assert "set-cookie" not in unsafe.headers
    assert "location" not in unsafe.headers
    assert "x-provider-body" not in unsafe.headers
    assert "private-token" not in unsafe.text


def test_unknown_route_and_browser_auth_use_the_stable_public_contract() -> None:
    from services.api.app import create_app
    from services.api.composition import PlatformDependencies

    with TestClient(create_app(_settings(), PlatformDependencies.for_test())) as client:
        missing = client.get("/api/v1/does-not-exist", headers={"X-Request-ID": "req-404"})
        login = client.get("/api/v1/auth/login", follow_redirects=False)
        callback = client.get("/api/v1/auth/callback")
        session = client.get("/api/v1/auth/session")
        logout = client.post("/api/v1/auth/logout")
        me = client.get("/api/v1/me")

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
    assert login.status_code == 307
    assert login.headers["location"].startswith("http://identity.test/")
    assert callback.status_code == 422
    assert session.status_code == 200 and session.json() == {
        "authenticated": False,
        "user": None,
    }
    assert logout.status_code == 422
    assert me.status_code == 401
    assert me.json()["error"]["code"] == "authentication_required"


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


def test_lifespan_attempts_every_reverse_shutdown_after_a_stop_failure() -> None:
    from services.api.app import LifecycleError, create_app
    from services.api.composition import PlatformDependencies

    events: list[str] = []
    resources = (
        _TrackedLifecycle("first", events),
        _TrackedLifecycle("second", events, stop_failure=RuntimeError("private stop token")),
        _TrackedLifecycle("third", events),
    )
    app = create_app(
        _settings(),
        PlatformDependencies.for_test(lifecycle_resources=resources),
    )

    with pytest.raises(LifecycleError) as captured:
        with TestClient(app):
            pass

    assert events == [
        "start:first",
        "start:second",
        "start:third",
        "stop:third",
        "stop:second",
        "stop:first",
    ]
    assert str(captured.value) == "Platform lifecycle cleanup failed."
    assert "private" not in str(captured.value)


def test_lifespan_cleans_started_resources_after_partial_startup_failure() -> None:
    from services.api.app import LifecycleError, create_app
    from services.api.composition import PlatformDependencies

    events: list[str] = []
    resources = (
        _TrackedLifecycle("first", events),
        _TrackedLifecycle("second", events, start_failure=RuntimeError("private startup token")),
        _TrackedLifecycle("third", events),
    )
    app = create_app(
        _settings(),
        PlatformDependencies.for_test(lifecycle_resources=resources),
    )

    with pytest.raises(LifecycleError) as captured:
        with TestClient(app):
            pass

    assert events == ["start:first", "start:second", "stop:first"]
    assert str(captured.value) == "Platform lifecycle startup failed."
    assert "private" not in str(captured.value)


def test_lifespan_bounds_uncooperative_stop_and_continues_reverse_cleanup() -> None:
    from services.api.app import LifecycleError, create_app
    from services.api.composition import PlatformDependencies

    events: list[str] = []
    resources = (
        _TrackedLifecycle("first", events),
        _UnstoppableLifecycle("second", events),
        _TrackedLifecycle("third", events),
    )
    app = create_app(
        _settings(),
        PlatformDependencies.for_test(
            lifecycle_resources=resources,
            lifecycle_stop_timeout_seconds=0.02,
            lifecycle_cleanup_timeout_seconds=0.08,
        ),
    )

    started = time.monotonic()
    with pytest.raises(LifecycleError):
        with TestClient(app):
            pass
    elapsed = time.monotonic() - started

    assert elapsed < 0.15
    assert events == [
        "start:first",
        "start:second",
        "start:third",
        "stop:third",
        "stop:second",
        "stop:first",
    ]


def test_partial_startup_cleanup_bounds_uncooperative_started_resource() -> None:
    from services.api.app import LifecycleError, create_app
    from services.api.composition import PlatformDependencies

    events: list[str] = []
    resources = (
        _UnstoppableLifecycle("first", events),
        _TrackedLifecycle("second", events, start_failure=RuntimeError("private startup token")),
    )
    app = create_app(
        _settings(),
        PlatformDependencies.for_test(
            lifecycle_resources=resources,
            lifecycle_stop_timeout_seconds=0.02,
            lifecycle_cleanup_timeout_seconds=0.04,
        ),
    )

    started = time.monotonic()
    with pytest.raises(LifecycleError) as captured:
        with TestClient(app):
            pass
    elapsed = time.monotonic() - started

    assert elapsed < 0.10
    assert events == ["start:first", "start:second", "stop:first"]
    assert str(captured.value) == "Platform lifecycle startup failed."


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


@dataclass(frozen=True)
class _DelayedReadiness:
    name: str
    delay: float

    async def check(self) -> bool:
        await asyncio.sleep(self.delay)
        return True


class _HungReadiness:
    def __init__(self, name: str) -> None:
        self.name = name
        self.cancelled = False

    async def check(self) -> bool:
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled = True


class _UnstoppableReadiness:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0

    async def check(self) -> bool:
        self.calls += 1
        while True:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                continue


class _Lifecycle:
    started = False
    stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


class _TrackedLifecycle:
    def __init__(
        self,
        name: str,
        events: list[str],
        *,
        start_failure: Exception | None = None,
        stop_failure: Exception | None = None,
    ) -> None:
        self.name = name
        self.events = events
        self.start_failure = start_failure
        self.stop_failure = stop_failure

    async def start(self) -> None:
        self.events.append(f"start:{self.name}")
        if self.start_failure is not None:
            raise self.start_failure

    async def stop(self) -> None:
        self.events.append(f"stop:{self.name}")
        if self.stop_failure is not None:
            raise self.stop_failure


class _UnstoppableLifecycle(_TrackedLifecycle):
    async def stop(self) -> None:
        self.events.append(f"stop:{self.name}")
        while True:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                continue

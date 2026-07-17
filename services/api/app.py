"""FastAPI application factory for the public platform boundary."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from ipaddress import ip_address, ip_network

from fastapi import FastAPI
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from common.config import PlatformSettings

from .composition import PlatformDependencies, build_dependencies
from .errors import install_exception_handlers, new_request_id
from .routes import platform_routers

_IDENTITY_HEADERS = frozenset(
    {
        b"x-organization-id",
        b"x-project-id",
        b"x-role",
        b"x-tenant-id",
        b"x-user-id",
    }
)
_FORWARDED_HEADERS = frozenset(
    {b"forwarded", b"x-forwarded-for", b"x-forwarded-host", b"x-forwarded-proto"}
)


class LifecycleError(RuntimeError):
    """Safe startup or shutdown failure with provider details suppressed."""


async def _stop_resources(resources: list[object]) -> bool:
    failed = False
    for resource in reversed(resources):
        try:
            await resource.stop()
        except Exception:
            failed = True
    return failed


def _consume_readiness_task(tasks: set[asyncio.Task[bool]], task: asyncio.Task[bool]) -> None:
    tasks.discard(task)
    try:
        task.exception()
    except BaseException:
        pass


async def _stop_readiness_tasks(app: FastAPI, timeout: float) -> None:
    tasks = set(app.state.readiness_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)


class PublicBoundaryMiddleware:
    """Assign request IDs and normalize only explicitly trusted proxy metadata."""

    def __init__(self, app: ASGIApp, *, trusted_proxy_cidrs: tuple[str, ...]) -> None:
        self.app = app
        self._trusted_networks = tuple(ip_network(cidr, strict=False) for cidr in trusted_proxy_cidrs)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = list(scope["headers"])
        supplied_request_id = _last_header(headers, b"x-request-id")
        request_id = new_request_id(supplied_request_id)
        trusted = self._trusted_peer(scope)
        forwarded_proto = _last_header(headers, b"x-forwarded-proto") if trusted else None
        forwarded_host = _last_header(headers, b"x-forwarded-host") if trusted else None
        headers = [
            (name, value)
            for name, value in headers
            if name.lower() not in _IDENTITY_HEADERS | _FORWARDED_HEADERS | {b"x-request-id"}
        ]
        headers.append((b"x-request-id", request_id.encode("ascii")))
        if forwarded_proto in {"http", "https"}:
            scope["scheme"] = forwarded_proto
        if forwarded_host is not None and _safe_forwarded_host(forwarded_host):
            headers = [(name, value) for name, value in headers if name.lower() != b"host"]
            headers.append((b"host", forwarded_host.encode("ascii")))
        scope["headers"] = headers
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != b"x-request-id"
                ] + [(b"x-request-id", request_id.encode("ascii"))]
            await send(message)

        await self.app(scope, receive, send_with_request_id)

    def _trusted_peer(self, scope: Scope) -> bool:
        client = scope.get("client")
        if client is None:
            return False
        try:
            address = ip_address(client[0])
        except ValueError:
            return False
        return any(address in network for network in self._trusted_networks)


def _last_header(headers: list[tuple[bytes, bytes]], name: bytes) -> str | None:
    values = [value for key, value in headers if key.lower() == name]
    if not values:
        return None
    try:
        return values[-1].decode("ascii").split(",", 1)[0].strip()
    except UnicodeDecodeError:
        return None


def _safe_forwarded_host(value: str) -> bool:
    return bool(value) and len(value) <= 253 and all(
        character.isalnum() or character in ".:-[]" for character in value
    )


def create_app(settings: PlatformSettings, dependencies: PlatformDependencies) -> FastAPI:
    """Create an app without constructing environment providers at import time."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        started = []
        try:
            for resource in dependencies.lifecycle_resources:
                await resource.start()
                started.append(resource)
        except Exception:
            await _stop_resources(started)
            raise LifecycleError("Platform lifecycle startup failed.") from None
        try:
            yield
        finally:
            await _stop_readiness_tasks(
                app,
                dependencies.readiness_cancellation_timeout_seconds,
            )
            if await _stop_resources(started):
                raise LifecycleError("Platform lifecycle cleanup failed.") from None

    app = FastAPI(
        title="PeerAssist Platform API",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.dependencies = dependencies
    app.state.readiness_tasks = set()
    app.state.consume_readiness_task = lambda task: _consume_readiness_task(
        app.state.readiness_tasks,
        task,
    )
    install_exception_handlers(app)
    for router in platform_routers():
        app.include_router(router)
    app.add_middleware(
        PublicBoundaryMiddleware,
        trusted_proxy_cidrs=tuple(settings.trusted_proxy_cidrs),
    )
    return app


def create_configured_app() -> FastAPI:
    """Load environment settings and providers when the server factory is invoked."""

    settings = PlatformSettings()
    return create_app(settings, build_dependencies(settings))


def create_openapi_app() -> FastAPI:
    """Build the provider-free deterministic application used for schema export."""

    settings = PlatformSettings(
        environment="test",
        database_url="postgresql+psycopg://test:test@db/peerassist",
        oidc_issuer="http://identity.test/realms/peerassist",
        oidc_audience="peerassist-api",
        s3_endpoint="http://objects.test",
        s3_bucket="peerassist-test",
        public_base_url="http://api.test",
        allowed_origins=["http://api.test"],
        internal_legacy_audience="peerassist-legacy",
    )
    return create_app(settings, PlatformDependencies.for_test())

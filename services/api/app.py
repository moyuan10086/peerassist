"""FastAPI application factory for the public platform boundary."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from ipaddress import ip_address, ip_network
from pathlib import Path
from types import MethodType

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Mount
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from common.config import PlatformSettings

from .composition import PlatformDependencies, build_dependencies
from .dependencies import IsolatedReadinessProbe
from .errors import install_exception_handlers, new_request_id
from .identity_gateway import IdentityGatewayMiddleware
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


class FrontendStaticFiles(StaticFiles):
    """Serve the built workspace and fall back to index.html for SPA routes."""

    async def get_response(self, path: str, scope: Scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and not path.startswith(("api/", "identity/")):
                return await super().get_response("index.html", scope)
            raise


async def _stop_resources(
    resources: list[object],
    *,
    per_resource_timeout: float,
    overall_timeout: float,
) -> bool:
    failed = False
    deadline = asyncio.get_running_loop().time() + overall_timeout
    for resource in reversed(resources):
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        if not await _isolated_stop(resource, min(per_resource_timeout, remaining)):
            failed = True
    return failed


async def _isolated_stop(resource: object, timeout: float) -> bool:
    completed = threading.Event()
    succeeded = False

    def execute() -> None:
        nonlocal succeeded
        try:
            asyncio.run(resource.stop())
            succeeded = True
        except BaseException:
            succeeded = False
        finally:
            completed.set()

    threading.Thread(target=execute, name="lifecycle-stop", daemon=True).start()
    deadline = asyncio.get_running_loop().time() + timeout
    while not completed.is_set():
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(0.005, remaining))
    return succeeded


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
            await _stop_resources(
                started,
                per_resource_timeout=dependencies.lifecycle_stop_timeout_seconds,
                overall_timeout=dependencies.lifecycle_cleanup_timeout_seconds,
            )
            raise LifecycleError("Platform lifecycle startup failed.") from None
        try:
            yield
        finally:
            if await _stop_resources(
                started,
                per_resource_timeout=dependencies.lifecycle_stop_timeout_seconds,
                overall_timeout=dependencies.lifecycle_cleanup_timeout_seconds,
            ):
                raise LifecycleError("Platform lifecycle cleanup failed.") from None

    app = FastAPI(
        title="PeerAssist Platform API",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.dependencies = dependencies
    app.state.readiness_probe_slots = tuple(
        IsolatedReadinessProbe(check) for check in dependencies.readiness_checks
    )
    install_exception_handlers(app)
    for router in platform_routers():
        app.include_router(router)
    app.add_middleware(
        PublicBoundaryMiddleware,
        trusted_proxy_cidrs=tuple(settings.trusted_proxy_cidrs),
    )
    app.add_middleware(
        IdentityGatewayMiddleware,
        upstream=settings.identity_gateway_url,
    )
    frontend_root = Path(__file__).resolve().parents[2] / "web" / "peerassist-workspace" / "dist"
    if (frontend_root / "index.html").is_file():
        app.mount("/workspace", FrontendStaticFiles(directory=frontend_root, html=True), name="workspace-assets")
        app.mount("/", FrontendStaticFiles(directory=frontend_root, html=True), name="frontend")

        # Keep API routes added by integration tests and embedding applications ahead of
        # the root SPA mount. Starlette mounts are prefix matches, so a route appended
        # after ``create_app`` would otherwise be swallowed by the frontend fallback.
        original_add_api_route = app.add_api_route

        def add_api_route_before_frontend(self: FastAPI, *args, **kwargs):
            result = original_add_api_route(*args, **kwargs)
            route = self.router.routes.pop()
            mount_index = next(
                index
                for index, candidate in enumerate(self.router.routes)
                if isinstance(candidate, Mount) and candidate.name in {"workspace-assets", "frontend"}
            )
            self.router.routes.insert(mount_index, route)
            return result

        app.add_api_route = MethodType(add_api_route_before_frontend, app)
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

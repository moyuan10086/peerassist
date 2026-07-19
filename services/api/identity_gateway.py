"""Same-origin gateway for a private OIDC browser identity service."""

from __future__ import annotations

import httpx
from starlette.types import ASGIApp, Receive, Scope, Send

_REQUEST_HEADERS = frozenset(
    {b"accept", b"accept-language", b"authorization", b"content-type", b"cookie", b"user-agent"}
)
_RESPONSE_HEADERS = frozenset(
    {b"cache-control", b"content-language", b"content-type", b"expires", b"location", b"set-cookie"}
)
_MAX_REQUEST_BYTES = 2 * 1024 * 1024


class IdentityGatewayMiddleware:
    """Forward only the reserved /identity path to a private provider."""

    def __init__(self, app: ASGIApp, *, upstream: str | None) -> None:
        self.app = app
        self.upstream = upstream.rstrip("/") if upstream else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _identity_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        if self.upstream is None:
            await _response(send, 404, b'{"detail":"Not Found"}', b"application/json")
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":
                continue
            body.extend(message.get("body", b""))
            if len(body) > _MAX_REQUEST_BYTES:
                await _response(send, 413, b"request too large", b"text/plain")
                return
            if not message.get("more_body", False):
                break
        suffix = scope["path"].removeprefix("/identity")
        query = scope.get("query_string", b"")
        url = f"{self.upstream}{suffix or '/'}"
        if query:
            url = f"{url}?{query.decode('ascii')}"
        headers = [
            (name.decode("latin-1"), value.decode("latin-1"))
            for name, value in scope.get("headers", [])
            if name.lower() in _REQUEST_HEADERS
        ]
        # Keycloak keeps a long-lived gzip cache for theme resources. Asking for
        # an identity response here prevents an old compressed stylesheet from
        # surviving a theme deployment while the browser still gets a normal
        # same-origin response from this gateway.
        headers.append(("accept-encoding", "identity"))
        try:
            async with httpx.AsyncClient(follow_redirects=False, timeout=20.0) as client:
                response = await client.request(
                    scope["method"],
                    url,
                    content=bytes(body),
                    headers=headers,
                )
        except httpx.HTTPError:
            await _response(send, 503, b"identity service unavailable", b"text/plain")
            return
        response_headers = [
            (name.encode("latin-1"), value.encode("latin-1"))
            for name, value in response.headers.multi_items()
            if name.encode("latin-1").lower() in _RESPONSE_HEADERS
        ]
        await send({"type": "http.response.start", "status": response.status_code, "headers": response_headers})
        await send({"type": "http.response.body", "body": response.content})


def _identity_path(path: str) -> bool:
    return path == "/identity" or path.startswith("/identity/")


async def _response(send: Send, status: int, body: bytes, content_type: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", content_type), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})

"""Safe, stable HTTP error mapping for the public API."""

from __future__ import annotations

import re
from email.utils import parsedate_to_datetime
from http import HTTPStatus
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from peerassist.platform.errors import PlatformError

_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_UNSAFE_REQUEST_WORDS = frozenset(
    {"authorization", "cookie", "credential", "password", "secret", "token"}
)
_PLATFORM_STATUS = {
    "authentication_required": HTTPStatus.UNAUTHORIZED,
    "dependency_unavailable": HTTPStatus.SERVICE_UNAVAILABLE,
    "forbidden": HTTPStatus.FORBIDDEN,
    "idempotency_conflict": HTTPStatus.CONFLICT,
    "immutable_resource": HTTPStatus.CONFLICT,
    "invalid_canonical_payload": HTTPStatus.BAD_REQUEST,
    "invalid_upload": HTTPStatus.BAD_REQUEST,
    "not_found": HTTPStatus.NOT_FOUND,
    "payload_too_large": HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
    "stale_version": HTTPStatus.CONFLICT,
    "invalid_review_job_state": HTTPStatus.CONFLICT,
}
_HTTP_METHOD = re.compile(r"[A-Z][A-Z0-9-]{0,31}")
_AUTH_CHALLENGE = re.compile(
    r'(?:Basic|Bearer)(?: realm="[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")?'
)


class NotConfiguredError(Exception):
    """Fixed placeholder response until the OIDC/session milestone."""


def new_request_id(supplied: str | None) -> str:
    if supplied is not None:
        words = frozenset(
            word for word in re.split(r"[^a-z0-9]+", supplied.casefold()) if word
        )
        if _SAFE_REQUEST_ID.fullmatch(supplied) is not None and not words & _UNSAFE_REQUEST_WORDS:
            return supplied
    return uuid4().hex


def install_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(PlatformError, _platform_error)
    app.add_exception_handler(NotConfiguredError, _not_configured)
    app.add_exception_handler(RequestValidationError, _invalid_request)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unexpected_error)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", new_request_id(None))


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    retryable: bool,
    details: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        headers=headers,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id,
                "details": details or {},
                "retryable": retryable,
            }
        },
    )


async def _platform_error(request: Request, error: Exception) -> Response:
    assert isinstance(error, PlatformError)
    fields = error.public_fields(request_id=_request_id(request))
    if error.code == "authentication_required" and _is_browser_navigation(request):
        return_path = request.url.path
        if request.url.query:
            return_path = f"{return_path}?{request.url.query}"
        return RedirectResponse(
            url=f"/api/v1/auth/login?{urlencode({'return_path': return_path})}",
            status_code=HTTPStatus.SEE_OTHER,
            headers={"Cache-Control": "no-store"},
        )
    return _error_response(
        status_code=_PLATFORM_STATUS.get(error.code, HTTPStatus.INTERNAL_SERVER_ERROR),
        code=str(fields["code"]),
        message=str(fields["message"]),
        request_id=str(fields["request_id"]),
        details=dict(fields["details"]),
        retryable=bool(fields["retryable"]),
    )


def _is_browser_navigation(request: Request) -> bool:
    if request.method.upper() not in {"GET", "HEAD"}:
        return False
    accepted = request.headers.get("accept", "").casefold()
    return any(item.partition(";")[0].strip() == "text/html" for item in accepted.split(","))


async def _not_configured(request: Request, error: Exception) -> JSONResponse:
    del error
    return _error_response(
        status_code=HTTPStatus.SERVICE_UNAVAILABLE,
        code="not_configured",
        message="Authentication is not configured.",
        request_id=_request_id(request),
        retryable=False,
    )


async def _invalid_request(request: Request, error: Exception) -> JSONResponse:
    del error
    return _error_response(
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        code="platform_error",
        message="The request could not be validated.",
        request_id=_request_id(request),
        retryable=False,
    )


async def _http_error(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, StarletteHTTPException)
    if error.status_code == HTTPStatus.NOT_FOUND:
        code = "not_found"
        message = "The requested resource was not found."
    else:
        code = "platform_error"
        message = "The operation could not be completed."
    headers = _safe_standard_headers(error.headers)
    if error.status_code == HTTPStatus.METHOD_NOT_ALLOWED and "Allow" not in headers:
        allowed = _allowed_methods(request)
        if allowed:
            headers["Allow"] = allowed
    return _error_response(
        status_code=error.status_code,
        code=code,
        message=message,
        request_id=_request_id(request),
        retryable=False,
        headers=headers,
    )


def _allowed_methods(request: Request) -> str:
    methods: set[str] = set()
    path = request.url.path
    candidates = []
    for route in request.app.router.routes:
        candidates.append(route)
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            candidates.extend(original_router.routes)
    for route in candidates:
        if getattr(route, "path", None) == path:
            methods.update(getattr(route, "methods", ()) or ())
    return ", ".join(sorted(methods))


def _safe_standard_headers(headers: dict[str, str] | None) -> dict[str, str]:
    if not headers:
        return {}
    result: dict[str, str] = {}
    for name, value in headers.items():
        normalized = name.casefold()
        if not _safe_header_value(value):
            continue
        if normalized == "allow" and _safe_allow(value):
            result["Allow"] = value
        elif normalized == "retry-after" and _safe_retry_after(value):
            result["Retry-After"] = value
        elif normalized == "www-authenticate" and _AUTH_CHALLENGE.fullmatch(value):
            result["WWW-Authenticate"] = value
    return result


def _safe_header_value(value: str) -> bool:
    if not value or len(value) > 512 or any(character in value for character in "\r\n\0"):
        return False
    words = frozenset(word for word in re.split(r"[^a-z0-9]+", value.casefold()) if word)
    return not words & _UNSAFE_REQUEST_WORDS


def _safe_allow(value: str) -> bool:
    methods = [method.strip() for method in value.split(",")]
    return bool(methods) and all(_HTTP_METHOD.fullmatch(method) for method in methods)


def _safe_retry_after(value: str) -> bool:
    if value.isdecimal():
        return len(value) <= 10
    try:
        return parsedate_to_datetime(value).tzinfo is not None
    except (TypeError, ValueError, OverflowError):
        return False


async def _unexpected_error(request: Request, error: Exception) -> JSONResponse:
    del error
    fields = PlatformError().public_fields(request_id=_request_id(request))
    return _error_response(
        status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
        code=str(fields["code"]),
        message=str(fields["message"]),
        request_id=str(fields["request_id"]),
        details=dict(fields["details"]),
        retryable=bool(fields["retryable"]),
    )

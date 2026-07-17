"""Safe, stable HTTP error mapping for the public API."""

from __future__ import annotations

import re
from http import HTTPStatus
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
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
    "not_found": HTTPStatus.NOT_FOUND,
    "payload_too_large": HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
    "stale_version": HTTPStatus.CONFLICT,
}


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
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
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


async def _platform_error(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, PlatformError)
    fields = error.public_fields(request_id=_request_id(request))
    return _error_response(
        status_code=_PLATFORM_STATUS.get(error.code, HTTPStatus.INTERNAL_SERVER_ERROR),
        code=str(fields["code"]),
        message=str(fields["message"]),
        request_id=str(fields["request_id"]),
        details=dict(fields["details"]),
        retryable=bool(fields["retryable"]),
    )


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
    return _error_response(
        status_code=error.status_code,
        code=code,
        message=message,
        request_id=_request_id(request),
        retryable=False,
    )


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

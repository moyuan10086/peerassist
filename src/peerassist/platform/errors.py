"""Stable, provider-neutral domain errors."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import PurePosixPath, PureWindowsPath
from types import MappingProxyType

from .models import FrozenJsonValue

SafeDetailValue = None | bool | int | str

_PUBLIC_MESSAGES = {
    "platform_error": "The operation could not be completed.",
    "not_found": "The requested resource was not found.",
    "forbidden": "The requested operation is not permitted.",
    "authentication_required": "Authentication is required.",
    "stale_version": "The resource changed before the operation completed.",
    "idempotency_conflict": "The idempotency key was already used for a different request.",
    "invalid_canonical_payload": "The command payload is not valid canonical JSON data.",
    "dependency_unavailable": "A required service is temporarily unavailable.",
    "payload_too_large": "The uploaded payload exceeds the configured limit.",
    "immutable_resource": "The resource is read-only.",
}
_SAFE_DETAIL_KEYS = frozenset(
    {
        "current_version",
        "expected_version",
        "field",
        "limit",
        "reason",
        "resource_id",
        "resource_type",
    }
)
_SAFE_DETAIL_TEXT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


def _safe_details(details: Mapping[str, SafeDetailValue] | None) -> Mapping[str, SafeDetailValue]:
    if details is None:
        return MappingProxyType({})
    result: dict[str, SafeDetailValue] = {}
    for key, value in details.items():
        if key not in _SAFE_DETAIL_KEYS:
            raise ValueError(f"detail key {key!r} is not public-safe")
        if value is not None and not isinstance(value, (bool, int, str)):
            raise TypeError("public error details must contain safe scalar values")
        if isinstance(value, str) and (
            PurePosixPath(value).is_absolute()
            or PureWindowsPath(value).is_absolute()
            or value.startswith("file:")
        ):
            raise ValueError("public error details must not contain absolute private paths")
        if isinstance(value, str) and _SAFE_DETAIL_TEXT.fullmatch(value) is None:
            raise ValueError("public error string details must be safe identifiers")
        result[key] = value
    return MappingProxyType(result)


class PlatformError(Exception):
    code = "platform_error"
    retryable = False

    def __init__(
        self,
        *,
        details: Mapping[str, SafeDetailValue] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        self.message = _PUBLIC_MESSAGES[self.code]
        self.details = _safe_details(details)
        if cause is not None:
            self.__cause__ = cause
        super().__init__(self.message)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r})"

    def public_fields(self, *, request_id: str) -> dict[str, FrozenJsonValue]:
        return {
            "code": self.code,
            "message": self.message,
            "request_id": request_id,
            "retryable": self.retryable,
            "details": dict(self.details),
        }


class NotFound(PlatformError):
    code = "not_found"


class Forbidden(PlatformError):
    code = "forbidden"


class AuthenticationRequired(PlatformError):
    code = "authentication_required"


class StaleVersion(PlatformError):
    code = "stale_version"


class IdempotencyConflict(PlatformError):
    code = "idempotency_conflict"


class InvalidCanonicalPayload(PlatformError):
    code = "invalid_canonical_payload"


class DependencyUnavailable(PlatformError):
    code = "dependency_unavailable"
    retryable = True


class PayloadTooLarge(PlatformError):
    code = "payload_too_large"


class ImmutableResource(PlatformError):
    code = "immutable_resource"

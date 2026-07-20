"""Stable, provider-neutral domain errors."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import PurePosixPath, PureWindowsPath
from types import MappingProxyType

from .models import FrozenJsonValue

SafeDetailValue = None | bool | int | str

_ERROR_SPECS = MappingProxyType(
    {
        "platform_error": ("The operation could not be completed.", False),
        "not_found": ("The requested resource was not found.", False),
        "forbidden": ("The requested operation is not permitted.", False),
        "authentication_required": ("Authentication is required.", False),
        "stale_version": ("The resource changed before the operation completed.", False),
        "idempotency_conflict": (
            "The idempotency key was already used for a different request.",
            False,
        ),
        "invalid_canonical_payload": ("The command payload is not valid canonical JSON data.", False),
        "invalid_upload": ("The uploaded file is not a supported document.", False),
        "dependency_unavailable": ("A required service is temporarily unavailable.", True),
        "payload_too_large": ("The uploaded payload exceeds the configured limit.", False),
        "immutable_resource": ("The resource is read-only.", False),
        "invalid_review_job_state": ("只有失败或已取消的任务可以删除。", False),
    }
)
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
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_UNSAFE_REQUEST_WORDS = frozenset({"authorization", "cookie", "credential", "password", "secret", "token"})


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

    def __setattr__(self, name: str, value: object) -> None:
        if name in {
            "args",
            "code",
            "details",
            "message",
            "retryable",
            "_public_code",
            "_public_details",
            "_public_message",
            "_public_retryable",
        }:
            raise AttributeError("PlatformError public state is immutable")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        *,
        details: Mapping[str, SafeDetailValue] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        code = type(self).code
        frozen_details = _safe_details(details)
        details_tuple = tuple(sorted(frozen_details.items()))
        if cause is not None:
            self.__cause__ = cause
        super().__init__(code, details_tuple)

    def _snapshot(self) -> tuple[str, tuple[tuple[str, SafeDetailValue], ...]]:
        code, details = self.args
        if code not in _ERROR_SPECS or not isinstance(details, tuple):
            return "platform_error", ()
        return code, details

    @property
    def message(self) -> str:
        return _ERROR_SPECS[self._snapshot()[0]][0]

    @property
    def details(self) -> Mapping[str, SafeDetailValue]:
        return MappingProxyType(dict(self._snapshot()[1]))

    def __str__(self) -> str:
        return self.message

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self._snapshot()[0]!r})"

    def public_fields(self, *, request_id: str) -> dict[str, FrozenJsonValue]:
        request_words = frozenset(word for word in re.split(r"[^a-z0-9]+", request_id.casefold()) if word)
        if _SAFE_REQUEST_ID.fullmatch(request_id) is None or request_words & _UNSAFE_REQUEST_WORDS:
            raise ValueError("request_id must be a safe identifier of at most 128 characters")
        code, details = self._snapshot()
        message, retryable = _ERROR_SPECS[code]
        return {
            "code": code,
            "message": message,
            "request_id": request_id,
            "retryable": retryable,
            "details": dict(details),
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


class InvalidUpload(PlatformError):
    code = "invalid_upload"


class DependencyUnavailable(PlatformError):
    code = "dependency_unavailable"
    retryable = True


class PayloadTooLarge(PlatformError):
    code = "payload_too_large"


class ImmutableResource(PlatformError):
    code = "immutable_resource"


class InvalidReviewJobState(PlatformError):
    code = "invalid_review_job_state"

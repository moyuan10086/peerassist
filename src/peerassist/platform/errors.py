"""Stable, provider-neutral domain errors."""

from __future__ import annotations

from collections.abc import Mapping

from .models import JsonValue


class PlatformError(Exception):
    code = "platform_error"
    message = "The operation could not be completed."
    retryable = False

    def __init__(self, message: str | None = None, *, details: Mapping[str, JsonValue] | None = None) -> None:
        self.safe_message = message or self.message
        self.details = dict(details or {})
        super().__init__(self.safe_message)


class NotFound(PlatformError):
    code = "not_found"
    message = "The requested resource was not found."


class Forbidden(PlatformError):
    code = "forbidden"
    message = "The requested operation is not permitted."


class AuthenticationRequired(PlatformError):
    code = "authentication_required"
    message = "Authentication is required."


class StaleVersion(PlatformError):
    code = "stale_version"
    message = "The resource changed before the operation completed."


class IdempotencyConflict(PlatformError):
    code = "idempotency_conflict"
    message = "The idempotency key was already used for a different request."


class InvalidCanonicalPayload(PlatformError):
    code = "invalid_canonical_payload"
    message = "The command payload is not valid canonical JSON data."


class DependencyUnavailable(PlatformError):
    code = "dependency_unavailable"
    message = "A required service is temporarily unavailable."
    retryable = True


class PayloadTooLarge(PlatformError):
    code = "payload_too_large"
    message = "The uploaded payload exceeds the configured limit."


class ImmutableResource(PlatformError):
    code = "immutable_resource"
    message = "The resource is read-only."

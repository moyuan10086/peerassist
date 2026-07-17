"""Deterministic command payload hashing and replay decisions."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from .errors import IdempotencyConflict, InvalidCanonicalPayload
from .models import CommandRecord, IdempotencyDecision, IdempotencyResult, JsonValue


def _validate_json(value: Any) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidCanonicalPayload()
        return
    if isinstance(value, list):
        for item in value:
            _validate_json(item)
        return
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise InvalidCanonicalPayload()
        for item in value.values():
            _validate_json(item)
        return
    raise InvalidCanonicalPayload()


def canonical_json_bytes(payload: JsonValue) -> bytes:
    """Serialize JSON while preserving every value and sorting object keys only."""
    _validate_json(payload)
    try:
        rendered = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return rendered.encode("utf-8")
    except (TypeError, UnicodeEncodeError, ValueError) as error:
        raise InvalidCanonicalPayload() from error


def canonical_json_digest(payload: JsonValue) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def resolve_idempotency(existing: CommandRecord | None, payload: JsonValue) -> IdempotencyResult:
    """Return execute/replay or raise a stable conflict without exposing digests."""
    digest = canonical_json_digest(payload)
    if existing is None:
        return IdempotencyResult(IdempotencyDecision.EXECUTE)
    if existing.payload_digest != digest:
        raise IdempotencyConflict()
    if existing.completed_at is None:
        return IdempotencyResult(IdempotencyDecision.EXECUTE)
    return IdempotencyResult(
        IdempotencyDecision.REPLAY,
        response_status=existing.response_status,
        response_body=existing.response_body,
    )

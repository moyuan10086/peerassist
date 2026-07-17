from datetime import UTC, datetime
from uuid import uuid4

import pytest

from peerassist.platform.errors import IdempotencyConflict, InvalidCanonicalPayload
from peerassist.platform.idempotency import canonical_json_digest, resolve_idempotency
from peerassist.platform.models import CommandRecord, IdempotencyDecision

NOW = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)


def record_for(payload: object) -> CommandRecord:
    return CommandRecord(
        id=uuid4(),
        organization_id=uuid4(),
        actor_id=uuid4(),
        operation="paper.upload",
        idempotency_key="request-123",
        payload_digest=canonical_json_digest(payload),
        response_status=201,
        response_body={"paper_id": "paper-1"},
        created_at=NOW,
        completed_at=NOW,
    )


def test_canonical_digest_ignores_only_object_key_order() -> None:
    left = {"outer": {"b": 2, "a": 1}, "items": [" x ", 1]}
    reordered = {"items": [" x ", 1], "outer": {"a": 1, "b": 2}}

    assert canonical_json_digest(left) == canonical_json_digest(reordered)
    assert canonical_json_digest({"value": " x "}) != canonical_json_digest({"value": "x"})
    assert canonical_json_digest({"value": 1}) != canonical_json_digest({"value": "1"})
    assert canonical_json_digest({"value": 1}) != canonical_json_digest({"value": 1.0})


@pytest.mark.parametrize(
    "payload",
    [
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": float("-inf")},
        {1: "non-string-key"},
        {"value": (1, 2)},
        {"value": uuid4()},
        {"value": "\ud800"},
    ],
)
def test_canonical_digest_rejects_non_json_or_ambiguous_values(payload: object) -> None:
    with pytest.raises(InvalidCanonicalPayload):
        canonical_json_digest(payload)


def test_same_key_and_same_canonical_payload_replays_stored_response() -> None:
    original = {"paper": {"sha256": "a" * 64, "size": 42}, "expected_version": 1}
    existing = record_for(original)

    result = resolve_idempotency(
        existing,
        {"expected_version": 1, "paper": {"size": 42, "sha256": "a" * 64}},
    )

    assert result.decision is IdempotencyDecision.REPLAY
    assert result.response_status == 201
    assert result.response_body == {"paper_id": "paper-1"}


def test_same_key_with_changed_payload_is_a_stable_conflict() -> None:
    existing = record_for({"expected_version": 1})

    with pytest.raises(IdempotencyConflict) as raised:
        resolve_idempotency(existing, {"expected_version": 2})

    assert raised.value.code == "idempotency_conflict"
    assert "digest" not in str(raised.value).lower()

"""Apply human confirmation actions to PeerAssist concerns."""

from __future__ import annotations

from typing import Any

from schemas.peerassist import Concern, ConcernLevel, ConcernStatus, HumanConfirmationAction


def _apply_action(concern: Concern, action: HumanConfirmationAction) -> Concern:
    updated = concern.model_copy(deep=True)
    normalized = action.action.strip().lower()
    if normalized == "confirm":
        updated.status = ConcernStatus.CONFIRMED
    elif normalized == "rewrite":
        updated.status = ConcernStatus.REWRITTEN
        if action.new_text.strip():
            updated.author_action = action.new_text.strip()
    elif normalized == "downgrade":
        updated.status = ConcernStatus.DOWNGRADED
        if updated.level is ConcernLevel.MAJOR_CONCERN:
            updated.level = ConcernLevel.MINOR_CONCERN
        if action.new_text.strip():
            updated.author_action = action.new_text.strip()
    elif normalized == "delete":
        updated.status = ConcernStatus.DELETED
    elif normalized == "mark_pending":
        updated.status = ConcernStatus.PENDING_HUMAN_CONFIRMATION
    else:
        updated.metadata = dict(updated.metadata)
        updated.metadata.setdefault("unsupported_confirmation_actions", []).append(normalized)
    updated.metadata = dict(updated.metadata)
    updated.metadata["last_confirmation_action"] = action.model_dump(mode="json")
    return updated


def apply_confirmations(
    concerns: list[Concern],
    actions: list[HumanConfirmationAction],
) -> list[Concern]:
    actions_by_concern: dict[str, list[HumanConfirmationAction]] = {}
    for action in actions:
        actions_by_concern.setdefault(action.concern_id, []).append(action)

    result: list[Concern] = []
    for concern in concerns:
        updated = concern
        for action in actions_by_concern.get(concern.id, []):
            updated = _apply_action(updated, action)
        result.append(updated)
    return result


def build_confirmation_bundle(
    *, concerns: list[Concern], evidence_lookup: dict[str, str] | None = None
) -> dict[str, Any]:
    evidence_lookup = evidence_lookup or {}
    groups: dict[str, list[dict[str, Any]]] = {status.value: [] for status in ConcernStatus}

    for concern in concerns:
        row = concern.model_dump(mode="json")
        row["evidence"] = [
            {"id": evidence_id, "locator": evidence_lookup.get(evidence_id, evidence_id)}
            for evidence_id in concern.evidence_ids
        ]
        groups[concern.status.value].append(row)

    return {
        "schema_version": "peerassist.confirmation_bundle.v1",
        "counts_by_status": {status: len(rows) for status, rows in groups.items()},
        "groups": groups,
    }

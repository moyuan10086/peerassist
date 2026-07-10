"""Apply human confirmation actions to PeerAssist concerns."""

from __future__ import annotations

from typing import Any

from schemas.peerassist import Concern, ConcernLevel, ConcernStatus, HumanConfirmationAction

ALLOWED_CONFIRMATION_ACTIONS = [
    "confirm",
    "rewrite",
    "downgrade",
    "delete",
    "mark_pending",
]


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


def build_confirmation_review_queue(bundle: dict[str, Any]) -> dict[str, Any]:
    groups = bundle.get("groups") if isinstance(bundle.get("groups"), dict) else {}
    ordered_statuses = [
        ConcernStatus.PENDING_HUMAN_CONFIRMATION.value,
        ConcernStatus.REWRITTEN.value,
        ConcernStatus.DOWNGRADED.value,
        ConcernStatus.CONFIRMED.value,
        ConcernStatus.DELETED.value,
    ]
    items: list[dict[str, Any]] = []
    for status in ordered_statuses:
        rows = groups.get(status, [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                items.append(_queue_item(row, position=len(items) + 1))
    return {
        "schema_version": "peerassist.confirmation_review_queue.v1",
        "source_schema_version": bundle.get("schema_version", ""),
        "total_items": len(items),
        "items": items,
        "allowed_actions": list(ALLOWED_CONFIRMATION_ACTIONS),
    }


def confirmation_action_from_queue_decision(
    *,
    concern_id: str,
    action: str,
    reviewer_id: str,
    timestamp: str,
    previous_text: str = "",
    new_text: str = "",
    reason: str = "",
    metadata: dict[str, Any] | None = None,
) -> HumanConfirmationAction:
    normalized = action.strip().lower()
    if normalized not in ALLOWED_CONFIRMATION_ACTIONS:
        raise ValueError(f"unsupported confirmation action: {action}")
    return HumanConfirmationAction(
        concern_id=concern_id,
        action=normalized,
        previous_text=previous_text,
        new_text=new_text,
        reviewer_id=reviewer_id,
        timestamp=timestamp,
        reason=reason,
        metadata=dict(metadata or {}),
    )


def _queue_item(row: dict[str, Any], *, position: int) -> dict[str, Any]:
    return {
        "position": position,
        "id": row.get("id", ""),
        "status": row.get("status", ""),
        "level": row.get("level", ""),
        "category": row.get("category", ""),
        "title": row.get("title", ""),
        "impact": row.get("impact", ""),
        "benign_explanation": row.get("benign_explanation", ""),
        "author_action": row.get("author_action", ""),
        "evidence": list(row.get("evidence") or []),
        "evidence_ids": list(row.get("evidence_ids") or []),
        "source_agent_ids": list(row.get("source_agent_ids") or []),
        "source_check_ids": list(row.get("source_check_ids") or []),
        "allowed_actions": list(ALLOWED_CONFIRMATION_ACTIONS),
        "metadata": dict(row.get("metadata") or {}),
    }

"""Apply human confirmation actions to PeerAssist concerns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from schemas.citation import CitationAudit
from schemas.peerassist import Concern, ConcernLevel, ConcernStatus, HumanConfirmationAction

ALLOWED_CONFIRMATION_ACTIONS = [
    "confirm",
    "rewrite",
    "downgrade",
    "delete",
    "mark_pending",
]


@dataclass(frozen=True)
class CitationConfirmationReconciliation:
    replayable_actions: list[HumanConfirmationAction]
    unresolved_historical_actions: list[HumanConfirmationAction]
    concern_ids_needing_reconciliation: list[str]


def reconcile_citation_confirmations(
    concerns: list[Concern],
    actions: list[HumanConfirmationAction],
    audit: CitationAudit,
) -> CitationConfirmationReconciliation:
    """Classify citation confirmation history without changing historical actions."""
    concern_ids_by_finding: dict[str, set[str]] = {}
    for concern in concerns:
        finding_ids = concern.metadata.get("citation_finding_ids", [])
        if isinstance(finding_ids, list):
            for finding_id in finding_ids:
                if isinstance(finding_id, str):
                    concern_ids_by_finding.setdefault(finding_id, set()).add(concern.id)
    current_finding_ids = {finding.id for finding in audit.findings}
    audit_version = f"{audit.schema_version}:{audit.parse_version}"
    replayable: list[HumanConfirmationAction] = []
    unresolved: list[HumanConfirmationAction] = []
    needs_reconciliation: list[str] = []
    for action in actions:
        if not action.citation_finding_ids:
            replayable.append(action)
            continue
        action_findings = set(action.citation_finding_ids)
        current_concerns = set().union(*(concern_ids_by_finding.get(finding_id, set()) for finding_id in action_findings))
        unchanged = action_findings <= current_finding_ids and action.audit_version == audit_version
        if unchanged and action.concern_id in current_concerns:
            replayable.append(action)
            continue
        unresolved.append(action)
        if action.concern_id not in needs_reconciliation:
            needs_reconciliation.append(action.concern_id)
    return CitationConfirmationReconciliation(
        replayable_actions=replayable,
        unresolved_historical_actions=unresolved,
        concern_ids_needing_reconciliation=needs_reconciliation,
    )


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
            if action.finding_lineage_id and action.finding_lineage_id != concern.finding_lineage_id:
                continue
            if action.finding_id and action.finding_id != concern.finding_id:
                continue
            if action.revision is not None and action.revision != concern.revision:
                continue
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
    finding_lineage_id: str = "",
    finding_id: str = "",
    revision: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> HumanConfirmationAction:
    normalized = action.strip().lower()
    if normalized not in ALLOWED_CONFIRMATION_ACTIONS:
        raise ValueError(f"unsupported confirmation action: {action}")
    return HumanConfirmationAction(
        concern_id=concern_id,
        finding_lineage_id=finding_lineage_id,
        finding_id=finding_id,
        revision=revision,
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
        "finding_lineage_id": row.get("finding_lineage_id", ""),
        "finding_id": row.get("finding_id", ""),
        "revision": row.get("revision", 1),
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

"""Automatic evidence-binding audit helpers for PeerAssist evaluation."""

from __future__ import annotations

import re
from typing import Any

NUMERIC_FACT_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?\b"
    r"|\b\d+(?:\.\d+)?%"
    r"|\b\d+(?:\.\d+)?\b"
)


def audit_concern_evidence(
    *, concerns: list[dict[str, Any]], ledger_items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    items_by_id = {
        str(item.get("id")): item
        for item in ledger_items
        if str(item.get("id") or "").strip()
    }
    available_ids = set(items_by_id)
    rows: list[dict[str, Any]] = []
    for concern in concerns:
        if str(concern.get("status") or "").strip().lower() == "pending_human_confirmation":
            continue
        evidence_ids = [
            str(item)
            for item in concern.get("evidence_ids", [])
            if str(item).strip()
        ]
        if not evidence_ids:
            continue
        missing = [evidence_id for evidence_id in evidence_ids if evidence_id not in available_ids]
        evidence_text = " ".join(str(items_by_id.get(evidence_id, {}).get("text") or "") for evidence_id in evidence_ids)
        unsupported_numeric = _unsupported_numeric_facts(concern, evidence_text)
        rows.append(
            {
                "concern_id": str(concern.get("id") or ""),
                "faithful": not missing and not unsupported_numeric,
                "evidence_ids": evidence_ids,
                "missing_evidence_ids": missing,
                "unsupported_numeric_facts": unsupported_numeric,
                "audit": "evidence_ids_and_numeric_facts",
            }
        )
    return rows


def _unsupported_numeric_facts(concern: dict[str, Any], evidence_text: str) -> list[str]:
    concern_text = " ".join(
        str(concern.get(field) or "")
        for field in ("title", "impact", "author_action")
    )
    evidence_numbers = {_normalize_number(match.group(0)) for match in NUMERIC_FACT_RE.finditer(evidence_text)}
    unsupported: list[str] = []
    for match in NUMERIC_FACT_RE.finditer(concern_text):
        token = match.group(0)
        normalized = _normalize_number(token)
        if normalized in evidence_numbers:
            continue
        if token not in unsupported:
            unsupported.append(token)
    return unsupported


def _normalize_number(value: str) -> str:
    return re.sub(r"\s+", "", value.strip())

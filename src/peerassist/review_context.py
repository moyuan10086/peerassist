"""Claim-prioritized, evidence-traceable model context construction."""

from __future__ import annotations

from typing import Any


def build_review_context(
    *,
    ledger: dict[str, Any],
    paper_profile: dict[str, Any],
    claim_graph: dict[str, Any],
    experiment_inventory: dict[str, Any],
    review_plan: dict[str, Any],
    deterministic_checks: dict[str, Any],
    max_evidence: int = 80,
) -> dict[str, Any]:
    """Select evidence by review importance instead of source order."""

    rows = [row for row in ledger.get("items", []) if isinstance(row, dict)]
    by_id = {str(row.get("id") or ""): row for row in rows if row.get("id")}
    priority_ids: list[str] = []

    route = [row for row in review_plan.get("reading_route", []) if isinstance(row, dict)]
    for row in sorted(route, key=lambda item: int(item.get("rank") or 10**9)):
        _extend_ids(priority_ids, row.get("evidence_ids"))

    claims = [row for row in claim_graph.get("claims", []) if isinstance(row, dict)]
    core_ids = set(review_plan.get("core_claim_ids") or [])
    claims.sort(
        key=lambda row: (
            0 if row.get("claim_id") in core_ids else 1,
            -float(row.get("centrality") or 0),
            str(row.get("claim_id") or ""),
        )
    )
    for claim in claims:
        _extend_ids(priority_ids, claim.get("evidence_ids"))
        _extend_ids(priority_ids, claim.get("support_evidence_ids"))

    _collect_evidence_ids(experiment_inventory, priority_ids)
    _collect_evidence_ids(paper_profile, priority_ids)
    _collect_evidence_ids(deterministic_checks, priority_ids)
    _extend_ids(priority_ids, by_id)

    selected_ids = [evidence_id for evidence_id in priority_ids if evidence_id in by_id][
        : max(1, max_evidence)
    ]
    selected_rows = [_evidence_summary(by_id[evidence_id]) for evidence_id in selected_ids]
    return {
        "schema_version": "peerassist.review_context.v1",
        "paper_profile": paper_profile,
        "claim_graph": claim_graph,
        "experiment_inventory": experiment_inventory,
        "review_plan": review_plan,
        "deterministic_checks": deterministic_checks,
        "selected_evidence_ids": selected_ids,
        "selected_evidence": selected_rows,
    }


def _collect_evidence_ids(value: Any, target: list[str]) -> None:
    if isinstance(value, dict):
        _extend_ids(target, value.get("evidence_ids"))
        for child in value.values():
            _collect_evidence_ids(child, target)
    elif isinstance(value, list):
        for child in value:
            _collect_evidence_ids(child, target)


def _extend_ids(target: list[str], values: Any) -> None:
    if isinstance(values, dict):
        iterable = values.keys()
    elif isinstance(values, (list, tuple, set)):
        iterable = values
    else:
        return
    seen = set(target)
    for value in iterable:
        evidence_id = str(value or "")
        if evidence_id and evidence_id not in seen:
            target.append(evidence_id)
            seen.add(evidence_id)


def _evidence_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id") or ""),
        "type": str(row.get("type") or ""),
        "page": row.get("page"),
        "locator": str(row.get("locator") or ""),
        "section": str(row.get("section") or ""),
        "text": str(row.get("text") or "")[:800],
        "bbox": row.get("bbox") or row.get("metadata", {}).get("bbox"),
    }

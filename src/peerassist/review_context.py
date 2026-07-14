"""Claim-prioritized, evidence-traceable model context construction."""

from __future__ import annotations

import json
from typing import Any


def build_review_context(
    *,
    ledger: dict[str, Any],
    paper_profile: dict[str, Any],
    claim_graph: dict[str, Any],
    experiment_inventory: dict[str, Any],
    review_plan: dict[str, Any],
    deterministic_checks: dict[str, Any],
    max_evidence: int = 40,
    max_context_chars: int = 12_000,
    max_serialized_chars: int = 24_000,
) -> dict[str, Any]:
    """Select complete evidence blocks by review importance instead of source order."""

    rows = [row for row in ledger.get("items", []) if isinstance(row, dict)]
    by_id = {str(row.get("id") or ""): row for row in rows if row.get("id")}
    blocks: dict[str, list[dict[str, Any]]] = {}
    block_order: list[str] = []
    for row in rows:
        key = _block_key(row)
        if key not in blocks:
            blocks[key] = []
            block_order.append(key)
        blocks[key].append(row)
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

    priority_blocks: list[str] = []
    seen_blocks: set[str] = set()
    for evidence_id in priority_ids:
        row = by_id.get(evidence_id)
        if row is None:
            continue
        key = _block_key(row)
        if key not in seen_blocks:
            priority_blocks.append(key)
            seen_blocks.add(key)
    for key in block_order:
        if key not in seen_blocks:
            priority_blocks.append(key)
            seen_blocks.add(key)

    selected_rows: list[dict[str, Any]] = []
    selected_ids: list[str] = []
    selected_chars = 0
    block_limit = max(1, int(max_evidence))
    char_limit = max(1, int(max_context_chars))
    for key in priority_blocks:
        if len(selected_rows) >= block_limit or selected_chars >= char_limit:
            break
        summary = _evidence_block_summary(blocks[key], max_chars=char_limit - selected_chars)
        if not summary["text"]:
            continue
        selected_rows.append(summary)
        selected_chars += len(summary["text"])
        _extend_ids(selected_ids, summary["evidence_ids"])
    context = {
        "schema_version": "peerassist.review_context.v2",
        "paper_profile": _paper_profile_summary(paper_profile),
        "claim_graph": _claim_graph_summary(claim_graph, core_ids),
        "experiment_inventory": _experiment_inventory_summary(experiment_inventory),
        "review_plan": _review_plan_summary(review_plan),
        "deterministic_checks": _deterministic_summary(deterministic_checks),
        "selected_evidence_ids": selected_ids,
        "selected_evidence": selected_rows,
        "budget": {
            "max_blocks": block_limit,
            "max_chars": char_limit,
            "selected_blocks": len(selected_rows),
            "selected_chars": selected_chars,
            "max_serialized_chars": max(1, int(max_serialized_chars)),
            "serialized_chars": 0,
        },
    }
    total_limit = max(1, int(max_serialized_chars))
    _trim_to_serialized_budget(context, total_limit)
    return context


def _serialized_chars(value: dict[str, Any]) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _refresh_serialized_chars(context: dict[str, Any]) -> int:
    budget = context["budget"]
    budget["serialized_chars"] = 0
    for _ in range(3):
        measured = _serialized_chars(context)
        if measured == budget["serialized_chars"]:
            return measured
        budget["serialized_chars"] = measured
    return _serialized_chars(context)


def _sync_evidence_budget(context: dict[str, Any]) -> None:
    selected_rows = context["selected_evidence"]
    selected_ids = [
        evidence_id
        for row in selected_rows
        for evidence_id in row.get("evidence_ids", [])
    ]
    context["selected_evidence_ids"] = list(dict.fromkeys(selected_ids))
    context["budget"]["selected_blocks"] = len(selected_rows)
    context["budget"]["selected_chars"] = sum(
        len(str(row.get("text") or "")) for row in selected_rows
    )


def _trim_lists(
    context: dict[str, Any],
    total_limit: int,
    targets: list[tuple[list[Any], int]],
) -> None:
    while _refresh_serialized_chars(context) > total_limit:
        changed = False
        for rows, minimum in targets:
            if len(rows) > minimum:
                rows.pop()
                changed = True
        if not changed:
            break


def _trim_to_serialized_budget(context: dict[str, Any], total_limit: int) -> None:
    checks = context["deterministic_checks"]["checks"]
    assignments = context["review_plan"]["agent_assignments"]
    route = context["review_plan"]["reading_route"]
    experiments = context["experiment_inventory"]["experiments"]
    claims = context["claim_graph"]["claims"]
    evidence = context["selected_evidence"]

    _trim_lists(
        context,
        total_limit,
        [(checks, 8), (assignments, 7), (route, 5), (experiments, 3), (claims, 6)],
    )
    while evidence and _refresh_serialized_chars(context) > total_limit:
        evidence.pop()
        _sync_evidence_budget(context)
    _trim_lists(
        context,
        total_limit,
        [(checks, 1), (assignments, 1), (route, 1), (experiments, 1), (claims, 1)],
    )
    while evidence and _refresh_serialized_chars(context) > total_limit:
        evidence.pop()
        _sync_evidence_budget(context)
    _refresh_serialized_chars(context)


def _string_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value[:limit] if str(item).strip()]


def _bounded_value(value: Any, *, text_limit: int = 600, list_limit: int = 3) -> Any:
    if isinstance(value, str):
        return value[:text_limit]
    if isinstance(value, list):
        return [
            item[:text_limit] if isinstance(item, str) else item
            for item in value[:list_limit]
        ]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:text_limit]


def _grounded_summary(value: Any, *, text_limit: int = 600, list_limit: int = 3) -> dict[str, Any]:
    field = value if isinstance(value, dict) else {}
    return {
        "value": _bounded_value(field.get("value"), text_limit=text_limit, list_limit=list_limit),
        "evidence_ids": _string_list(field.get("evidence_ids"), limit=6),
        "provenance": str(field.get("provenance") or ""),
        "needs_human_review": bool(field.get("needs_human_review")),
    }


def _compact_grounded_summary(
    value: Any,
    *,
    text_limit: int,
    list_limit: int,
    evidence_limit: int = 4,
) -> dict[str, Any]:
    field = value if isinstance(value, dict) else {}
    return {
        "value": _bounded_value(field.get("value"), text_limit=text_limit, list_limit=list_limit),
        "evidence_ids": _string_list(field.get("evidence_ids"), limit=evidence_limit),
    }


def _paper_profile_summary(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": str(payload.get("schema_version") or ""),
        "paper_id": str(payload.get("paper_id") or ""),
    }
    limits = {
        "title": (300, 1),
        "abstract": (1200, 1),
        "domain": (200, 1),
        "paper_type": (200, 1),
        "research_question": (600, 1),
        "contributions": (500, 3),
        "method_inputs": (500, 1),
        "method_outputs": (500, 1),
        "method_assumptions": (500, 1),
        "conclusions": (500, 2),
        "conclusion_boundaries": (400, 2),
        "parse_warnings": (200, 5),
    }
    for name, (text_limit, list_limit) in limits.items():
        result[name] = _compact_grounded_summary(
            payload.get(name),
            text_limit=text_limit,
            list_limit=list_limit,
        )
    return result


def _claim_graph_summary(payload: dict[str, Any], core_ids: set[str]) -> dict[str, Any]:
    rows = [row for row in payload.get("claims", []) if isinstance(row, dict)]
    rows.sort(
        key=lambda row: (
            0 if str(row.get("claim_id") or "") in core_ids else 1,
            -float(row.get("centrality") or 0),
            str(row.get("claim_id") or ""),
        )
    )
    return {
        "schema_version": str(payload.get("schema_version") or ""),
        "paper_id": str(payload.get("paper_id") or ""),
        "claims": [
            {
                "claim_id": str(row.get("claim_id") or ""),
                "text": str(row.get("text") or "")[:360],
                "claim_type": str(row.get("claim_type") or ""),
                "centrality": float(row.get("centrality") or 0),
                "evidence_ids": _string_list(row.get("evidence_ids"), limit=4),
                "support_evidence_ids": _string_list(row.get("support_evidence_ids"), limit=4),
                "support_status": str(row.get("support_status") or ""),
            }
            for row in rows[:8]
        ],
    }


def _experiment_inventory_summary(payload: dict[str, Any]) -> dict[str, Any]:
    field_names = (
        "label",
        "datasets",
        "sample_sizes",
        "data_splits",
        "baselines",
        "metrics",
        "random_seeds",
        "statistics",
        "ablations",
        "key_figures",
        "key_tables",
    )
    rows = [row for row in payload.get("experiments", []) if isinstance(row, dict)]
    rows.sort(key=lambda row: -float(row.get("importance_score") or 0))
    experiments: list[dict[str, Any]] = []
    for row in rows[:4]:
        summary: dict[str, Any] = {
            "experiment_id": str(row.get("experiment_id") or ""),
            "importance_score": float(row.get("importance_score") or 0),
        }
        for name in field_names:
            summary[name] = _compact_grounded_summary(
                row.get(name),
                text_limit=140,
                list_limit=1,
                evidence_limit=3,
            )
        experiments.append(summary)
    return {
        "schema_version": str(payload.get("schema_version") or ""),
        "paper_id": str(payload.get("paper_id") or ""),
        "experiments": experiments,
    }


def _review_plan_summary(payload: dict[str, Any]) -> dict[str, Any]:
    route = [row for row in payload.get("reading_route", []) if isinstance(row, dict)]
    route.sort(key=lambda row: int(row.get("rank") or 10**9))
    assignments = [row for row in payload.get("agent_assignments", []) if isinstance(row, dict)]
    return {
        "schema_version": str(payload.get("schema_version") or ""),
        "paper_id": str(payload.get("paper_id") or ""),
        "core_claim_ids": _string_list(payload.get("core_claim_ids"), limit=5),
        "reading_route": [
            {
                "rank": int(row.get("rank") or 0),
                "priority": str(row.get("priority") or ""),
                "reason": str(row.get("reason") or "")[:160],
                "claim_ids": _string_list(row.get("claim_ids"), limit=4),
                "evidence_ids": _string_list(row.get("evidence_ids"), limit=6),
            }
            for row in route[:8]
        ],
        "agent_assignments": [
            {
                "agent_id": str(row.get("agent_id") or ""),
                "focus": str(row.get("focus") or "")[:160],
                "claim_ids": _string_list(row.get("claim_ids"), limit=4),
                "evidence_ids": _string_list(row.get("evidence_ids"), limit=8),
            }
            for row in assignments[:10]
        ],
    }


def _deterministic_summary(payload: dict[str, Any]) -> dict[str, Any]:
    rows = [row for row in payload.get("checks", []) if isinstance(row, dict)]
    rows.sort(
        key=lambda row: (
            0 if str(row.get("status") or "") in {"lead", "inconclusive", "failed_to_run"} else 1,
            str(row.get("id") or ""),
        )
    )
    return {
        "checks": [
            {
                "id": str(row.get("id") or ""),
                "kind": str(row.get("kind") or ""),
                "status": str(row.get("status") or ""),
                "applicability": str(row.get("applicability") or ""),
                "evidence_ids": _string_list(row.get("evidence_ids"), limit=6),
                "message": str(row.get("message") or "")[:360],
                "benign_explanations": _bounded_value(
                    row.get("benign_explanations"), text_limit=160, list_limit=3
                ),
            }
            for row in rows[:20]
        ]
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


def _block_key(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    block_id = str(metadata.get("block_id") or "").strip()
    return block_id or str(row.get("id") or "")


def _join_block_text(rows: list[dict[str, Any]]) -> str:
    text = ""
    for row in rows:
        part = str(row.get("text") or "").strip()
        if not part:
            continue
        if text.endswith("-") and part[:1].islower():
            text = text[:-1] + part
        else:
            text = f"{text} {part}".strip()
    return text


def _block_bbox(rows: list[dict[str, Any]]) -> list[float] | None:
    values = [
        row.get("bbox")
        for row in rows
        if isinstance(row.get("bbox"), list) and len(row["bbox"]) == 4
    ]
    if not values:
        return None
    return [
        min(float(value[0]) for value in values),
        min(float(value[1]) for value in values),
        max(float(value[2]) for value in values),
        max(float(value[3]) for value in values),
    ]


def _evidence_block_summary(rows: list[dict[str, Any]], *, max_chars: int) -> dict[str, Any]:
    row = rows[0]
    evidence_ids = [str(item.get("id") or "") for item in rows if str(item.get("id") or "")]
    text = _join_block_text(rows)[: max(0, max_chars)]
    return {
        "id": str(row.get("id") or ""),
        "evidence_ids": evidence_ids,
        "type": str(row.get("type") or ""),
        "page": row.get("page"),
        "locator": str(row.get("locator") or ""),
        "section": str(row.get("section") or ""),
        "text": text,
        "bbox": _block_bbox(rows),
    }

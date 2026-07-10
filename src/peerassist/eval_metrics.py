"""PeerAssist evaluation metric helpers."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any


def precision_recall(*, tp: int, fp: int, fn: int) -> dict[str, float]:
    precision_denom = tp + fp
    recall_denom = tp + fn
    return {
        "precision": (tp / precision_denom) if precision_denom else 0.0,
        "recall": (tp / recall_denom) if recall_denom else 0.0,
    }


def _has_evidence(row: dict[str, Any]) -> bool:
    evidence = row.get("evidence_ids")
    return isinstance(evidence, list) and bool(evidence)


def evidence_binding_rate(concerns: Iterable[dict[str, Any]]) -> float:
    rows = list(concerns)
    if not rows:
        return 0.0
    return sum(1 for row in rows if _has_evidence(row)) / len(rows)


def unevidenced_new_fact_rate(concerns: Iterable[dict[str, Any]]) -> float:
    rows = [
        row
        for row in concerns
        if str(row.get("status") or "").strip().lower() != "pending_human_confirmation"
    ]
    if not rows:
        return 0.0
    return sum(1 for row in rows if not _has_evidence(row)) / len(rows)


def stratified_mean(
    rows: Iterable[dict[str, Any]],
    *,
    metric: str,
    by: tuple[str, ...],
) -> dict[tuple[Any, ...], float]:
    grouped: dict[tuple[Any, ...], list[float]] = defaultdict(list)
    for row in rows:
        if metric not in row:
            continue
        key = tuple(row.get(name) for name in by)
        grouped[key].append(float(row[metric]))
    return {key: sum(values) / len(values) for key, values in grouped.items() if values}

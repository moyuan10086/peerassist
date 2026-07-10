"""Reviewer crossover experiment analysis for PeerAssist evaluation."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def analyze_reviewer_crossover(path: Path) -> dict[str, Any]:
    trials = _load_trials(Path(path))
    by_sample: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trial in trials:
        sample_id = str(trial.get("sample_id") or "").strip()
        if sample_id:
            by_sample[sample_id].append(trial)

    records: list[dict[str, float | str]] = []
    warnings: list[str] = []
    for sample_id in sorted(by_sample):
        sample_trials = by_sample[sample_id]
        baseline = [row for row in sample_trials if _condition(row) == "baseline"]
        assisted = [row for row in sample_trials if _condition(row) == "assisted"]
        if not baseline:
            warnings.append(f"{sample_id} missing baseline condition")
            continue
        if not assisted:
            warnings.append(f"{sample_id} missing assisted condition")
            continue

        record = {
            "sample_id": sample_id,
            "mechanical_baseline_minutes": _mean(_mechanical_and_evidence_minutes(row) for row in baseline),
            "mechanical_assisted_minutes": _mean(_mechanical_and_evidence_minutes(row) for row in assisted),
            "review_items_proposed": sum(_number(row, "review_items_proposed") for row in assisted),
            "review_items_retained": sum(_number(row, "review_items_retained") for row in assisted),
            "baseline_core_recall": _mean(_core_recall(row) for row in baseline),
            "assisted_core_recall": _mean(_core_recall(row) for row in assisted),
        }
        records.append(record)

    aggregate = {
        "mechanical_time_reduction": 1.0
        - _rate(
            sum(float(row["mechanical_assisted_minutes"]) for row in records),
            sum(float(row["mechanical_baseline_minutes"]) for row in records),
        ),
        "review_retention_rate": _rate(
            sum(float(row["review_items_retained"]) for row in records),
            sum(float(row["review_items_proposed"]) for row in records),
        ),
        "core_problem_recall_delta": _mean(
            float(row["assisted_core_recall"]) - float(row["baseline_core_recall"])
            for row in records
        ),
    }
    return {
        "schema_version": "peerassist.reviewer_crossover_report.v1",
        "records": records,
        "aggregate": aggregate,
        "warnings": warnings,
    }


def _load_trials(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        rows: list[dict[str, Any]] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
        return rows

    payload = json.loads(text)
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        trials = payload.get("trials")
        if isinstance(trials, list):
            return [row for row in trials if isinstance(row, dict)]
    raise ValueError("reviewer crossover input must be JSONL, a JSON array, or an object with trials")


def _condition(row: dict[str, Any]) -> str:
    return str(row.get("condition") or "").strip().lower()


def _mechanical_and_evidence_minutes(row: dict[str, Any]) -> float:
    return _number(row, "mechanical_minutes") + _number(row, "evidence_location_minutes")


def _core_recall(row: dict[str, Any]) -> float:
    gold = {
        str(item)
        for item in row.get("gold_core_concerns", [])
        if str(item).strip()
    }
    if not gold:
        return 0.0
    found = {
        str(item)
        for item in row.get("core_concerns_found", [])
        if str(item).strip()
    }
    return len(gold & found) / len(gold)


def _mean(values: Any) -> float:
    rows = [float(value) for value in values]
    if not rows:
        return 0.0
    return sum(rows) / len(rows)


def _rate(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _number(row: dict[str, Any], key: str) -> float:
    value = row.get(key, 0)
    if value in (None, ""):
        return 0.0
    return float(value)

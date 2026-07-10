"""Build per-sample PeerAssist eval records from run artifacts and gold labels."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from common.pipeline_context import peerassist_stage_dir, read_json_file


def build_eval_record_from_artifacts(
    *, sample_id: str, run_dir: Path, gold_path: Path | None = None
) -> dict[str, Any]:
    out_dir = peerassist_stage_dir(run_dir)
    gold = read_json_file(gold_path) if gold_path else {}
    runtime = read_json_file(out_dir / "peerassist_eval_runtime.json")
    report = read_json_file(out_dir / "peerassist_report.json")
    checks_payload = read_json_file(out_dir / "deterministic_checks.json")

    concerns = _rows(report.get("concerns"))
    checks = _rows(checks_payload.get("checks"))
    gold_concerns = _rows(gold.get("gold_concerns"))
    deterministic_errors = _rows(gold.get("deterministic_errors"))
    evidence_checks = _rows(gold.get("evidence_checks"))
    crossover = gold.get("reviewer_crossover") if isinstance(gold.get("reviewer_crossover"), dict) else {}

    deterministic_leads = {
        str(check.get("id"))
        for check in checks
        if str(check.get("status") or "").strip().lower() == "lead" and check.get("id")
    }
    gold_error_ids = {
        str(row.get("check_id"))
        for row in deterministic_errors
        if row.get("check_id")
    }
    deterministic_tp = len(deterministic_leads & gold_error_ids)
    deterministic_fp = len(deterministic_leads - gold_error_ids)
    deterministic_fn = len(gold_error_ids - deterministic_leads)

    covered_gold = sum(1 for row in gold_concerns if _gold_concern_covered(row, concerns))
    active_non_pending = [
        concern
        for concern in concerns
        if str(concern.get("status") or "").strip().lower() != "pending_human_confirmation"
    ]
    unevidenced = [
        concern
        for concern in active_non_pending
        if not _nonempty_list(concern.get("evidence_ids"))
    ]

    record = {
        "sample_id": sample_id,
        "mode": str(runtime.get("mode") or "fast"),
        "parse_success": bool(runtime.get("parse_success", _parse_success_from_artifacts(out_dir))),
        "evidence_faithful": sum(1 for row in evidence_checks if bool(row.get("faithful"))),
        "evidence_total": len(evidence_checks),
        "deterministic_tp": deterministic_tp,
        "deterministic_fp": deterministic_fp,
        "deterministic_fn": deterministic_fn,
        "gold_concerns_total": len(gold_concerns),
        "gold_concerns_covered": covered_gold,
        "unevidenced_new_facts": len(unevidenced),
        "new_facts_total": len(active_non_pending),
        "latency_seconds": _number(runtime, "latency_seconds"),
        "mechanical_baseline_minutes": _number(crossover, "mechanical_baseline_minutes"),
        "mechanical_assisted_minutes": _number(crossover, "mechanical_assisted_minutes"),
        "review_items_proposed": _number(crossover, "review_items_proposed"),
        "review_items_retained": _number(crossover, "review_items_retained"),
        "baseline_core_recall": _number(crossover, "baseline_core_recall"),
        "assisted_core_recall": _number(crossover, "assisted_core_recall"),
    }
    return record


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _nonempty_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value)


def _gold_concern_covered(gold_row: dict[str, Any], concerns: list[dict[str, Any]]) -> bool:
    covered_by = {
        str(item)
        for item in gold_row.get("covered_by", [])
        if str(item).strip()
    }
    if not covered_by:
        return False
    active_ids = {
        str(concern.get("id"))
        for concern in concerns
        if str(concern.get("status") or "").strip().lower() != "deleted"
    }
    return bool(covered_by & active_ids)


def _parse_success_from_artifacts(out_dir: Path) -> bool:
    return (out_dir / "evidence_ledger.json").exists()


def _number(row: dict[str, Any], key: str) -> float:
    value = row.get(key, 0)
    if value in (None, ""):
        return 0.0
    return float(value)

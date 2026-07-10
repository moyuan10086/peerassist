"""PeerAssist-Eval-v1 manifest and aggregate metric harness."""

from __future__ import annotations

import json
from pathlib import Path
from statistics import median
from typing import Any

from peerassist.eval_metrics import precision_recall

DATASET_NAME = "PeerAssist-Eval-v1"
MANIFEST_SCHEMA_VERSION = "peerassist.eval_manifest.v1"

TARGETS = {
    "document_parse_success_rate": 0.95,
    "evidence_faithfulness_rate": 0.97,
    "deterministic_precision": 0.95,
    "deterministic_recall": 0.85,
    "gold_concern_coverage": 0.70,
    "unevidenced_new_fact_rate": 0.03,
    "fast_median_latency_seconds": 300.0,
    "mechanical_time_reduction": 0.40,
    "review_retention_rate": 0.60,
    "core_problem_recall_delta": 0.0,
}


def load_eval_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError(f"unsupported PeerAssist eval manifest schema: {payload.get('schema_version')}")
    if payload.get("dataset") != DATASET_NAME:
        raise ValueError(f"unsupported PeerAssist eval dataset: {payload.get('dataset')}")

    policy = payload.get("policy") if isinstance(payload.get("policy"), dict) else {}
    freeze_policy_ok = all(
        bool(policy.get(name))
        for name in (
            "frozen_samples_must_not_enter_prompts",
            "frozen_samples_must_not_enter_indexes",
            "frozen_samples_must_not_enter_finetuning",
        )
    )
    samples = payload.get("samples") if isinstance(payload.get("samples"), list) else []
    sample_ids = [str(row.get("sample_id")) for row in samples if isinstance(row, dict) and row.get("sample_id")]
    duplicate_sample_ids = sorted(
        sample_id for sample_id in set(sample_ids) if sample_ids.count(sample_id) > 1
    )
    missing_sha256_sample_ids = sorted(
        str(row.get("sample_id"))
        for row in samples
        if isinstance(row, dict)
        and row.get("sample_id")
        and not str(row.get("sha256") or "").strip()
    )
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "dataset": DATASET_NAME,
        "path": str(path),
        "sample_count": len(sample_ids),
        "sample_ids": sample_ids,
        "duplicate_sample_ids": duplicate_sample_ids,
        "missing_sha256_sample_ids": missing_sha256_sample_ids,
        "integrity_ok": not duplicate_sample_ids and not missing_sha256_sample_ids,
        "freeze_policy_ok": freeze_policy_ok,
        "policy": policy,
        "samples": samples,
    }


def evaluate_peerassist_records(
    *, manifest_path: Path, records: list[dict[str, Any]]
) -> dict[str, Any]:
    manifest = load_eval_manifest(manifest_path)
    manifest_sample_ids = set(manifest["sample_ids"])
    record_sample_id_list = [
        str(row.get("sample_id"))
        for row in records
        if row.get("sample_id")
    ]
    record_sample_ids = {
        sample_id for sample_id in record_sample_id_list
    }
    record_counts = {
        sample_id: record_sample_id_list.count(sample_id)
        for sample_id in record_sample_ids
    }
    duplicate_sample_ids = sorted(
        sample_id for sample_id, count in record_counts.items() if count > 1
    )
    unknown_sample_ids = sorted(
        {
            str(row.get("sample_id"))
            for row in records
            if row.get("sample_id") and str(row.get("sample_id")) not in manifest_sample_ids
        }
    )
    missing_sample_ids = sorted(manifest_sample_ids - record_sample_ids)
    record_coverage_ok = not missing_sample_ids and not unknown_sample_ids and not duplicate_sample_ids

    metrics = _compute_metrics(records)
    targets = _target_results(
        metrics,
        freeze_policy_ok=bool(manifest["freeze_policy_ok"]),
        manifest_integrity_ok=bool(manifest["integrity_ok"]),
        record_coverage_ok=record_coverage_ok,
    )
    return {
        "schema_version": "peerassist.eval_report.v1",
        "dataset": DATASET_NAME,
        "manifest_path": str(manifest_path),
        "sample_count": len(records),
        "manifest_sample_count": manifest["sample_count"],
        "manifest_duplicate_sample_ids": manifest["duplicate_sample_ids"],
        "manifest_missing_sha256_sample_ids": manifest["missing_sha256_sample_ids"],
        "missing_record_sample_ids": missing_sample_ids,
        "unknown_record_sample_ids": unknown_sample_ids,
        "duplicate_record_sample_ids": duplicate_sample_ids,
        "metrics": metrics,
        "stratified_metrics": _stratified_metrics(records=records, manifest_samples=manifest["samples"]),
        "targets": targets,
    }


def _compute_metrics(records: list[dict[str, Any]]) -> dict[str, float]:
    parse_success_rate = _rate(
        sum(1 for row in records if bool(row.get("parse_success"))),
        len(records),
    )
    evidence_faithfulness_rate = _rate(
        sum(_number(row, "evidence_faithful") for row in records),
        sum(_number(row, "evidence_total") for row in records),
    )
    pr = precision_recall(
        tp=sum(_number(row, "deterministic_tp") for row in records),
        fp=sum(_number(row, "deterministic_fp") for row in records),
        fn=sum(_number(row, "deterministic_fn") for row in records),
    )
    gold_concern_coverage = _rate(
        sum(_number(row, "gold_concerns_covered") for row in records),
        sum(_number(row, "gold_concerns_total") for row in records),
    )
    unevidenced_new_fact_rate = _rate(
        sum(_number(row, "unevidenced_new_facts") for row in records),
        sum(_number(row, "new_facts_total") for row in records),
    )
    fast_latencies = [
        float(row["latency_seconds"])
        for row in records
        if str(row.get("mode") or "").lower() == "fast" and row.get("latency_seconds") is not None
    ]
    mechanical_time_reduction = 1.0 - _rate(
        sum(_number(row, "mechanical_assisted_minutes") for row in records),
        sum(_number(row, "mechanical_baseline_minutes") for row in records),
    )
    review_retention_rate = _rate(
        sum(_number(row, "review_items_retained") for row in records),
        sum(_number(row, "review_items_proposed") for row in records),
    )
    core_problem_recall_delta = _mean_delta(
        records,
        after="assisted_core_recall",
        before="baseline_core_recall",
    )
    return {
        "document_parse_success_rate": parse_success_rate,
        "evidence_faithfulness_rate": evidence_faithfulness_rate,
        "deterministic_precision": pr["precision"],
        "deterministic_recall": pr["recall"],
        "gold_concern_coverage": gold_concern_coverage,
        "unevidenced_new_fact_rate": unevidenced_new_fact_rate,
        "fast_median_latency_seconds": median(fast_latencies) if fast_latencies else 0.0,
        "mechanical_time_reduction": mechanical_time_reduction,
        "review_retention_rate": review_retention_rate,
        "core_problem_recall_delta": core_problem_recall_delta,
    }


def _stratified_metrics(
    *, records: list[dict[str, Any]], manifest_samples: list[Any]
) -> dict[str, dict[str, dict[str, Any]]]:
    sample_lookup = {
        str(row.get("sample_id")): row
        for row in manifest_samples
        if isinstance(row, dict) and row.get("sample_id")
    }
    dimensions: dict[str, dict[str, list[dict[str, Any]]]] = {
        "domain": {},
        "pdf_type": {},
        "problem_category": {},
    }
    sample_ids_by_group: dict[str, dict[str, list[str]]] = {
        "domain": {},
        "pdf_type": {},
        "problem_category": {},
    }

    for record in records:
        sample_id = str(record.get("sample_id") or "")
        sample = sample_lookup.get(sample_id, {})
        assignments = {
            "domain": [_clean_group_value(sample.get("domain"), default="unknown_domain")],
            "pdf_type": [_clean_group_value(sample.get("pdf_type"), default="unknown_pdf_type")],
            "problem_category": _problem_categories(sample),
        }
        for dimension, values in assignments.items():
            for value in values:
                dimensions[dimension].setdefault(value, []).append(record)
                sample_ids_by_group[dimension].setdefault(value, []).append(sample_id)

    result: dict[str, dict[str, dict[str, Any]]] = {}
    for dimension, groups in dimensions.items():
        result[dimension] = {}
        for value in sorted(groups):
            group_records = groups[value]
            result[dimension][value] = {
                "sample_count": len(group_records),
                "sample_ids": sample_ids_by_group[dimension].get(value, []),
                "metrics": _compute_metrics(group_records),
            }
    return result


def _target_results(
    metrics: dict[str, float],
    *,
    freeze_policy_ok: bool,
    manifest_integrity_ok: bool,
    record_coverage_ok: bool,
) -> dict[str, Any]:
    results = {
        "freeze_policy_ok": freeze_policy_ok,
        "manifest_integrity_ok": manifest_integrity_ok,
        "record_coverage_ok": record_coverage_ok,
        "document_parse_success_rate": metrics["document_parse_success_rate"]
        >= TARGETS["document_parse_success_rate"],
        "evidence_faithfulness_rate": metrics["evidence_faithfulness_rate"]
        >= TARGETS["evidence_faithfulness_rate"],
        "deterministic_precision": metrics["deterministic_precision"]
        >= TARGETS["deterministic_precision"],
        "deterministic_recall": metrics["deterministic_recall"] >= TARGETS["deterministic_recall"],
        "gold_concern_coverage": metrics["gold_concern_coverage"] >= TARGETS["gold_concern_coverage"],
        "unevidenced_new_fact_rate": metrics["unevidenced_new_fact_rate"]
        <= TARGETS["unevidenced_new_fact_rate"],
        "fast_median_latency_seconds": metrics["fast_median_latency_seconds"]
        <= TARGETS["fast_median_latency_seconds"],
        "mechanical_time_reduction": metrics["mechanical_time_reduction"]
        >= TARGETS["mechanical_time_reduction"],
        "review_retention_rate": metrics["review_retention_rate"] >= TARGETS["review_retention_rate"],
        "core_problem_recall_delta": metrics["core_problem_recall_delta"]
        >= TARGETS["core_problem_recall_delta"],
    }
    results["all_targets_passed"] = all(bool(value) for value in results.values())
    results["thresholds"] = dict(TARGETS)
    return results


def _rate(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _clean_group_value(value: Any, *, default: str) -> str:
    token = str(value or "").strip()
    return token if token else default


def _problem_categories(sample: dict[str, Any]) -> list[str]:
    raw = sample.get("problem_categories")
    if isinstance(raw, list):
        values = [
            _clean_group_value(item, default="")
            for item in raw
            if _clean_group_value(item, default="")
        ]
        return values or ["unknown_problem_category"]
    value = _clean_group_value(sample.get("problem_category"), default="")
    return [value] if value else ["unknown_problem_category"]


def _number(row: dict[str, Any], key: str) -> float:
    value = row.get(key, 0)
    if value in (None, ""):
        return 0.0
    return float(value)


def _mean_delta(records: list[dict[str, Any]], *, after: str, before: str) -> float:
    rows = [row for row in records if row.get(after) is not None and row.get(before) is not None]
    if not rows:
        return 0.0
    after_mean = sum(_number(row, after) for row in rows) / len(rows)
    before_mean = sum(_number(row, before) for row in rows) / len(rows)
    return round(after_mean - before_mean, 12)

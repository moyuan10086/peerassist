from __future__ import annotations

from common.pipeline_context import write_json_file
from peerassist.eval_harness import evaluate_peerassist_records, load_eval_manifest


def test_load_eval_manifest_validates_dataset_and_freeze_policy(tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    write_json_file(
        manifest_path,
        {
            "schema_version": "peerassist.eval_manifest.v1",
            "dataset": "PeerAssist-Eval-v1",
            "policy": {
                "frozen_samples_must_not_enter_prompts": True,
                "frozen_samples_must_not_enter_indexes": True,
                "frozen_samples_must_not_enter_finetuning": True,
            },
            "samples": [
                {
                    "sample_id": "paper-001",
                    "split": "frozen",
                    "sha256": "abc",
                    "domain": "cs",
                    "pdf_type": "text_pdf",
                }
            ],
        },
    )

    manifest = load_eval_manifest(manifest_path)

    assert manifest["dataset"] == "PeerAssist-Eval-v1"
    assert manifest["sample_ids"] == ["paper-001"]
    assert manifest["freeze_policy_ok"] is True


def test_evaluate_peerassist_records_reports_target_gate_metrics(tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    write_json_file(
        manifest_path,
        {
            "schema_version": "peerassist.eval_manifest.v1",
            "dataset": "PeerAssist-Eval-v1",
            "policy": {
                "frozen_samples_must_not_enter_prompts": True,
                "frozen_samples_must_not_enter_indexes": True,
                "frozen_samples_must_not_enter_finetuning": True,
            },
            "samples": [
                {"sample_id": "paper-001", "split": "frozen", "sha256": "abc"},
                {"sample_id": "paper-002", "split": "frozen", "sha256": "def"},
            ],
        },
    )
    records = [
        {
            "sample_id": "paper-001",
            "mode": "fast",
            "parse_success": True,
            "evidence_faithful": 98,
            "evidence_total": 100,
            "deterministic_tp": 10,
            "deterministic_fp": 0,
            "deterministic_fn": 1,
            "gold_concerns_total": 10,
            "gold_concerns_covered": 8,
            "unevidenced_new_facts": 1,
            "new_facts_total": 100,
            "latency_seconds": 240,
            "mechanical_baseline_minutes": 50,
            "mechanical_assisted_minutes": 25,
            "review_items_proposed": 10,
            "review_items_retained": 7,
            "baseline_core_recall": 0.75,
            "assisted_core_recall": 0.75,
        },
        {
            "sample_id": "paper-002",
            "mode": "fast",
            "parse_success": True,
            "evidence_faithful": 99,
            "evidence_total": 100,
            "deterministic_tp": 9,
            "deterministic_fp": 1,
            "deterministic_fn": 2,
            "gold_concerns_total": 10,
            "gold_concerns_covered": 7,
            "unevidenced_new_facts": 2,
            "new_facts_total": 100,
            "latency_seconds": 260,
            "mechanical_baseline_minutes": 40,
            "mechanical_assisted_minutes": 20,
            "review_items_proposed": 10,
            "review_items_retained": 6,
            "baseline_core_recall": 0.80,
            "assisted_core_recall": 0.82,
        },
    ]

    result = evaluate_peerassist_records(manifest_path=manifest_path, records=records)

    metrics = result["metrics"]
    assert metrics["document_parse_success_rate"] == 1.0
    assert metrics["evidence_faithfulness_rate"] == 197 / 200
    assert metrics["deterministic_precision"] == 19 / 20
    assert metrics["deterministic_recall"] == 19 / 22
    assert metrics["gold_concern_coverage"] == 15 / 20
    assert metrics["unevidenced_new_fact_rate"] == 3 / 200
    assert metrics["fast_median_latency_seconds"] == 250
    assert metrics["mechanical_time_reduction"] == 0.5
    assert metrics["review_retention_rate"] == 13 / 20
    assert metrics["core_problem_recall_delta"] == 0.01
    assert result["targets"]["all_targets_passed"] is True


def test_evaluate_peerassist_records_reports_stratified_metrics(tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    write_json_file(
        manifest_path,
        {
            "schema_version": "peerassist.eval_manifest.v1",
            "dataset": "PeerAssist-Eval-v1",
            "policy": {
                "frozen_samples_must_not_enter_prompts": True,
                "frozen_samples_must_not_enter_indexes": True,
                "frozen_samples_must_not_enter_finetuning": True,
            },
            "samples": [
                {
                    "sample_id": "paper-001",
                    "split": "frozen",
                    "sha256": "abc",
                    "domain": "computer_science",
                    "pdf_type": "text_pdf",
                    "problem_categories": ["statistics", "layout"],
                },
                {
                    "sample_id": "paper-002",
                    "split": "frozen",
                    "sha256": "def",
                    "domain": "mathematics",
                    "pdf_type": "scanned_pdf",
                    "problem_categories": ["citation"],
                },
            ],
        },
    )
    records = [
        {
            "sample_id": "paper-001",
            "mode": "fast",
            "parse_success": True,
            "evidence_faithful": 10,
            "evidence_total": 10,
            "deterministic_tp": 4,
            "deterministic_fp": 0,
            "deterministic_fn": 1,
            "gold_concerns_total": 4,
            "gold_concerns_covered": 3,
            "unevidenced_new_facts": 0,
            "new_facts_total": 5,
            "latency_seconds": 120,
        },
        {
            "sample_id": "paper-002",
            "mode": "fast",
            "parse_success": False,
            "evidence_faithful": 7,
            "evidence_total": 10,
            "deterministic_tp": 1,
            "deterministic_fp": 2,
            "deterministic_fn": 3,
            "gold_concerns_total": 4,
            "gold_concerns_covered": 1,
            "unevidenced_new_facts": 2,
            "new_facts_total": 5,
            "latency_seconds": 420,
        },
    ]

    result = evaluate_peerassist_records(manifest_path=manifest_path, records=records)

    strata = result["stratified_metrics"]
    assert strata["domain"]["computer_science"]["sample_count"] == 1
    assert strata["domain"]["computer_science"]["metrics"]["document_parse_success_rate"] == 1.0
    assert strata["domain"]["mathematics"]["metrics"]["document_parse_success_rate"] == 0.0
    assert strata["pdf_type"]["scanned_pdf"]["sample_ids"] == ["paper-002"]
    assert strata["pdf_type"]["scanned_pdf"]["metrics"]["evidence_faithfulness_rate"] == 0.7
    assert strata["problem_category"]["statistics"]["sample_ids"] == ["paper-001"]
    assert strata["problem_category"]["layout"]["sample_ids"] == ["paper-001"]
    assert strata["problem_category"]["citation"]["metrics"]["deterministic_precision"] == 1 / 3


def test_evaluate_peerassist_records_requires_complete_manifest_sample_coverage(tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    write_json_file(
        manifest_path,
        {
            "schema_version": "peerassist.eval_manifest.v1",
            "dataset": "PeerAssist-Eval-v1",
            "policy": {
                "frozen_samples_must_not_enter_prompts": True,
                "frozen_samples_must_not_enter_indexes": True,
                "frozen_samples_must_not_enter_finetuning": True,
            },
            "samples": [
                {"sample_id": "paper-001", "split": "frozen", "sha256": "abc"},
                {"sample_id": "paper-002", "split": "frozen", "sha256": "def"},
            ],
        },
    )
    records = [
        {
            "sample_id": "paper-001",
            "mode": "fast",
            "parse_success": True,
            "evidence_faithful": 100,
            "evidence_total": 100,
            "deterministic_tp": 10,
            "deterministic_fp": 0,
            "deterministic_fn": 0,
            "gold_concerns_total": 10,
            "gold_concerns_covered": 10,
            "unevidenced_new_facts": 0,
            "new_facts_total": 100,
            "latency_seconds": 120,
            "mechanical_baseline_minutes": 50,
            "mechanical_assisted_minutes": 20,
            "review_items_proposed": 10,
            "review_items_retained": 8,
            "baseline_core_recall": 0.80,
            "assisted_core_recall": 0.80,
        }
    ]

    result = evaluate_peerassist_records(manifest_path=manifest_path, records=records)

    assert result["missing_record_sample_ids"] == ["paper-002"]
    assert result["targets"]["record_coverage_ok"] is False
    assert result["targets"]["all_targets_passed"] is False


def test_evaluate_peerassist_records_rejects_duplicate_sample_records(tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    write_json_file(
        manifest_path,
        {
            "schema_version": "peerassist.eval_manifest.v1",
            "dataset": "PeerAssist-Eval-v1",
            "policy": {
                "frozen_samples_must_not_enter_prompts": True,
                "frozen_samples_must_not_enter_indexes": True,
                "frozen_samples_must_not_enter_finetuning": True,
            },
            "samples": [
                {"sample_id": "paper-001", "split": "frozen", "sha256": "abc"},
                {"sample_id": "paper-002", "split": "frozen", "sha256": "def"},
            ],
        },
    )
    base_record = {
        "mode": "fast",
        "parse_success": True,
        "evidence_faithful": 100,
        "evidence_total": 100,
        "deterministic_tp": 10,
        "deterministic_fp": 0,
        "deterministic_fn": 0,
        "gold_concerns_total": 10,
        "gold_concerns_covered": 10,
        "unevidenced_new_facts": 0,
        "new_facts_total": 100,
        "latency_seconds": 120,
        "mechanical_baseline_minutes": 50,
        "mechanical_assisted_minutes": 20,
        "review_items_proposed": 10,
        "review_items_retained": 8,
        "baseline_core_recall": 0.80,
        "assisted_core_recall": 0.80,
    }
    records = [
        {"sample_id": "paper-001", **base_record},
        {"sample_id": "paper-001", **base_record},
        {"sample_id": "paper-002", **base_record},
    ]

    result = evaluate_peerassist_records(manifest_path=manifest_path, records=records)

    assert result["duplicate_record_sample_ids"] == ["paper-001"]
    assert result["targets"]["record_coverage_ok"] is False
    assert result["targets"]["all_targets_passed"] is False


def test_evaluate_peerassist_records_rejects_manifest_with_duplicate_or_unhashed_samples(tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    write_json_file(
        manifest_path,
        {
            "schema_version": "peerassist.eval_manifest.v1",
            "dataset": "PeerAssist-Eval-v1",
            "policy": {
                "frozen_samples_must_not_enter_prompts": True,
                "frozen_samples_must_not_enter_indexes": True,
                "frozen_samples_must_not_enter_finetuning": True,
            },
            "samples": [
                {"sample_id": "paper-001", "split": "frozen", "sha256": "abc"},
                {"sample_id": "paper-001", "split": "frozen", "sha256": "abc"},
                {"sample_id": "paper-002", "split": "frozen"},
            ],
        },
    )
    base_record = {
        "mode": "fast",
        "parse_success": True,
        "evidence_faithful": 100,
        "evidence_total": 100,
        "deterministic_tp": 10,
        "deterministic_fp": 0,
        "deterministic_fn": 0,
        "gold_concerns_total": 10,
        "gold_concerns_covered": 10,
        "unevidenced_new_facts": 0,
        "new_facts_total": 100,
        "latency_seconds": 120,
        "mechanical_baseline_minutes": 50,
        "mechanical_assisted_minutes": 20,
        "review_items_proposed": 10,
        "review_items_retained": 8,
        "baseline_core_recall": 0.80,
        "assisted_core_recall": 0.80,
    }
    records = [
        {"sample_id": "paper-001", **base_record},
        {"sample_id": "paper-002", **base_record},
    ]

    result = evaluate_peerassist_records(manifest_path=manifest_path, records=records)

    assert result["manifest_duplicate_sample_ids"] == ["paper-001"]
    assert result["manifest_missing_sha256_sample_ids"] == ["paper-002"]
    assert result["targets"]["manifest_integrity_ok"] is False
    assert result["targets"]["all_targets_passed"] is False

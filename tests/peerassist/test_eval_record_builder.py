from __future__ import annotations

from pathlib import Path

from common.pipeline_context import init_full_pipeline_context, peerassist_stage_dir, write_json_file
from peerassist.eval_record_builder import build_eval_record_from_artifacts


def _seed_stage(run_dir: Path) -> Path:
    init_full_pipeline_context(run_dir=run_dir)
    out_dir = peerassist_stage_dir(run_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json_file(out_dir / "evidence_ledger.json", {"items": [{"id": "P01-L001"}]})
    write_json_file(
        out_dir / "deterministic_checks.json",
        {
            "checks": [
                {
                    "id": "check_percentage_consistency_001",
                    "status": "lead",
                    "evidence_ids": ["P01-L001"],
                },
                {
                    "id": "check_percentage_consistency_002",
                    "status": "lead",
                    "evidence_ids": ["P01-L002"],
                },
                {
                    "id": "check_percentage_consistency_003",
                    "status": "pass",
                    "evidence_ids": ["P01-L003"],
                },
            ]
        },
    )
    write_json_file(
        out_dir / "peerassist_report.json",
        {
            "concerns": [
                {
                    "id": "concern_check_percentage_consistency_001",
                    "status": "confirmed",
                    "evidence_ids": ["P01-L001"],
                    "source_check_ids": ["check_percentage_consistency_001"],
                },
                {
                    "id": "concern_manual_without_evidence",
                    "status": "confirmed",
                    "evidence_ids": [],
                    "source_check_ids": [],
                },
                {
                    "id": "concern_pending_manual",
                    "status": "pending_human_confirmation",
                    "evidence_ids": [],
                    "source_check_ids": [],
                },
            ],
        },
    )
    write_json_file(
        out_dir / "peerassist_eval_runtime.json",
        {"mode": "fast", "latency_seconds": 245, "parse_success": True},
    )
    return out_dir


def test_build_eval_record_from_artifacts_counts_gold_and_runtime_metrics(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _seed_stage(run_dir)
    gold_path = tmp_path / "gold.json"
    write_json_file(
        gold_path,
        {
            "sample_id": "paper-001",
            "gold_concerns": [
                {"id": "gold-1", "covered_by": ["concern_check_percentage_consistency_001"]},
                {"id": "gold-2", "covered_by": []},
            ],
            "deterministic_errors": [
                {"check_id": "check_percentage_consistency_001"},
                {"check_id": "check_percentage_consistency_004"},
            ],
            "evidence_checks": [
                {"concern_id": "concern_check_percentage_consistency_001", "faithful": True},
                {"concern_id": "concern_manual_without_evidence", "faithful": False},
            ],
            "reviewer_crossover": {
                "mechanical_baseline_minutes": 50,
                "mechanical_assisted_minutes": 25,
                "review_items_proposed": 2,
                "review_items_retained": 1,
                "baseline_core_recall": 0.75,
                "assisted_core_recall": 0.76,
            },
        },
    )

    record = build_eval_record_from_artifacts(
        sample_id="paper-001",
        run_dir=run_dir,
        gold_path=gold_path,
    )

    assert record["sample_id"] == "paper-001"
    assert record["mode"] == "fast"
    assert record["parse_success"] is True
    assert record["latency_seconds"] == 245
    assert record["evidence_faithful"] == 1
    assert record["evidence_total"] == 2
    assert record["deterministic_tp"] == 1
    assert record["deterministic_fp"] == 1
    assert record["deterministic_fn"] == 1
    assert record["gold_concerns_total"] == 2
    assert record["gold_concerns_covered"] == 1
    assert record["unevidenced_new_facts"] == 1
    assert record["new_facts_total"] == 2
    assert record["mechanical_baseline_minutes"] == 50
    assert record["mechanical_assisted_minutes"] == 25
    assert record["review_items_proposed"] == 2
    assert record["review_items_retained"] == 1
    assert record["baseline_core_recall"] == 0.75
    assert record["assisted_core_recall"] == 0.76

from __future__ import annotations

import json
from pathlib import Path

from common.pipeline_context import write_json_file
from peerassist.reviewer_crossover import analyze_reviewer_crossover


def test_analyze_reviewer_crossover_builds_eval_ready_sample_records(tmp_path: Path) -> None:
    input_path = tmp_path / "crossover.json"
    write_json_file(
        input_path,
        {
            "schema_version": "peerassist.reviewer_crossover.v1",
            "trials": [
                {
                    "sample_id": "paper-001",
                    "reviewer_id": "r1",
                    "condition": "baseline",
                    "mechanical_minutes": 30,
                    "evidence_location_minutes": 20,
                    "gold_core_concerns": ["c1", "c2"],
                    "core_concerns_found": ["c1"],
                },
                {
                    "sample_id": "paper-001",
                    "reviewer_id": "r1",
                    "condition": "assisted",
                    "mechanical_minutes": 12,
                    "evidence_location_minutes": 8,
                    "gold_core_concerns": ["c1", "c2"],
                    "core_concerns_found": ["c1", "c2"],
                    "review_items_proposed": 5,
                    "review_items_retained": 3,
                },
                {
                    "sample_id": "paper-001",
                    "reviewer_id": "r2",
                    "condition": "baseline",
                    "mechanical_minutes": 20,
                    "evidence_location_minutes": 20,
                    "gold_core_concerns": ["c1", "c2"],
                    "core_concerns_found": ["c1", "c2"],
                },
                {
                    "sample_id": "paper-001",
                    "reviewer_id": "r2",
                    "condition": "assisted",
                    "mechanical_minutes": 10,
                    "evidence_location_minutes": 10,
                    "gold_core_concerns": ["c1", "c2"],
                    "core_concerns_found": ["c1", "c2"],
                    "review_items_proposed": 5,
                    "review_items_retained": 4,
                },
            ],
        },
    )

    result = analyze_reviewer_crossover(input_path)

    assert result["schema_version"] == "peerassist.reviewer_crossover_report.v1"
    assert result["records"] == [
        {
            "sample_id": "paper-001",
            "mechanical_baseline_minutes": 45.0,
            "mechanical_assisted_minutes": 20.0,
            "review_items_proposed": 10.0,
            "review_items_retained": 7.0,
            "baseline_core_recall": 0.75,
            "assisted_core_recall": 1.0,
        }
    ]
    assert result["aggregate"]["mechanical_time_reduction"] == 1.0 - 20.0 / 45.0
    assert result["aggregate"]["review_retention_rate"] == 0.7
    assert result["aggregate"]["core_problem_recall_delta"] == 0.25
    assert result["warnings"] == []


def test_analyze_reviewer_crossover_records_missing_condition_warnings(tmp_path: Path) -> None:
    input_path = tmp_path / "crossover.jsonl"
    input_path.write_text(
        json.dumps(
            {
                "sample_id": "paper-001",
                "reviewer_id": "r1",
                "condition": "assisted",
                "mechanical_minutes": 10,
                "evidence_location_minutes": 5,
                "gold_core_concerns": ["c1"],
                "core_concerns_found": ["c1"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = analyze_reviewer_crossover(input_path)

    assert result["records"] == []
    assert result["warnings"] == ["paper-001 missing baseline condition"]


def test_analyze_reviewer_crossover_flags_paired_core_recall_regressions(tmp_path: Path) -> None:
    input_path = tmp_path / "crossover.json"
    write_json_file(
        input_path,
        {
            "schema_version": "peerassist.reviewer_crossover.v1",
            "trials": [
                {
                    "sample_id": "paper-001",
                    "reviewer_id": "r1",
                    "condition": "baseline",
                    "mechanical_minutes": 20,
                    "evidence_location_minutes": 10,
                    "gold_core_concerns": ["c1", "c2"],
                    "core_concerns_found": ["c1", "c2"],
                },
                {
                    "sample_id": "paper-001",
                    "reviewer_id": "r1",
                    "condition": "assisted",
                    "mechanical_minutes": 10,
                    "evidence_location_minutes": 5,
                    "gold_core_concerns": ["c1", "c2"],
                    "core_concerns_found": ["c1"],
                    "review_items_proposed": 3,
                    "review_items_retained": 2,
                },
            ],
        },
    )

    result = analyze_reviewer_crossover(input_path)

    assert result["paired_reviewer_records"] == [
        {
            "sample_id": "paper-001",
            "reviewer_id": "r1",
            "baseline_core_recall": 1.0,
            "assisted_core_recall": 0.5,
            "core_problem_recall_delta": -0.5,
            "core_recall_regressed": True,
        }
    ]
    assert result["recall_regression_warnings"] == [
        "paper-001/r1 assisted core recall lower than baseline: 0.500 < 1.000"
    ]

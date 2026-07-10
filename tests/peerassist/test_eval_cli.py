from __future__ import annotations

import json
import tomllib
from pathlib import Path

from common.pipeline_context import write_json_file
from peerassist.eval_cli import load_eval_records, main as eval_cli_main
from tests.peerassist.test_eval_record_builder import _seed_stage


def _manifest(path: Path) -> None:
    write_json_file(
        path,
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


def _passing_record(sample_id: str, latency_seconds: int) -> dict[str, object]:
    return {
        "sample_id": sample_id,
        "mode": "fast",
        "parse_success": True,
        "evidence_faithful": 99,
        "evidence_total": 100,
        "deterministic_tp": 10,
        "deterministic_fp": 0,
        "deterministic_fn": 1,
        "gold_concerns_total": 10,
        "gold_concerns_covered": 8,
        "unevidenced_new_facts": 1,
        "new_facts_total": 100,
        "latency_seconds": latency_seconds,
        "mechanical_baseline_minutes": 50,
        "mechanical_assisted_minutes": 25,
        "review_items_proposed": 10,
        "review_items_retained": 7,
        "baseline_core_recall": 0.75,
        "assisted_core_recall": 0.76,
    }


def test_load_eval_records_accepts_jsonl_and_json_array(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "records.jsonl"
    jsonl_path.write_text(
        json.dumps(_passing_record("paper-001", 240)) + "\n"
        + json.dumps(_passing_record("paper-002", 260)) + "\n",
        encoding="utf-8",
    )
    json_path = tmp_path / "records.json"
    json_path.write_text(
        json.dumps({"records": [_passing_record("paper-001", 240)]}),
        encoding="utf-8",
    )

    assert len(load_eval_records(jsonl_path)) == 2
    assert load_eval_records(json_path)[0]["sample_id"] == "paper-001"


def test_eval_cli_writes_report_and_returns_zero_when_targets_pass(tmp_path: Path, capsys) -> None:
    manifest_path = tmp_path / "manifest.json"
    records_path = tmp_path / "records.jsonl"
    report_path = tmp_path / "report.json"
    _manifest(manifest_path)
    records_path.write_text(
        "\n".join(
            [
                json.dumps(_passing_record("paper-001", 240)),
                json.dumps(_passing_record("paper-002", 260)),
            ]
        ),
        encoding="utf-8",
    )

    exit_code = eval_cli_main(
        [
            "--manifest",
            str(manifest_path),
            "--records",
            str(records_path),
            "--out",
            str(report_path),
        ]
    )

    assert exit_code == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["targets"]["all_targets_passed"] is True
    assert json.loads(capsys.readouterr().out)["report_path"] == str(report_path)


def test_eval_cli_returns_one_when_targets_fail(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    records_path = tmp_path / "records.json"
    _manifest(manifest_path)
    failing = _passing_record("paper-001", 600)
    failing["parse_success"] = False
    records_path.write_text(json.dumps([failing]), encoding="utf-8")

    exit_code = eval_cli_main(
        [
            "--manifest",
            str(manifest_path),
            "--records",
            str(records_path),
            "--out",
            str(tmp_path / "report.json"),
        ]
    )

    assert exit_code == 1


def test_eval_cli_record_command_writes_record_from_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _seed_stage(run_dir)
    gold_path = tmp_path / "gold.json"
    out_path = tmp_path / "record.json"
    write_json_file(
        gold_path,
        {
            "sample_id": "paper-001",
            "gold_concerns": [{"id": "gold-1", "covered_by": ["concern_check_percentage_consistency_001"]}],
            "deterministic_errors": [{"check_id": "check_percentage_consistency_001"}],
            "evidence_checks": [{"concern_id": "concern_check_percentage_consistency_001", "faithful": True}],
        },
    )

    exit_code = eval_cli_main(
        [
            "record",
            "--sample-id",
            "paper-001",
            "--run-dir",
            str(run_dir),
            "--gold",
            str(gold_path),
            "--out",
            str(out_path),
        ]
    )

    assert exit_code == 0
    record = json.loads(out_path.read_text(encoding="utf-8"))
    assert record["sample_id"] == "paper-001"
    assert record["deterministic_tp"] == 1


def test_eval_cli_batch_records_command_uses_manifest_run_and_gold_paths(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _seed_stage(run_dir)
    gold_path = tmp_path / "gold.json"
    records_path = tmp_path / "records.jsonl"
    manifest_path = tmp_path / "manifest.json"
    write_json_file(
        gold_path,
        {
            "sample_id": "paper-001",
            "gold_concerns": [{"id": "gold-1", "covered_by": ["concern_check_percentage_consistency_001"]}],
            "deterministic_errors": [{"check_id": "check_percentage_consistency_001"}],
            "evidence_checks": [{"concern_id": "concern_check_percentage_consistency_001", "faithful": True}],
        },
    )
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
                    "run_dir": str(run_dir),
                    "gold_path": str(gold_path),
                }
            ],
        },
    )

    exit_code = eval_cli_main(
        [
            "batch-records",
            "--manifest",
            str(manifest_path),
            "--out",
            str(records_path),
        ]
    )

    assert exit_code == 0
    rows = load_eval_records(records_path)
    assert len(rows) == 1
    assert rows[0]["sample_id"] == "paper-001"
    assert rows[0]["gold_concerns_covered"] == 1


def test_eval_cli_crossover_command_writes_report_and_eval_records(tmp_path: Path) -> None:
    input_path = tmp_path / "crossover.json"
    report_path = tmp_path / "crossover_report.json"
    records_path = tmp_path / "crossover_records.jsonl"
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

    exit_code = eval_cli_main(
        [
            "crossover",
            "--input",
            str(input_path),
            "--out",
            str(report_path),
            "--records-out",
            str(records_path),
        ]
    )

    assert exit_code == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["aggregate"]["review_retention_rate"] == 0.8
    rows = load_eval_records(records_path)
    assert rows[0]["sample_id"] == "paper-001"
    assert rows[0]["mechanical_baseline_minutes"] == 50.0
    assert rows[0]["mechanical_assisted_minutes"] == 20.0


def test_eval_cli_merge_records_combines_peerassist_and_crossover_rows(tmp_path: Path) -> None:
    peerassist_records = tmp_path / "peerassist_records.jsonl"
    crossover_records = tmp_path / "crossover_records.jsonl"
    merged_records = tmp_path / "merged.jsonl"
    peerassist_records.write_text(
        json.dumps(
            {
                "sample_id": "paper-001",
                "mode": "fast",
                "parse_success": True,
                "deterministic_tp": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    crossover_records.write_text(
        json.dumps(
            {
                "sample_id": "paper-001",
                "mechanical_baseline_minutes": 50,
                "mechanical_assisted_minutes": 20,
                "review_items_proposed": 5,
                "review_items_retained": 4,
                "baseline_core_recall": 0.5,
                "assisted_core_recall": 1.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    exit_code = eval_cli_main(
        [
            "merge-records",
            "--base-records",
            str(peerassist_records),
            "--overlay-records",
            str(crossover_records),
            "--out",
            str(merged_records),
        ]
    )

    assert exit_code == 0
    rows = load_eval_records(merged_records)
    assert rows == [
        {
            "sample_id": "paper-001",
            "mode": "fast",
            "parse_success": True,
            "deterministic_tp": 1,
            "mechanical_baseline_minutes": 50,
            "mechanical_assisted_minutes": 20,
            "review_items_proposed": 5,
            "review_items_retained": 4,
            "baseline_core_recall": 0.5,
            "assisted_core_recall": 1.0,
        }
    ]


def test_peerassist_eval_console_script_is_registered() -> None:
    payload = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert payload["project"]["scripts"]["peerassist-eval"] == "peerassist.eval_cli:main"

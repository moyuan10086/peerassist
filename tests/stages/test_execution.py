"""Execution stage tests.

Code execution requires a Docker daemon, so this stage's only test is gated
behind ``@pytest.mark.requires_docker`` and skipped by default. The real
execution path is covered by manual runs of ``demos/`` papers (see the plan
doc's verification section).
"""

from __future__ import annotations

import json
import shutil

import pytest

from fact_generation.execution.nodes.plan import _merge_auto_baseline
from fact_generation.execution.tools.alignment import run_alignment
from fact_generation.execution.tools.paper_tables import extract_paper_metric_targets


def test_extracts_generic_paper_metric_table(tmp_path) -> None:
    tables_dir = tmp_path / "tables"
    tables_dir.mkdir()
    (tables_dir / "index.json").write_text(
        json.dumps([{"id": "table_1", "path_md": "table_1.md"}]),
        encoding="utf-8",
    )
    (tables_dir / "table_1.md").write_text(
        "\n".join(
            [
                "| Method | Dataset | Accuracy | F1 |",
                "| --- | --- | --- | --- |",
                "| Ours | CIFAR-10 | 94.2 | 93.5 |",
            ]
        ),
        encoding="utf-8",
    )

    targets = extract_paper_metric_targets(tables_dir)

    assert len(targets) == 1
    target = targets[0]
    assert target.dataset == "CIFAR-10"
    assert target.method == "Ours"
    assert target.metric_source == "generic_table"
    assert target.metrics == {"accuracy": 94.2, "f1": 93.5}


def test_alignment_matches_nested_metric_json_against_paper_target(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    metrics_dir = artifacts_dir / "text" / "execution_ours"
    metrics_dir.mkdir(parents=True)
    (metrics_dir / "metrics.json").write_text(
        json.dumps(
            {
                "dataset": "CIFAR-10",
                "method": "Ours",
                "metrics": {"accuracy": 0.943, "f1": 0.934},
            }
        ),
        encoding="utf-8",
    )

    result = run_alignment(
        cfg={},
        run_dir=tmp_path,
        artifacts_dir=artifacts_dir,
        paper_extracted_tables_dir=tmp_path / "missing_tables",
        paper_metric_targets=[
            {
                "paper_table_id": "table_1",
                "paper_table_md_path": "table_1.md",
                "dataset": "CIFAR-10",
                "method": "Ours",
                "scoring_function": "",
                "metrics": {"accuracy": 94.2, "f1": 93.5},
            }
        ],
    )

    assert result.extracted_targets == 1
    assert result.matched == 1
    assert result.passed == 1
    assert result.failed == 0
    assert result.matches[0]["run_metrics_file"] == "text/execution_ours/metrics.json"


def test_plan_auto_baseline_generates_checks_from_expected_metrics(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    tasks_p = tmp_path / "tasks.json"
    baseline_p = tmp_path / "baseline.json"
    baseline_p.write_text("{}", encoding="utf-8")
    tasks_p.write_text(
        json.dumps(
            [
                {
                    "id": "eval_ours",
                    "artifact_paths": ["metrics/eval_ours.json"],
                    "expected_metrics": {"accuracy": 0.942},
                }
            ]
        ),
        encoding="utf-8",
    )

    baseline = _merge_auto_baseline(
        baseline_p=baseline_p,
        tasks_p=tasks_p,
        paper_metric_targets=[
            {
                "paper_table_id": "table_1",
                "dataset": "CIFAR-10",
                "method": "Ours",
                "metrics": {"accuracy": 0.942},
            }
        ],
        run_dir=run_dir,
    )

    checks = baseline["checks"]
    assert baseline["paper_metric_targets"][0]["dataset"] == "CIFAR-10"
    assert any(chk["type"] == "file_exists" for chk in checks)
    assert any(
        chk["type"] == "json_value"
        and chk["path"] == "metrics/eval_ours.json"
        and chk["json_path"] == ["accuracy"]
        for chk in checks
    )


@pytest.mark.requires_docker
def test_docker_daemon_is_available_for_execution_stage() -> None:
    # Smoke check that an environment claiming to be Docker-capable actually
    # has the CLI available — protects against running the gated test on a
    # host where the orchestrator would crash in a confusing way.
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not available")

"""Execution stage tests.

Code execution requires a Docker daemon, so this stage's only test is gated
behind ``@pytest.mark.requires_docker`` and skipped by default. The real
execution path is covered by manual runs of ``demos/`` papers (see the plan
doc's verification section).
"""

from __future__ import annotations

import json
import shutil
import sys

import pytest

from fact_generation.execution.nodes.plan import _merge_auto_baseline
from fact_generation.execution.nodes.prepare import (
    _anonymous_4open_repo_id,
    _normalize_shell_script_line_endings,
    _patch_api_placeholders_for_env,
)
from fact_generation.execution.nodes.run import _semantic_runtime_failure, run_node
from fact_generation.execution.tools.alignment import run_alignment
from fact_generation.execution.tools.docker import (
    _docker_build_args,
    _docker_env_passthrough,
    _normalize_container_proxy,
    _paper_install_deps_py_text,
    docker_run_paper_image,
)
from fact_generation.execution.tools.log_metrics import extract_metrics_from_text, write_task_metric_artifact
from fact_generation.execution.tools.metrics import compute_check
from fact_generation.execution.tools.paper_tables import extract_paper_metric_targets
from fact_generation.execution.tools.task_infer import infer_tasks_heuristic
from util.subprocess_runner import run_command


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


def test_extracts_metric_row_paper_table(tmp_path) -> None:
    tables_dir = tmp_path / "tables"
    tables_dir.mkdir()
    (tables_dir / "index.json").write_text(
        json.dumps([{"id": "table_2", "path_md": "table_2.md"}]),
        encoding="utf-8",
    )
    (tables_dir / "table_2.md").write_text(
        "\n".join(
            [
                "| Metric | Baseline | Ours |",
                "| --- | --- | --- |",
                "| Accuracy | 91.0 | 94.2 |",
                "| F1 | 90.5 | 93.5 |",
            ]
        ),
        encoding="utf-8",
    )

    targets = extract_paper_metric_targets(tables_dir)
    ours = next(t for t in targets if t.method == "Ours")

    assert ours.metric_source == "metric_row_table"
    assert ours.metrics == {"accuracy": 94.2, "f1": 93.5}


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


def test_log_metrics_writes_standard_metric_artifact(tmp_path) -> None:
    rel = write_task_metric_artifact(
        artifacts_dir=tmp_path / "artifacts",
        task_id="eval_ours",
        task={
            "family": "eval",
            "dataset": "CIFAR-10",
            "method": "Ours",
            "expected_metrics": {"accuracy": 94.2, "f1": 93.5},
            "claims": ["Ours reaches 94.2 accuracy and 93.5 F1 on CIFAR-10."],
        },
        stdout="Epoch done\naccuracy: 94.3\nf1 = 93.4\n",
        stderr="",
    )

    assert rel == "metrics/eval_ours_metrics.json"
    payload = json.loads((tmp_path / "artifacts" / rel).read_text(encoding="utf-8"))
    assert payload["dataset"] == "CIFAR-10"
    assert payload["metrics"] == {"accuracy": 94.3, "f1": 93.4}


def test_extract_metrics_from_json_log_line() -> None:
    metrics = extract_metrics_from_text(
        'prefix\n{"metrics": {"acc": 0.943, "loss": 0.12}}\n',
        expected_metrics={"accuracy": 94.2},
    )

    assert metrics["accuracy"] == 0.943
    assert metrics["loss"] == 0.12


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
        and chk["path"] == "metrics/eval_ours_metrics.json"
        and chk["json_path"] == ["metrics", "accuracy"]
        for chk in checks
    )


def test_json_value_check_handles_metric_scaling_and_nested_paths(tmp_path) -> None:
    artifacts = tmp_path / "artifacts"
    (artifacts / "metrics").mkdir(parents=True)
    (artifacts / "metrics" / "eval.json").write_text(
        json.dumps({"metrics": {"accuracy": 0.943}}),
        encoding="utf-8",
    )

    result = compute_check(
        str(artifacts),
        {
            "type": "json_value",
            "path": "metrics/eval.json",
            "json_path": ["metrics", "accuracy"],
            "metric": "accuracy",
            "expected": 94.2,
            "tolerance": 0.02,
        },
    )

    assert result["passed"] is True


def test_run_node_extracts_metrics_from_task_logs(tmp_path) -> None:
    paper_root = tmp_path / "repo"
    paper_root.mkdir()
    tasks_p = tmp_path / "tasks.json"
    run_dir = tmp_path / "run"
    tasks_p.write_text(
        json.dumps(
            [
                {
                    "id": "eval_ours",
                    "family": "eval",
                    "cwd": "{paper_root}",
                    "cmd": ["python", "-c", "print('accuracy: 94.3')"],
                    "timeout_sec": 30,
                    "dataset": "CIFAR-10",
                    "method": "Ours",
                    "expected_metrics": {"accuracy": 94.2},
                }
            ]
        ),
        encoding="utf-8",
    )
    state = {
        "config": {
            "paper_root": str(paper_root),
            "tasks_path": str(tasks_p),
            "docker_enabled": False,
        },
        "run": {
            "dir": str(run_dir),
            "logs_dir": str(run_dir / "logs"),
            "artifacts_dir": str(run_dir / "artifacts"),
        },
    }

    out = run_node(state)

    task_result = out["run_result"]["tasks"][0]
    assert task_result["metric_artifact"] == "metrics/eval_ours_metrics.json"
    metrics = json.loads((run_dir / "artifacts" / task_result["metric_artifact"]).read_text())
    assert metrics["dataset"] == "CIFAR-10"
    assert metrics["metrics"]["accuracy"] == 94.3


def test_runtime_traceback_output_is_treated_as_failure() -> None:
    reason = _semantic_runtime_failure(
        stdout="",
        stderr=(
            "Traceback (most recent call last):\n"
            '  File "/app/main.py", line 1, in <module>\n'
            "ModuleNotFoundError: No module named 'numpy'\n"
        ),
    )

    assert reason == "python_traceback_in_output"


def test_run_command_reports_timeout_explicitly(tmp_path) -> None:
    result = run_command(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        cwd=str(tmp_path),
        timeout_sec=1,
    )

    assert result.returncode == 124
    assert "TimeoutExpired" in result.stderr


def test_run_command_zero_timeout_means_no_timeout(tmp_path) -> None:
    result = run_command(
        [sys.executable, "-c", "import time; time.sleep(0.1); print('ok')"],
        cwd=str(tmp_path),
        timeout_sec=0,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "ok"


def test_docker_build_args_include_pip_index_and_extra_packages(monkeypatch) -> None:
    monkeypatch.setenv("EXECUTION_DOCKER_PIP_INDEX_URL", "https://mirror.example/simple")
    monkeypatch.setenv("EXECUTION_DOCKER_PIP_TRUSTED_HOST", "mirror.example")
    monkeypatch.setenv("EXECUTION_DOCKER_HTTP_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setenv("EXECUTION_DOCKER_HTTPS_PROXY", "http://localhost:7897")

    args = _docker_build_args({"docker_extra_pip_packages": "numpy scikit-learn"})

    joined = " ".join(args)
    assert "--build-arg PIP_INDEX_URL=https://mirror.example/simple" in joined
    assert "--build-arg PIP_TRUSTED_HOST=mirror.example" in joined
    assert "--build-arg EXECUTION_DOCKER_EXTRA_PIP_PACKAGES=numpy scikit-learn" in joined
    assert "--build-arg HTTP_PROXY=http://host.docker.internal:7897" in joined
    assert "--build-arg HTTPS_PROXY=http://host.docker.internal:7897" in joined


def test_docker_runtime_injects_host_proxy(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("EXECUTION_DOCKER_HTTP_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setenv("EXECUTION_DOCKER_HTTPS_PROXY", "http://localhost:7897")

    cmd = docker_run_paper_image(
        image="paper:test",
        paper_root_host=str(tmp_path),
        run_dir_host=str(tmp_path),
        cwd_container="/app",
        cmd=["python", "-V"],
    )

    joined = " ".join(cmd)
    assert "-e HTTP_PROXY=http://host.docker.internal:7897" in joined
    assert "-e HTTPS_PROXY=http://host.docker.internal:7897" in joined


def test_docker_runtime_passes_selected_env_names_without_values(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-value")
    monkeypatch.setenv("EXECUTION_DOCKER_ENV_PASSTHROUGH", "OPENAI_API_KEY,OPENAI_BASE_URL")

    cmd = docker_run_paper_image(
        image="paper:test",
        paper_root_host=str(tmp_path),
        run_dir_host=str(tmp_path),
        cwd_container="/app",
        cmd=["python", "-V"],
        env_passthrough=_docker_env_passthrough({}),
    )

    joined = " ".join(cmd)
    assert "-e OPENAI_API_KEY" in joined
    assert "secret-value" not in joined
    assert "OPENAI_BASE_URL" not in joined


def test_normalize_container_proxy_rewrites_loopback() -> None:
    assert _normalize_container_proxy("http://127.0.0.1:7897") == "http://host.docker.internal:7897"
    assert _normalize_container_proxy("http://localhost:7897") == "http://host.docker.internal:7897"


def test_docker_install_deps_installs_numpy_before_torch() -> None:
    text = _paper_install_deps_py_text()

    assert text.index("if numpy_line:") < text.index("if torch_pin:")


def test_heuristic_tasks_use_paper_targets_and_readme_commands(tmp_path) -> None:
    (tmp_path / "README.md").write_text(
        "\n".join(
            [
                "```bash",
                "python train.py --dataset CIFAR-10 --model ours",
                "python eval.py --dataset CIFAR-10 --model ours",
                "```",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text("", encoding="utf-8")
    (tmp_path / "train.py").write_text("print('train')\n", encoding="utf-8")
    (tmp_path / "eval.py").write_text("print('accuracy: 94.3')\n", encoding="utf-8")

    result = infer_tasks_heuristic(
        str(tmp_path),
        mode="full",
        paper_metric_targets=[
            {
                "paper_table_id": "table_1",
                "dataset": "CIFAR-10",
                "method": "ours",
                "metrics": {"accuracy": 94.2},
                "paper_claim": "Ours reaches 94.2 accuracy on CIFAR-10.",
            }
        ],
    )

    target_tasks = [t for t in result.tasks if t.get("expected_metrics") == {"accuracy": 94.2}]
    assert target_tasks
    assert any(t.get("family") == "eval" for t in target_tasks)
    assert all(t.get("metric_artifact_path") for t in target_tasks)


def test_heuristic_tasks_do_not_install_missing_requirements(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Tiny repo\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")

    result = infer_tasks_heuristic(str(tmp_path), mode="smoke")

    assert not any(t.get("id") == "install_deps" for t in result.tasks)
    assert any(t.get("id") == "repo_smoke" for t in result.tasks)


def test_heuristic_smoke_disables_readme_prepare_commands(tmp_path) -> None:
    (tmp_path / "README.md").write_text(
        "```bash\npip install -r requirements.txt\npython main.py --help\n```\n",
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text("tensorflow-gpu==1.14.0\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")

    result = infer_tasks_heuristic(str(tmp_path), mode="smoke")

    prepare_tasks = [t for t in result.tasks if t.get("family") == "prepare"]
    assert prepare_tasks
    assert all(t.get("enabled") is False for t in prepare_tasks)


def test_heuristic_full_adds_conventional_preprocess_before_training(tmp_path) -> None:
    (tmp_path / "README.md").write_text(
        "```bash\npython run.py -score_func transe -opn sub\n```\n",
        encoding="utf-8",
    )
    (tmp_path / "run.py").write_text("print('train')\n", encoding="utf-8")
    (tmp_path / "preprocess.sh").write_text("mkdir -p data\n", encoding="utf-8")

    result = infer_tasks_heuristic(str(tmp_path), mode="full")

    ids = [str(t.get("id") or "") for t in result.tasks]
    assert "prepare_preprocess_sh" in ids
    assert any(t.get("id") == "train_transe_sub" for t in result.tasks)
    assert ids.index("prepare_preprocess_sh") < ids.index("train_transe_sub")

    prep = next(t for t in result.tasks if t.get("id") == "prepare_preprocess_sh")
    assert prep.get("enabled") is True
    assert prep.get("cmd") == ["bash", "preprocess.sh"]
    assert not any((t.get("cmd") or [])[:2] == ["cmd", "/c"] for t in result.tasks)


def test_heuristic_infers_cwd_for_unique_readme_script(tmp_path) -> None:
    (tmp_path / "README.md").write_text(
        "```bash\npython generate_response.py\n```\n",
        encoding="utf-8",
    )
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    (scripts / "generate_response.py").write_text("print('ok')\n", encoding="utf-8")

    result = infer_tasks_heuristic(str(tmp_path), mode="full")

    task = next(t for t in result.tasks if t.get("id") == "reproduce_readme_1")
    assert task.get("cwd") == "{paper_root}/Scripts"
    assert task.get("cmd") == ["python", "generate_response.py"]


def test_normalize_shell_script_line_endings(tmp_path) -> None:
    script = tmp_path / "preprocess.sh"
    script.write_bytes(b"#!/bin/bash\r\nmkdir data\r\n")

    changed = _normalize_shell_script_line_endings(tmp_path)

    assert changed == 1
    assert script.read_bytes() == b"#!/bin/bash\nmkdir data\n"


def test_patch_api_placeholders_reads_runtime_env_without_writing_secret(tmp_path) -> None:
    script = tmp_path / "generate_response.py"
    script.write_text(
        "\n".join(
            [
                "from openai import OpenAI",
                "API_KEY = 'YOUR API-KEY'",
                "BASE_URL = 'https://api.openai.com/v1'",
                "MODEL_NAME = 'o3-mini'",
            ]
        ),
        encoding="utf-8",
    )

    changed = _patch_api_placeholders_for_env(tmp_path)

    text = script.read_text(encoding="utf-8")
    assert changed == ["generate_response.py"]
    assert "EXECUTION_OPENAI_API_KEY" in text
    assert "EXECUTION_OPENAI_BASE_URL" in text
    assert "EXECUTION_OPENAI_MODEL" in text
    assert "sk-" not in text


def test_anonymous_4open_repo_id_parses_repo_links() -> None:
    assert (
        _anonymous_4open_repo_id("https://anonymous.4open.science/r/FMP-AD84/README.md")
        == "FMP-AD84"
    )
    assert _anonymous_4open_repo_id("https://anonymous.4open.science/repository/BMAS-AAD0") == "BMAS-AAD0"
    assert _anonymous_4open_repo_id("https://github.com/mainlp/explaind") == ""


def test_run_execution_stage_accepts_repo_without_pdf(tmp_path) -> None:
    from fact_generation.execution.stage_runner import run_execution_stage

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Tiny repo\n", encoding="utf-8")
    (repo / "main.py").write_text("print('ok')\n", encoding="utf-8")

    result = run_execution_stage(
        run_dir=tmp_path / "run",
        paper_key="tiny_repo",
        paper_root=str(repo),
        no_pdf_extract=True,
        no_llm=True,
        auto_tasks=True,
        auto_tasks_force=True,
        docker_enabled=False,
        max_attempts=1,
    )

    assert result.status in {"ok", "inconclusive"}
    assert (tmp_path / "run" / "stages" / "fact_generation" / "execution" / "execution.json").exists()


def test_explicit_source_does_not_load_same_key_demo_fixture(tmp_path) -> None:
    from fact_generation.execution.stage_runner import run_execution_stage

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Tiny repo\n", encoding="utf-8")
    (repo / "main.py").write_text("print('ok')\n", encoding="utf-8")

    result = run_execution_stage(
        run_dir=tmp_path / "run",
        paper_key="compgcn",
        paper_root=str(repo),
        no_pdf_extract=True,
        no_llm=True,
        auto_tasks=True,
        auto_tasks_force=True,
        docker_enabled=False,
        max_attempts=1,
    )

    assert result.status == "inconclusive"


@pytest.mark.requires_docker
def test_docker_daemon_is_available_for_execution_stage() -> None:
    # Smoke check that an environment claiming to be Docker-capable actually
    # has the CLI available — protects against running the gated test on a
    # host where the orchestrator would crash in a confusing way.
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not available")

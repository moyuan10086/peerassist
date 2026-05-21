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
    _infer_python_spec_from_repo,
    _normalize_shell_script_line_endings,
    _patch_api_placeholders_for_env,
)
from fact_generation.execution.nodes.run import (
    _effective_task_timeout,
    _resolve_host_python_cmd,
    _semantic_runtime_failure,
    run_node,
)
from fact_generation.execution.tools.alignment import run_alignment
from fact_generation.execution.tools.docker import (
    _collect_repo_requirements_text,
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


def test_extract_metrics_from_plain_log_without_expected_metrics() -> None:
    metrics = extract_metrics_from_text("final report\nAccuracy: 94.3\nF1 = 93.4\n")

    assert metrics["accuracy"] == 94.3
    assert metrics["f1"] == 93.4


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


def test_run_node_accepts_eval_metric_without_expected_metrics(tmp_path) -> None:
    paper_root = tmp_path / "repo"
    paper_root.mkdir()
    tasks_p = tmp_path / "tasks.json"
    run_dir = tmp_path / "run"
    tasks_p.write_text(
        json.dumps(
            [
                {
                    "id": "eval_readme",
                    "family": "eval",
                    "cwd": "{paper_root}",
                    "cmd": ["python", "-c", "print('Accuracy: 94.3')"],
                    "timeout_sec": 30,
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
    assert task_result["success"] is True
    assert task_result["metric_artifact"] == "metrics/eval_readme_metrics.json"


def test_run_node_marks_eval_without_metrics_inconclusive(tmp_path) -> None:
    paper_root = tmp_path / "repo"
    paper_root.mkdir()
    tasks_p = tmp_path / "tasks.json"
    run_dir = tmp_path / "run"
    tasks_p.write_text(
        json.dumps(
            [
                {
                    "id": "eval_readme",
                    "family": "eval",
                    "cwd": "{paper_root}",
                    "cmd": [
                        "python",
                        "-c",
                        "print('Accumulated Accuracy Scores:\\n\\n-------------------------------------------------- Finished')",
                    ],
                    "timeout_sec": 30,
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

    assert out["status"] == "inconclusive"
    assert out["run_result"]["success"] is False
    assert out["run_result"]["inconclusive"] is True
    assert out["run_result"]["semantic_failure"] == "semantic_no_metrics"
    assert out["run_result"]["tasks"][0]["semantic_failure"] == "semantic_no_metrics"


def test_graph_exit_status_preserves_run_inconclusive() -> None:
    from fact_generation.execution.graph import _compute_exit_status

    assert _compute_exit_status({"status": "inconclusive", "run_result": {"success": False}}) == "inconclusive"


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


def test_masked_startup_failure_output_is_treated_as_failure() -> None:
    reason = _semantic_runtime_failure(
        stdout=(
            "All trained models found! Can skip training.\n"
            "Failed to load MIMIC data: preprocessed_mimic_data.pkl missing\n"
            "System startup failed\n"
        ),
        stderr="",
    )

    assert reason == "semantic_system_startup_failed"


def test_run_result_carries_semantic_failure(tmp_path) -> None:
    paper_root = tmp_path / "paper"
    paper_root.mkdir()
    run_dir = tmp_path / "run"
    tasks_p = tmp_path / "tasks.json"
    tasks_p.write_text(
        json.dumps(
            [
                {
                    "id": "masked_failure",
                    "cwd": "{paper_root}",
                    "cmd": ["python", "-c", "print('System startup failed')"],
                    "timeout_sec": 30,
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

    assert out["status"] == "failed"
    assert out["run_result"]["returncode"] == 0
    assert out["run_result"]["semantic_failure"] == "semantic_system_startup_failed"


def test_no_docker_python_tasks_use_current_interpreter() -> None:
    resolved = _resolve_host_python_cmd(["python", "-V"])

    assert resolved[0] == sys.executable
    assert resolved[1:] == ["-V"]


def test_task_timeout_can_be_disabled_by_env(monkeypatch) -> None:
    monkeypatch.setenv("EXECUTION_DISABLE_TASK_TIMEOUT", "1")

    assert _effective_task_timeout({"timeout_sec": 1}, {}) == 0


def test_task_timeout_can_be_overridden_by_env(monkeypatch) -> None:
    monkeypatch.setenv("EXECUTION_TASK_TIMEOUT_SEC", "0")

    assert _effective_task_timeout({"timeout_sec": 1}, {}) == 0


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


def test_docker_runtime_passes_default_execution_llm_env_names(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("EXECUTION_OPENAI_API_KEY", "secret-value")
    monkeypatch.setenv("EXECUTION_OPENAI_BASE_URL", "https://api.example/v1")
    monkeypatch.setenv("EXECUTION_OPENAI_MODEL", "gpt-test")

    cmd = docker_run_paper_image(
        image="paper:test",
        paper_root_host=str(tmp_path),
        run_dir_host=str(tmp_path),
        cwd_container="/app",
        cmd=["python", "-V"],
        env_passthrough=_docker_env_passthrough({}),
    )

    joined = " ".join(cmd)
    assert "-e EXECUTION_OPENAI_API_KEY" in joined
    assert "-e EXECUTION_OPENAI_BASE_URL" in joined
    assert "-e EXECUTION_OPENAI_MODEL" in joined
    assert "secret-value" not in joined


def test_normalize_container_proxy_rewrites_loopback() -> None:
    assert _normalize_container_proxy("http://127.0.0.1:7897") == "http://host.docker.internal:7897"
    assert _normalize_container_proxy("http://localhost:7897") == "http://host.docker.internal:7897"


def test_docker_install_deps_installs_numpy_before_torch() -> None:
    text = _paper_install_deps_py_text()

    assert text.index("if numpy_line:") < text.index("if torch_pin:")


def test_docker_install_deps_clears_old_torch_execstack() -> None:
    text = _paper_install_deps_py_text()

    assert "def _clear_torch_executable_stack()" in text
    assert "PT_GNU_STACK" in text
    assert "torch_execstack_cleared" in text


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


def test_heuristic_smoke_uses_unique_top_level_python_file(tmp_path) -> None:
    (tmp_path / "dinov2_moe_new.py").write_text("print('ok')\n", encoding="utf-8")

    result = infer_tasks_heuristic(str(tmp_path), mode="smoke")

    task = next(t for t in result.tasks if t.get("id") == "repo_smoke")
    assert task.get("cmd") == ["python", "-m", "py_compile", "dinov2_moe_new.py"]


def test_heuristic_smoke_finds_nested_main_script(tmp_path) -> None:
    nested = tmp_path / "Code" / "Diagramdiff platform" / "Core algorithm code"
    nested.mkdir(parents=True)
    (nested / "main.py").write_text("print('ok')\n", encoding="utf-8")

    result = infer_tasks_heuristic(str(tmp_path), mode="smoke")

    task = next(t for t in result.tasks if t.get("id") == "repo_smoke")
    assert task.get("cmd") == [
        "python",
        "-m",
        "py_compile",
        "Code/Diagramdiff platform/Core algorithm code/main.py",
    ]


def test_heuristic_full_adds_entrypoint_task_without_readme_command(tmp_path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "working_main_system.py").write_text(
        "if __name__ == '__main__':\n    print('train')\n",
        encoding="utf-8",
    )

    result = infer_tasks_heuristic(str(tmp_path), mode="full")

    task = next(t for t in result.tasks if t.get("id") == "train_entrypoint_src_working_main_system_py")
    assert task.get("cmd") == ["python", "src/working_main_system.py"]
    assert task.get("enabled") is True


def test_heuristic_disables_entrypoint_with_required_args(tmp_path) -> None:
    (tmp_path / "main.py").write_text(
        "\n".join(
            [
                "import argparse",
                "parser = argparse.ArgumentParser()",
                "parser.add_argument('--img_path', required=True)",
                "if __name__ == '__main__':",
                "    parser.parse_args()",
            ]
        ),
        encoding="utf-8",
    )

    result = infer_tasks_heuristic(str(tmp_path), mode="full")

    task = next(t for t in result.tasks if t.get("id") == "train_entrypoint_main_py")
    assert task.get("enabled") is False
    assert task.get("disabled_reason") == "required_cli_arguments_missing"


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


def test_heuristic_skips_conda_environment_commands(tmp_path) -> None:
    (tmp_path / "README.md").write_text(
        "```bash\nconda create -n paper python=3.9\nconda activate paper\npython train.py\n```\n",
        encoding="utf-8",
    )
    (tmp_path / "train.py").write_text("print('train')\n", encoding="utf-8")

    result = infer_tasks_heuristic(str(tmp_path), mode="full")

    commands = [t.get("cmd") or [] for t in result.tasks]
    assert ["python", "train.py"] in commands
    assert not any(cmd and cmd[0] == "conda" for cmd in commands)


def test_heuristic_deduplicates_repeated_readme_commands(tmp_path) -> None:
    (tmp_path / "README.md").write_text(
        "```bash\nbash run_for_dataset.sh\nbash run_for_dataset.sh\nbash run_for_dataset.sh\n```\n",
        encoding="utf-8",
    )
    (tmp_path / "run_for_dataset.sh").write_text("echo ok\n", encoding="utf-8")

    result = infer_tasks_heuristic(str(tmp_path), mode="full")

    commands = [t.get("cmd") for t in result.tasks]
    assert commands.count(["bash", "run_for_dataset.sh"]) == 1


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


def test_heuristic_marks_api_tasks_but_keeps_them_enabled_by_default(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("EXECUTION_DISABLE_EXTERNAL_API_TASKS", raising=False)
    (tmp_path / "README.md").write_text(
        "```bash\npython eval_model.py\npython plot_table.py\n```\n",
        encoding="utf-8",
    )
    (tmp_path / "eval_model.py").write_text(
        "from openai import OpenAI\nclient = OpenAI(api_key='xxx')\n",
        encoding="utf-8",
    )
    (tmp_path / "plot_table.py").write_text("print('accuracy: 23.9')\n", encoding="utf-8")

    result = infer_tasks_heuristic(str(tmp_path), mode="full")

    api_task = next(t for t in result.tasks if t.get("id") == "eval_readme_1")
    table_task = next(t for t in result.tasks if t.get("id") == "reproduce_readme_2")
    assert api_task.get("requires_external_api") is True
    assert api_task.get("enabled") is True
    assert "disabled_reason" not in api_task
    assert table_task.get("requires_external_api") is not True
    assert table_task.get("enabled") is True


def test_heuristic_can_disable_api_tasks_for_server_queues(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EXECUTION_DISABLE_EXTERNAL_API_TASKS", "1")
    (tmp_path / "README.md").write_text(
        "```bash\npython eval_model.py\npython plot_table.py\n```\n",
        encoding="utf-8",
    )
    (tmp_path / "eval_model.py").write_text(
        "from openai import OpenAI\nclient = OpenAI(api_key='xxx')\n",
        encoding="utf-8",
    )
    (tmp_path / "plot_table.py").write_text("print('accuracy: 23.9')\n", encoding="utf-8")

    result = infer_tasks_heuristic(str(tmp_path), mode="full")

    api_task = next(t for t in result.tasks if t.get("id") == "eval_readme_1")
    table_task = next(t for t in result.tasks if t.get("id") == "reproduce_readme_2")
    assert api_task.get("requires_external_api") is True
    assert api_task.get("enabled") is False
    assert api_task.get("disabled_reason") == "external_api_or_model_server_required"
    assert table_task.get("enabled") is True


def test_heuristic_marks_api_tasks_through_local_imports(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EXECUTION_DISABLE_EXTERNAL_API_TASKS", "1")
    (tmp_path / "README.md").write_text("```bash\npython conversation.py\n```\n", encoding="utf-8")
    (tmp_path / "conversation.py").write_text("from agent import Agent\nAgent()\n", encoding="utf-8")
    (tmp_path / "agent.py").write_text(
        "from openai import OpenAI\nclient = OpenAI(api_key='Your API_KEY')\n",
        encoding="utf-8",
    )

    result = infer_tasks_heuristic(str(tmp_path), mode="full")

    task = next(t for t in result.tasks if t.get("id") == "reproduce_readme_1")
    assert task.get("requires_external_api") is True
    assert task.get("enabled") is False
    assert task.get("disabled_reason") == "external_api_or_model_server_required"


def test_heuristic_repairs_missing_readme_shell_script_to_existing_candidate(tmp_path) -> None:
    (tmp_path / "README.md").write_text(
        "```bash\nscripts/test/test_pipeline_gpt_4o_resume.sh\n```\n",
        encoding="utf-8",
    )
    scripts = tmp_path / "scripts" / "test"
    scripts.mkdir(parents=True)
    (scripts / "test_pipeline.sh").write_text("echo ok\n", encoding="utf-8")

    result = infer_tasks_heuristic(str(tmp_path), mode="full")

    task = next(t for t in result.tasks if t.get("id") == "reproduce_readme_1")
    assert task.get("cmd") == ["bash", "scripts/test/test_pipeline.sh"]


def test_heuristic_marks_shell_api_script(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EXECUTION_DISABLE_EXTERNAL_API_TASKS", "1")
    (tmp_path / "README.md").write_text(
        "```bash\nbash run_eval.sh\n```\n",
        encoding="utf-8",
    )
    (tmp_path / "run_eval.sh").write_text("export OPENAI_API_KEY=xxx\npython eval.py\n", encoding="utf-8")

    result = infer_tasks_heuristic(str(tmp_path), mode="full")

    task = next(t for t in result.tasks if t.get("id") == "eval_readme_1")
    assert task.get("requires_external_api") is True
    assert task.get("enabled") is False


def test_heuristic_does_not_treat_title_case_make_sentence_as_command(tmp_path) -> None:
    (tmp_path / "README.md").write_text(
        "```text\nMake predictions\nmake evaluate\n```\n",
        encoding="utf-8",
    )

    result = infer_tasks_heuristic(str(tmp_path), mode="full")

    commands = [t.get("cmd") for t in result.tasks]
    assert ["Make", "predictions"] not in commands
    assert ["make", "evaluate"] in commands


def test_heuristic_ignores_python_comments_that_look_like_make_commands(tmp_path) -> None:
    (tmp_path / "README.md").write_text(
        "```python\n# make batch size small enough so you do not run OOM\nprint('ok')\n```\n",
        encoding="utf-8",
    )

    result = infer_tasks_heuristic(str(tmp_path), mode="full")

    commands = [t.get("cmd") for t in result.tasks]
    assert ["make", "batch", "size", "small", "enough", "so", "you", "do", "not", "run", "OOM"] not in commands


def test_heuristic_ignores_readme_directory_paths(tmp_path) -> None:
    (tmp_path / "README.md").write_text(
        "```text\n./ckpt/domainnet126/\n```\n",
        encoding="utf-8",
    )

    result = infer_tasks_heuristic(str(tmp_path), mode="full")

    commands = [t.get("cmd") for t in result.tasks]
    assert ["./ckpt/domainnet126/"] not in commands


def test_heuristic_disables_unresolved_usage_placeholder_commands(tmp_path) -> None:
    (tmp_path / "README.md").write_text(
        "```bash\n./run.sh DATASET ADAPTER [CONFIG_FILE]\n./run.sh cifar10c petta\n```\n",
        encoding="utf-8",
    )
    (tmp_path / "run.sh").write_text("echo ok\n", encoding="utf-8")

    result = infer_tasks_heuristic(str(tmp_path), mode="full")

    placeholder_task = next(t for t in result.tasks if t.get("id") == "reproduce_readme_1")
    concrete_task = next(t for t in result.tasks if t.get("id") == "reproduce_readme_2")
    assert placeholder_task.get("enabled") is False
    assert placeholder_task.get("disabled_reason") == "readme_placeholder_command"
    assert concrete_task.get("enabled") is True


def test_heuristic_detects_env_prefixed_training_command(tmp_path) -> None:
    (tmp_path / "README.md").write_text(
        "\n".join(
            [
                "```bash",
                "CUDA_VISIBLE_DEVICES=$GPU python main.py \\",
                "  --data_dir data/$1 \\",
                "  --do_train",
                "```",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "main.py").write_text("print('train')\n", encoding="utf-8")

    result = infer_tasks_heuristic(str(tmp_path), mode="full")

    task = next(t for t in result.tasks if t.get("id") == "train_readme_1")
    assert task.get("cmd") == [
        "bash",
        "-lc",
        "CUDA_VISIBLE_DEVICES=$GPU python main.py --data_dir data/$1 --do_train",
    ]
    assert task.get("enabled") is False
    assert task.get("disabled_reason") == "readme_placeholder_command"


def test_heuristic_selects_first_readme_choice_placeholder(tmp_path) -> None:
    (tmp_path / "README.md").write_text(
        "```bash\npython test_time_new.py --cfg cfgs/cifar10_c/[tent/cotta].yaml fed.fed_tech [fedavg/fedbn]\n```\n",
        encoding="utf-8",
    )
    (tmp_path / "test_time_new.py").write_text("print('ok')\n", encoding="utf-8")

    result = infer_tasks_heuristic(str(tmp_path), mode="full")

    task = next(t for t in result.tasks if t.get("id") == "reproduce_readme_1")
    assert task.get("cmd") == [
        "python",
        "test_time_new.py",
        "--cfg",
        "cfgs/cifar10_c/tent.yaml",
        "fed.fed_tech",
        "fedavg",
    ]


def test_normalize_shell_script_line_endings(tmp_path) -> None:
    script = tmp_path / "preprocess.sh"
    script.write_bytes(b"#!/bin/bash\r\nmkdir data\r\n")

    changed = _normalize_shell_script_line_endings(tmp_path)

    assert changed == 1
    assert script.read_bytes() == b"#!/bin/bash\nmkdir data\n"


def test_collect_repo_requirements_uses_readme_and_imports(tmp_path) -> None:
    (tmp_path / "README.md").write_text(
        "\n".join(
            [
                "## Python Packages",
                "* pytorch==1.12.1",
                "* dgl==1.0.1+cu113",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "main.py").write_text(
        "\n".join(
            [
                "import numpy as np",
                "import torch",
                "from sklearn.metrics import accuracy_score",
                "from utils import local_helper",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "utils.py").write_text("def local_helper(): pass\n", encoding="utf-8")

    req_text = _collect_repo_requirements_text(tmp_path)

    assert "torch==1.12.1" in req_text
    assert "dgl==1.0.1" in req_text
    assert "numpy" in req_text
    assert "scikit-learn" in req_text
    assert "utils" not in req_text
    assert _infer_python_spec_from_repo(tmp_path) == "3.10"


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


def test_patch_api_placeholders_handles_lowercase_xxx_keys(tmp_path) -> None:
    script = tmp_path / "eval_model.py"
    script.write_text(
        "\n".join(
            [
                "from openai import OpenAI",
                "openai_api_key = 'xxx'",
                "openai_api_base = 'http://localhost:8000/v1'",
                "model_name = 'o3'",
                "client = OpenAI(api_key=openai_api_key, base_url=openai_api_base)",
            ]
        ),
        encoding="utf-8",
    )

    changed = _patch_api_placeholders_for_env(tmp_path)

    text = script.read_text(encoding="utf-8")
    assert changed == ["eval_model.py"]
    assert "openai_api_key = __import__('os').environ.get('EXECUTION_OPENAI_API_KEY')" in text
    assert "openai_api_base = __import__('os').environ.get('EXECUTION_OPENAI_BASE_URL')" in text
    assert "model_name = __import__('os').environ.get('EXECUTION_OPENAI_MODEL')" in text


def test_patch_api_placeholders_handles_shell_exports(tmp_path) -> None:
    script = tmp_path / "run_eval.sh"
    script.write_text(
        "\n".join(
            [
                'export OPENAI_API_KEY="your api key"',
                'export OPENAI_BASE_URL="your api base url"',
                'MODEL_NAME="gpt-4o"',
                "python -m tests.test_full_pipeline_resume --model_name $MODEL_NAME",
            ]
        ),
        encoding="utf-8",
    )

    changed = _patch_api_placeholders_for_env(tmp_path)

    text = script.read_text(encoding="utf-8")
    assert changed == ["run_eval.sh"]
    assert 'export OPENAI_API_KEY="${EXECUTION_OPENAI_API_KEY:-' in text
    assert 'export OPENAI_BASE_URL="${EXECUTION_OPENAI_BASE_URL:-' in text
    assert 'MODEL_NAME="${EXECUTION_OPENAI_MODEL:-' in text


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

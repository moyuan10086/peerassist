from __future__ import annotations

from fact_generation.execution.tools.evidence_summary import classify_run_failure, classify_task_failure


def test_command_not_found_is_environment_failure() -> None:
    task = {"success": False, "returncode": 127, "stderr_tail": "/usr/bin/bash: line 1: uv: command not found\n"}
    run_result = {"success": False, "returncode": 127, "stderr_tail": task["stderr_tail"]}

    assert classify_task_failure(task) == "environment"
    assert classify_run_failure(run_result, {}) == "environment"

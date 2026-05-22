from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from common import run_stats

FAILURE_STAGES = ("access", "environment", "run", "metric", "alignment")


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _read_events(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "issues.jsonl"
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except Exception:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events


def _event_duration_sec(events: list[dict[str, Any]]) -> float:
    ts_values: list[float] = []
    for event in events:
        try:
            ts_values.append(float(event.get("ts") or 0.0))
        except Exception:
            continue
    ts_values = [x for x in ts_values if x > 0.0]
    if len(ts_values) < 2:
        return 0.0
    return max(ts_values) - min(ts_values)


def _node_timings_from_state(state: dict[str, Any]) -> dict[str, float]:
    raw = state.get("node_timings")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for node, rows in raw.items():
        total = 0.0
        for row in _as_list(rows):
            if not isinstance(row, dict):
                continue
            try:
                total += max(0.0, float(row.get("duration_sec") or 0.0))
            except Exception:
                continue
        if total > 0:
            out[str(node)] = round(total, 3)
    return out


def _node_timings_from_events(events: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for event in events:
        if event.get("kind") != "node_timing":
            continue
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        node = str(data.get("node") or "").strip()
        if not node:
            continue
        try:
            out[node] = round(out.get(node, 0.0) + max(0.0, float(data.get("duration_sec") or 0.0)), 3)
        except Exception:
            continue
    return out


def _status_from_task(task: dict[str, Any]) -> str:
    if task.get("skipped"):
        return "skipped"
    if task.get("success"):
        return "success"
    if task.get("semantic_failure") == "semantic_no_metrics":
        return "inconclusive"
    return "failed"


def _stage_from_error_text(text: str) -> str:
    lower = text.lower()
    if any(
        token in lower
        for token in [
            "clone",
            "not found",
            "access unavailable",
            "paper_pdf_unavailable",
            "supplementary_download_failed",
            "supplementary_unavailable",
        ]
    ):
        return "access"
    if any(
        token in lower
        for token in [
            "docker_paper_image_build_failed",
            "docker_image_build_failed",
            "docker_env_ensure_failed",
            "could not select device driver",
            "module not found",
            "modulenotfounderror",
            "importerror",
            "requirements",
            "pip install",
        ]
    ):
        return "environment"
    if any(
        token in lower
        for token in [
            "semantic_no_metrics",
            "metric_evidence_missing",
            "metric unavailable",
            "no metric",
            "missing metric",
            "empty accuracy",
        ]
    ):
        return "metric"
    if any(token in lower for token in ["alignment", "unmatched", "baseline"]):
        return "alignment"
    return "run"


def classify_task_failure(task: dict[str, Any]) -> str:
    if task.get("success"):
        return ""
    if task.get("skipped"):
        return ""
    semantic = str(task.get("semantic_failure") or "")
    if semantic == "semantic_no_metrics":
        return "metric"
    if "metric_artifact_path" in task and not task.get("metric_artifact"):
        return "metric"
    return _stage_from_error_text(
        " ".join(
            [
                str(semantic),
                str(task.get("error") or ""),
                str(task.get("stderr_tail") or ""),
                str(task.get("stdout_tail") or ""),
            ]
        )
    )


def classify_run_failure(run_result: dict[str, Any], judge: dict[str, Any]) -> str:
    if bool(run_result.get("success")) and judge.get("passed") is True:
        return ""
    if bool(run_result.get("skipped")):
        return "access"
    if run_result.get("semantic_failure") == "semantic_no_metrics" or run_result.get("inconclusive"):
        return "metric"
    text = json.dumps(run_result, ensure_ascii=False)
    stage = _stage_from_error_text(text)
    if stage != "run":
        return stage

    results = _as_list(judge.get("results"))
    for result in results:
        if not isinstance(result, dict):
            continue
        rtype = str(result.get("type") or "")
        if rtype in {"paper_metric_alignment", "paper_table_alignment"} and not result.get("passed"):
            return "alignment"
        if rtype in {"json_value", "csv_agg", "file_exists"} and not result.get("passed"):
            return "alignment"
    return "run" if not bool(run_result.get("success")) else ""


def _task_rows(run_result: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task in _as_list(run_result.get("tasks")):
        if not isinstance(task, dict):
            continue
        stage = classify_task_failure(task)
        expected = task.get("expected_metrics")
        rows.append(
            {
                "id": str(task.get("id") or ""),
                "family": str(task.get("family") or ""),
                "dataset": str(task.get("dataset") or ""),
                "method": str(task.get("method") or task.get("model") or task.get("variant") or ""),
                "status": _status_from_task(task),
                "failure_stage": stage,
                "semantic_failure": str(task.get("semantic_failure") or ""),
                "returncode": task.get("returncode"),
                "duration_sec": task.get("duration_sec"),
                "metric_artifact": str(task.get("metric_artifact") or ""),
                "has_expected_metrics": isinstance(expected, dict) and bool(expected),
                "expected_metric_keys": sorted(str(k) for k in expected) if isinstance(expected, dict) else [],
            }
        )
    return rows


def _token_usage() -> dict[str, Any]:
    path = run_stats.stats_path()
    if path is None or not path.exists():
        return {}
    payload = run_stats.with_totals(run_stats.read(path))
    modules = payload.get("modules") if isinstance(payload.get("modules"), dict) else {}
    execution = modules.get("execution") if isinstance(modules.get("execution"), dict) else {}
    return execution.get("token_usage") if isinstance(execution.get("token_usage"), dict) else {}


def build_execution_evidence_summary(
    *,
    state: dict[str, Any],
    run_dir: Path,
    artifacts_dir: Path,
) -> dict[str, Any]:
    events = _read_events(run_dir)
    run_result = state.get("run_result") if isinstance(state.get("run_result"), dict) else {}
    judge = state.get("judge") if isinstance(state.get("judge"), dict) else {}
    tasks = _task_rows(run_result)

    stage_counts = {stage: 0 for stage in FAILURE_STAGES}
    for task in tasks:
        stage = str(task.get("failure_stage") or "")
        if stage in stage_counts:
            stage_counts[stage] += 1
    run_failure_stage = classify_run_failure(run_result, judge)
    if run_failure_stage in stage_counts and not any(stage_counts.values()):
        stage_counts[run_failure_stage] += 1

    node_durations = _node_timings_from_state(state) or _node_timings_from_events(events)
    metric_artifacts = [t["metric_artifact"] for t in tasks if str(t.get("metric_artifact") or "")]
    alignment_results = [
        r
        for r in _as_list(judge.get("results"))
        if isinstance(r, dict) and str(r.get("type") or "") in {"paper_metric_alignment", "paper_table_alignment"}
    ]

    return {
        "schema_version": 1,
        "paper_key": str((state.get("config") or {}).get("paper_key") or ""),
        "status": str(state.get("status") or ""),
        "attempts": int(state.get("attempt") or 0),
        "run_success": bool(run_result.get("success")),
        "judge_passed": bool(judge.get("passed")),
        "failure_stage": run_failure_stage,
        "failure_stage_counts": stage_counts,
        "cost": {
            "event_wall_time_sec": round(_event_duration_sec(events), 3),
            "node_duration_sec": node_durations,
            "task_run_time_sec": round(
                sum(float(t.get("duration_sec") or 0.0) for t in tasks if t.get("duration_sec") is not None),
                3,
            ),
            "execution_llm_tokens": _token_usage(),
        },
        "funnel": {
            "tasks_total": len(tasks),
            "tasks_success": sum(1 for t in tasks if t.get("status") == "success"),
            "tasks_inconclusive": sum(1 for t in tasks if t.get("status") == "inconclusive"),
            "tasks_failed": sum(1 for t in tasks if t.get("status") == "failed"),
            "tasks_skipped": sum(1 for t in tasks if t.get("status") == "skipped"),
            "metric_artifacts": len(metric_artifacts),
            "alignment_results": len(alignment_results),
        },
        "tasks": tasks,
        "artifacts": {
            "metrics": metric_artifacts,
            "execution_evidence_path": str((artifacts_dir / "execution_evidence.json").relative_to(artifacts_dir)),
        },
    }

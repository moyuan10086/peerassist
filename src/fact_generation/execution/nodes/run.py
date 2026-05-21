from __future__ import annotations

import glob
import json
import os
import shutil
from pathlib import Path
from typing import Any

from util.fs import ensure_dir, safe_relpath, write_text
from util.recorder import append_event
from util.subprocess_runner import persist_command_result, run_command

from ..tools.docker import docker_ensure_paper_image, docker_run_paper_image
from ..tools.log_metrics import write_task_metric_artifact
from ..tools.results_tables import maybe_summarize_metrics_tables


def _load_tasks(tasks_path: str) -> list[dict[str, Any]]:
    """
    Tasks format (minimal):
    [
      {"id":"exp1", "cwd":"<paper_root>", "cmd":["python","script.py"], "timeout_sec": 3600, "artifact_paths":["relative/or/absolute"]},
      ...
    ]
    YAML supported if PyYAML is installed.
    """
    if not tasks_path:
        return []
    p = Path(tasks_path)
    if not p.exists():
        return []
    if p.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore

            data = yaml.safe_load(p.read_text(encoding="utf-8", errors="ignore"))
            return data if isinstance(data, list) else []
        except Exception:
            return []
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _expand_artifact_paths(cwd: str, paper_root: str, items: list[str]) -> list[Path]:
    out: list[Path] = []
    for raw in items:
        if not raw:
            continue
        s = str(raw).replace("{paper_root}", paper_root or ".")
        # relative patterns are resolved against cwd
        base = Path(cwd)
        pattern = s
        if not os.path.isabs(pattern):
            pattern = str(base / pattern)
        matches = glob.glob(pattern, recursive=True)
        for m in matches:
            p = Path(m)
            if p.exists():
                out.append(p)
    # de-dup
    uniq: list[Path] = []
    seen = set()
    for p in out:
        key = str(p.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def _ensure_task_output_roots(*, cwd: str, artifact_paths: list[Any]) -> None:
    for raw in artifact_paths:
        if not isinstance(raw, str):
            continue
        token = raw.replace("\\", "/").strip()
        if not token or token.startswith("{"):
            continue
        token = token.replace("{paper_root}/", "").replace("{paper_dir}/", "").replace("{run_dir}/", "")
        first = token.split("/", 1)[0].strip()
        if not first or any(ch in first for ch in "*?[]"):
            continue
        try:
            (Path(cwd) / first).mkdir(parents=True, exist_ok=True)
        except Exception:
            continue


def _task_result_base(task: dict[str, Any], task_id: str) -> dict[str, Any]:
    """Carry reviewer-facing task metadata into run_result."""

    keep = {
        "family",
        "dataset",
        "split",
        "eval_split",
        "method",
        "model",
        "variant",
        "claims",
        "expected_metrics",
        "paper_targets",
        "verification_target",
        "artifact_paths",
    }
    out: dict[str, Any] = {"id": task_id}
    for key in keep:
        if key in task:
            out[key] = task.get(key)
    return out


def run_node(state: dict[str, Any]) -> dict[str, Any]:
    # Soft wall-clock budget: short-circuit before launching another batch of
    # tasks if the per-paper budget is already exhausted.
    from ..graph import is_budget_exhausted

    if is_budget_exhausted(state):
        state["status"] = "partial"
        state["run_result"] = {"success": False, "partial": True, "reason": "paper_budget_exhausted"}
        return state

    cfg = state.get("config", {})
    run_info = state.get("run", {})
    run_dir = Path(run_info.get("dir") or "")
    logs_dir = Path(run_info.get("logs_dir") or (run_dir / "logs"))
    artifacts_dir = Path(run_info.get("artifacts_dir") or (run_dir / "artifacts"))

    ensure_dir(logs_dir)
    ensure_dir(artifacts_dir)

    paper_root = (cfg.get("paper_root") or "").strip()
    paper_dir = ""
    try:
        if paper_root:
            paper_dir = str(Path(paper_root).resolve().parent)
    except Exception:
        paper_dir = ""
    tasks_path = (cfg.get("tasks_path") or "").strip()
    dry_run = bool(cfg.get("dry_run"))
    attempt = int(state.get("attempt") or 0)
    docker_enabled = bool(cfg.get("docker_enabled", True))
    python_spec = str(cfg.get("python_spec") or "3.11").strip()

    tasks = _load_tasks(tasks_path)
    if not tasks:
        msg = "tasks file missing/invalid. Provide --tasks pointing to a yaml/json task list."
        append_event(run_dir, "run_error", {"error": msg, "tasks_path": tasks_path})
        state.setdefault("history", []).append({"kind": "run_error", "data": {"error": msg}})
        state["status"] = "failed"
        state["run_result"] = {"success": False, "error": msg}
        return state

    wheelhouse_candidates = []
    if tasks_path:
        wheelhouse_candidates.append(Path(tasks_path).resolve().parent / "wheelhouse_linux")
    baseline_path = str(cfg.get("baseline_path") or "").strip()
    if baseline_path:
        wheelhouse_candidates.append(Path(baseline_path).resolve().parent / "wheelhouse_linux")
    run_wheelhouse = run_dir / "wheelhouse_linux"
    for demo_wheelhouse in wheelhouse_candidates:
        if demo_wheelhouse.exists() and demo_wheelhouse.is_dir() and not run_wheelhouse.exists():
            try:
                shutil.copytree(demo_wheelhouse, run_wheelhouse)
                append_event(
                    run_dir,
                    "wheelhouse_copied",
                    {"src": str(demo_wheelhouse), "dst": str(run_wheelhouse)},
                )
            except Exception:
                pass
            break

    from ..graph import is_budget_exhausted as _bx

    results = []
    total_tasks = len(tasks)
    for idx, task in enumerate(tasks, 1):
        if _bx(state):
            append_event(
                run_dir,
                "run_partial",
                {"reason": "paper_budget_exhausted", "skipped_from_index": idx, "total": total_tasks},
            )
            state["status"] = "partial"
            state["run_result"] = {
                "success": False,
                "partial": True,
                "reason": "paper_budget_exhausted",
                "tasks": results,
                "tasks_skipped": total_tasks - idx + 1,
            }
            return state
        task_id = str(task.get("id") or f"task_{idx}")
        enabled = bool(task.get("enabled", True))
        pr_host = paper_root or "."
        pd_host = paper_dir or str(Path(pr_host).resolve().parent) if pr_host else "."
        if docker_enabled:
            # Only supported docker strategy: per-paper image build.
            pr = "/app"
            pd = "/app"
            rd = "/workspace/run_dir"
        else:
            pr = pr_host
            pd = pd_host
            rd = str(run_dir)
        # Host-side equivalents (for globbing/artifact copy).
        pr_h = pr_host
        pd_h = pd_host
        rd_h = str(run_dir)
        cwd_raw = str(task.get("cwd") or pr)
        cwd = cwd_raw.replace("{paper_root}", pr).replace("{paper_dir}", pd).replace("{run_dir}", rd)
        cwd_h = cwd_raw.replace("{paper_root}", pr_h).replace("{paper_dir}", pd_h).replace("{run_dir}", rd_h)
        cmd_raw = task.get("cmd")
        cmd = None
        if isinstance(cmd_raw, list) and all(isinstance(x, str) for x in cmd_raw):
            cmd = [
                str(x).replace("{paper_root}", pr).replace("{paper_dir}", pd).replace("{run_dir}", rd)
                for x in cmd_raw
            ]
        timeout_sec = int(task.get("timeout_sec") or 3600)
        if not isinstance(cmd, list) or not all(isinstance(x, str) for x in cmd):
            item = _task_result_base(task, task_id)
            item.update({"success": False, "error": "invalid_cmd"})
            results.append(item)
            continue

        # Docker mode always runs inside container; ignore per-task use_conda.
        use_conda = bool(task.get("use_conda", True))

        append_event(
            run_dir,
            "task_start",
            {
                "task": task_id,
                "task_index": idx,
                "task_total": total_tasks,
                "attempt": attempt,
                "cwd": cwd,
                "cmd": cmd,
                "timeout_sec": timeout_sec,
                "use_conda": use_conda,
                "enabled": enabled,
            },
        )

        if not enabled:
            item = _task_result_base(task, task_id)
            item.update({"success": True, "skipped": True})
            results.append(item)
            append_event(
                run_dir,
                "task_skipped",
                {
                    "task": task_id,
                    "task_index": idx,
                    "task_total": total_tasks,
                    "attempt": attempt,
                    "reason": "enabled=false",
                },
            )
            continue

        if dry_run:
            write_text(logs_dir / f"{task_id}_dry_run.txt", f"[DRY RUN] cwd={cwd}\ncmd={' '.join(cmd)}\n")
            item = _task_result_base(task, task_id)
            item.update({"success": True, "dry_run": True})
            results.append(item)
            append_event(
                run_dir,
                "task_done",
                {
                    "task": task_id,
                    "task_index": idx,
                    "task_total": total_tasks,
                    "attempt": attempt,
                    "success": True,
                    "dry_run": True,
                },
            )
            continue

        env = os.environ.copy()
        env["EXECUTION_RUN_DIR"] = str(run_dir)
        env["EXECUTION_ARTIFACT_DIR"] = str(artifacts_dir)
        env["EXECUTION_PAPER_ROOT"] = pr_host
        env["EXECUTION_ATTEMPT"] = str(attempt)
        env["EXECUTION_TASK_ID"] = task_id
        env["EXECUTION_OUTPUT_DIR"] = str(run_dir / "outputs" / task_id)
        env["EXECUTION_TASK_OUTPUT_DIR"] = str(run_dir / "outputs" / task_id)
        (run_dir / "outputs" / task_id).mkdir(parents=True, exist_ok=True)
        artifact_paths = task.get("artifact_paths") or []
        if isinstance(artifact_paths, list):
            _ensure_task_output_roots(cwd=cwd_h if docker_enabled else cwd, artifact_paths=artifact_paths)

        # Execute the task inside docker.
        if docker_enabled:
            # Only supported docker strategy: per-paper image build.
            ok_img, img_or_msg = docker_ensure_paper_image(
                cfg,
                paper_key=str(cfg.get("paper_key") or "paper"),
                paper_root_host=pr_host,
                python_spec=python_spec,
                timeout_sec=int(
                    cfg.get("docker_build_timeout_sec")
                    or os.environ.get("EXECUTION_DOCKER_BUILD_TIMEOUT_SEC")
                    or 3600
                ),
            )
            if not ok_img:
                state["status"] = "failed"
                state["run_result"] = {
                    "success": False,
                    "error": "docker_paper_image_build_failed",
                    "detail": img_or_msg,
                }
                return state
            docker_cmd = docker_run_paper_image(
                image=img_or_msg,
                paper_root_host=pr_host,
                run_dir_host=str(run_dir),
                cwd_container=cwd,
                cmd=cmd,
                env={
                    "EXECUTION_RUN_DIR": "/workspace/run_dir",
                    "EXECUTION_ARTIFACT_DIR": "/workspace/run_dir/artifacts",
                    "EXECUTION_PAPER_ROOT": "/app",
                    "EXECUTION_ATTEMPT": str(attempt),
                    "EXECUTION_TASK_ID": task_id,
                    "EXECUTION_OUTPUT_DIR": f"/workspace/run_dir/outputs/{task_id}",
                    "EXECUTION_TASK_OUTPUT_DIR": f"/workspace/run_dir/outputs/{task_id}",
                },
                gpus=str(cfg.get("docker_gpus") or os.environ.get("EXECUTION_DOCKER_GPUS") or "").strip()
                or None,
                shm_size=str(
                    cfg.get("docker_shm_size") or os.environ.get("EXECUTION_DOCKER_SHM_SIZE") or ""
                ).strip()
                or None,
                ipc=str(cfg.get("docker_ipc") or os.environ.get("EXECUTION_DOCKER_IPC") or "").strip()
                or None,
            )
            res = run_command(cmd=docker_cmd, cwd=str(run_dir), timeout_sec=timeout_sec, env=env)
        else:
            res = run_command(cmd=cmd, cwd=cwd, timeout_sec=timeout_sec, env=env)
        persist_command_result(res, logs_dir, prefix=f"{task_id}_attempt{attempt}")
        cmd_log = str(Path(logs_dir) / f"{task_id}_attempt{attempt}_command.txt")
        stdout_log = str(Path(logs_dir) / f"{task_id}_attempt{attempt}_stdout.log")
        stderr_log = str(Path(logs_dir) / f"{task_id}_attempt{attempt}_stderr.log")
        ok = res.returncode == 0
        item = _task_result_base(task, task_id)
        item.update(
            {
                "success": ok,
                "returncode": res.returncode,
                "duration_sec": res.duration_sec,
                "logs": {"command": cmd_log, "stdout": stdout_log, "stderr": stderr_log},
            }
        )
        metric_artifact = ""
        if ok:
            try:
                metric_artifact = write_task_metric_artifact(
                    artifacts_dir=artifacts_dir,
                    task_id=task_id,
                    task=task,
                    stdout=res.stdout or "",
                    stderr=res.stderr or "",
                )
            except Exception:
                metric_artifact = ""
        if metric_artifact:
            item["metric_artifact"] = metric_artifact
        results.append(item)
        append_event(
            run_dir,
            "task_done",
            {
                "task": task_id,
                "task_index": idx,
                "task_total": total_tasks,
                "attempt": attempt,
                "success": ok,
                "returncode": res.returncode,
                "duration_sec": res.duration_sec,
                "logs": {"command": cmd_log, "stdout": stdout_log, "stderr": stderr_log},
            },
        )

        if not ok:
            # stop at first failing task (simpler, deterministic); can be extended to continue.
            state["status"] = "failed"
            state["run_result"] = {
                "success": False,
                "failed_task": task_id,
                "task_index": idx,
                "task_total": total_tasks,
                "failed_task_cwd": cwd,
                "failed_task_cmd": cmd,
                "returncode": res.returncode,
                "stderr_tail": (res.stderr or "")[-2000:],
                "stdout_tail": (res.stdout or "")[-2000:],
                "logs": {"command": cmd_log, "stdout": stdout_log, "stderr": stderr_log},
            }
            append_event(run_dir, "run_failed", state["run_result"])
            state.setdefault("history", []).append({"kind": "run_failed", "data": state["run_result"]})
            return state

        # Archive artifacts (optional per task)
        if isinstance(artifact_paths, list) and artifact_paths:
            # In docker mode, the task ran against a host-mounted paper_root (now mounted at /app),
            # but artifact globbing must happen on the host paths.
            cwd_for_glob = cwd_h if docker_enabled else cwd
            paper_root_for_glob = pr_h if docker_enabled else pr
            expanded = _expand_artifact_paths(
                cwd=cwd_for_glob,
                paper_root=paper_root_for_glob,
                items=[
                    str(x)
                    .replace("{paper_root}", paper_root_for_glob)
                    .replace("{paper_dir}", (pd_h if docker_enabled else pd))
                    .replace("{run_dir}", str(run_dir))
                    for x in artifact_paths
                    if isinstance(x, str | int | float)
                ],
            )
            copied = []
            for p in expanded:
                try:
                    # preserve relative path under paper_root if possible, otherwise under cwd
                    rel = None
                    try:
                        root_for_rel = Path(pr_h if docker_enabled else pr).resolve()
                        cwd_for_rel = Path(cwd_h if docker_enabled else cwd).resolve()
                        if str(p.resolve()).lower().startswith(str(root_for_rel).lower()):
                            rel = safe_relpath(p, root_for_rel)
                        else:
                            rel = safe_relpath(p, cwd_for_rel)
                    except Exception:
                        rel = p.name
                    dest = Path(artifacts_dir) / rel
                    ensure_dir(dest.parent)
                    if p.is_dir():
                        # copy tree into destination parent with the folder name
                        if dest.exists():
                            import shutil

                            shutil.rmtree(dest, ignore_errors=True)
                        import shutil

                        shutil.copytree(p, dest, ignore=shutil.ignore_patterns(".git", "__pycache__"))
                    else:
                        import shutil

                        shutil.copy2(p, dest)
                    copied.append(str(rel).replace("\\", "/"))
                except Exception:
                    continue
            if copied:
                append_event(
                    run_dir,
                    "artifacts_archived",
                    {
                        "task": task_id,
                        "task_index": idx,
                        "task_total": total_tasks,
                        "count": len(copied),
                        "paths": copied,
                    },
                )

    # Optional: generic summarization (if metrics JSONs were produced into artifacts).
    try:
        maybe_summarize_metrics_tables(cfg=cfg, run_dir=run_dir, artifacts_dir=artifacts_dir)
    except Exception:
        pass

    state["status"] = "running"
    state["run_result"] = {"success": True, "tasks": results}
    append_event(run_dir, "run_ok", {"tasks": results, "task_total": total_tasks})
    state.setdefault("history", []).append({"kind": "run_ok", "data": {"tasks": results}})
    return state

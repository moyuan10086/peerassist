from __future__ import annotations

import glob
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from util.fs import ensure_dir, safe_relpath, write_text
from util.recorder import append_event
from util.subprocess_runner import persist_command_result, run_command

from ..tools.docker import _docker_env_passthrough, docker_ensure_paper_image, docker_run_paper_image
from ..tools.log_metrics import extract_metrics_from_text, write_task_metric_artifact
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


def _archive_task_artifacts(
    *,
    artifact_paths: list[Any],
    artifacts_dir: Path,
    run_dir: Path,
    task_id: str,
    task_index: int,
    task_total: int,
    docker_enabled: bool,
    cwd: str,
    cwd_h: str,
    pr: str,
    pr_h: str,
    pd: str,
    pd_h: str,
) -> list[str]:
    if not isinstance(artifact_paths, list) or not artifact_paths:
        return []
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
    copied: list[str] = []
    for p in expanded:
        try:
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
                if dest.exists():
                    shutil.rmtree(dest, ignore_errors=True)
                shutil.copytree(p, dest, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            else:
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
                "task_index": task_index,
                "task_total": task_total,
                "count": len(copied),
                "paths": copied,
            },
        )
    return copied


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
        "disabled_reason",
        "requires_external_api",
        "static_import_issues",
        "static_entrypoint_issues",
    }
    out: dict[str, Any] = {"id": task_id}
    for key in keep:
        if key in task:
            out[key] = task.get(key)
    return out


def _semantic_runtime_failure(stdout: str, stderr: str) -> str:
    text = f"{stdout or ''}\n{stderr or ''}"
    text_lower = text.lower()
    if "Traceback (most recent call last):" in text:
        return "python_traceback_in_output"
    for marker, reason in [
        ("system startup failed", "semantic_system_startup_failed"),
        ("error loading system:", "semantic_system_load_failed"),
        ("failed to load mimic data", "semantic_missing_mimic_data"),
        ("preprocessed data file not found", "semantic_missing_preprocessed_data"),
        ("please run mimic_processor.py first", "semantic_missing_preprocessed_data"),
        ("error(s) in loading state_dict", "semantic_checkpoint_state_dict_mismatch"),
    ]:
        if marker in text_lower:
            return reason
    if "size mismatch for " in text_lower and ("state_dict" in text_lower or "checkpoint" in text_lower):
        return "semantic_checkpoint_state_dict_mismatch"
    for token in [
        "ModuleNotFoundError:",
        "ImportError:",
        "FileNotFoundError:",
        "SyntaxError:",
    ]:
        if token in text:
            return token.rstrip(":").lower()
    return ""


def _task_family(task: dict[str, Any], task_id: str = "") -> str:
    ident = task_id.lower()
    if ident.startswith("smoke_") or "smoke" in ident:
        return "smoke"
    family = str(task.get("family") or "").strip().lower()
    if family:
        return family
    if ident.startswith("eval_") or ident.startswith("evaluate_"):
        return "eval"
    if ident.startswith("reproduce_") or ident.startswith("reproduction_"):
        return "reproduce"
    if ident.startswith("train_"):
        return "train"
    return ""


def _paper_targets_have_metrics(task: dict[str, Any]) -> bool:
    targets = task.get("paper_targets")
    if not isinstance(targets, list):
        return False
    return any(isinstance(t, dict) and isinstance(t.get("metrics"), dict) and bool(t["metrics"]) for t in targets)


def _looks_like_empty_metric_summary(text: str) -> bool:
    if not text:
        return False
    if re.search(r"(?is)accumulated\s+accuracy\s+scores\s*:\s*(?:\r?\n\s*)*(?:-+|finished|\Z)", text):
        return True
    return bool(re.search(r"(?is)accumulated\s+accuracy\s+scores\s*:\s*(?:\{\s*\}|\[\s*\])", text))


def _semantic_metric_failure(
    *,
    task: dict[str, Any],
    task_id: str,
    stdout: str,
    stderr: str,
    metric_artifact: str,
) -> str:
    text = "\n".join([stdout or "", stderr or ""])
    family = _task_family(task, task_id)
    expected = task.get("expected_metrics") if isinstance(task.get("expected_metrics"), dict) else {}
    metrics = extract_metrics_from_text(text, expected_metrics=expected)
    empty_summary = _looks_like_empty_metric_summary(text)
    if metric_artifact and metrics and not empty_summary:
        return ""
    metric_artifact_declared = bool(str(task.get("metric_artifact_path") or "").strip())
    has_metric_contract = bool(expected) or _paper_targets_have_metrics(task) or (
        metric_artifact_declared and family not in {"smoke", "prepare"}
    )
    if has_metric_contract and (not metrics or empty_summary):
        return "semantic_no_metrics"
    if family in {"eval", "evaluate", "evaluation", "reproduce", "reproduction", "benchmark"}:
        if not metrics or empty_summary:
            return "semantic_no_metrics"
    return ""


def _run_dir_venv_python(run_dir: str | Path | None) -> str:
    if not run_dir:
        return ""
    root = Path(run_dir)
    marker = root / ".host_venv_path"
    if marker.exists():
        try:
            venv_root = Path(marker.read_text(encoding="utf-8", errors="ignore").strip())
            candidates = [
                venv_root / "Scripts" / "python.exe",
                venv_root / "bin" / "python",
                venv_root / "bin" / "python3",
            ]
            for candidate in candidates:
                if candidate.exists():
                    return str(candidate)
        except Exception:
            pass
    candidates = [
        root / ".venv" / "Scripts" / "python.exe",
        root / ".venv" / "bin" / "python",
        root / ".venv" / "bin" / "python3",
    ]
    for candidate in candidates:
        try:
            if candidate.exists():
                return str(candidate)
        except Exception:
            continue
    return ""


def _resolve_host_python_cmd(cmd: list[str], run_dir: str | Path | None = None) -> list[str]:
    if not cmd:
        return cmd
    first = str(cmd[0] or "").strip().lower()
    python_exe = _run_dir_venv_python(run_dir) or sys.executable
    if first in {"python", "python3"}:
        return [python_exe, *cmd[1:]]
    if (
        first == "uv"
        and len(cmd) >= 3
        and str(cmd[1] or "").strip().lower() == "run"
        and str(cmd[2] or "").strip().lower() in {"python", "python3"}
        and not shutil.which("uv")
    ):
        return [python_exe, *cmd[3:]]
    if os.name == "nt" and first in {"bash", "sh"}:
        explicit = str(os.environ.get("EXECUTION_BASH_PATH") or os.environ.get("FACTREVIEW_BASH_PATH") or "").strip()
        candidates = [
            explicit,
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
        ]
        found = next((p for p in candidates if p and Path(p).exists()), "")
        if not found:
            resolved = shutil.which(cmd[0])
            if resolved and "system32" not in resolved.lower():
                found = resolved
        if found:
            return [found, *cmd[1:]]
    return cmd


def _path_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except Exception:
        return False


def _extra_pythonpath_tokens(cfg: dict[str, Any]) -> list[str]:
    raw = (
        cfg.get("extra_pythonpath_dirs")
        or cfg.get("extra_pythonpath")
        or os.environ.get("EXECUTION_EXTRA_PYTHONPATH")
        or os.environ.get("FACTREVIEW_EXTRA_PYTHONPATH")
        or ""
    )
    if isinstance(raw, (list, tuple, set)):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw or "").strip()
    if not text:
        return []
    tokens: list[str] = []
    for chunk in text.replace("\n", os.pathsep).split(os.pathsep):
        for item in chunk.split(","):
            item = item.strip()
            if item:
                tokens.append(item)
    return tokens


def _repo_pythonpath_parts(paper_root: str | Path, cfg: dict[str, Any] | None = None) -> list[str]:
    root = Path(paper_root or ".")
    paths: list[str] = []
    for path in [root, root / "src", root / "code", root / "python", root / "lib"]:
        try:
            if path.is_dir():
                paths.append(str(path.resolve()))
        except Exception:
            continue
    for raw in _extra_pythonpath_tokens(cfg or {}):
        try:
            path = Path(raw)
            if not path.is_absolute():
                path = root / path
            if path.is_dir() and _path_inside(path, root):
                paths.append(str(path.resolve()))
        except Exception:
            continue
    deduped: list[str] = []
    seen: set[str] = set()
    for path in paths:
        key = path.lower() if os.name == "nt" else path
        if key in seen:
            continue
        deduped.append(path)
        seen.add(key)
    return deduped


def _repo_container_pythonpath(paper_root: str | Path, cfg: dict[str, Any] | None = None) -> str:
    root = Path(paper_root or ".")
    parts = ["/app"]
    for rel in ["src", "code", "python", "lib"]:
        try:
            if (root / rel).is_dir():
                parts.append(f"/app/{rel}")
        except Exception:
            continue
    for raw in _extra_pythonpath_tokens(cfg or {}):
        try:
            path = Path(raw)
            if not path.is_absolute():
                path = root / path
            if not path.is_dir() or not _path_inside(path, root):
                continue
            rel = path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()
            if rel and rel != ".":
                parts.append(f"/app/{rel}")
        except Exception:
            continue
    deduped: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if part in seen:
            continue
        deduped.append(part)
        seen.add(part)
    return ":".join(deduped)


def _apply_execution_python_env_defaults(env: dict[str, str]) -> None:
    # Paper code often writes Unicode logs while stdout is captured by a pipe.
    # On Windows, Python may otherwise pick a legacy code page and fail before
    # the task can complete.
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")


def _run_local_jupyter_data_dirs(run_dir: str | Path | None, cfg: dict[str, Any]) -> list[str]:
    prefixes: list[str] = []
    raw_prefix = str(cfg.get("jupyter_kernel_prefix") or "").strip()
    if raw_prefix:
        prefixes.append(raw_prefix)
    if run_dir:
        marker = Path(run_dir) / ".jupyter_kernel_prefix"
        if marker.exists():
            try:
                marked = marker.read_text(encoding="utf-8", errors="ignore").strip()
                if marked:
                    prefixes.append(marked)
            except Exception:
                pass

    dirs: list[str] = []
    for raw in prefixes:
        try:
            prefix = Path(raw).expanduser()
            dirs.append(str((prefix / "share" / "jupyter").resolve(strict=False)))
        except Exception:
            continue

    raw_dirs = cfg.get("jupyter_path_dirs") or cfg.get("jupyter_paths") or ""
    if isinstance(raw_dirs, (list, tuple, set)):
        tokens = [str(item).strip() for item in raw_dirs if str(item).strip()]
    else:
        tokens = []
        for chunk in str(raw_dirs or "").replace("\n", os.pathsep).split(os.pathsep):
            for item in chunk.split(","):
                item = item.strip()
                if item:
                    tokens.append(item)
    for raw in tokens:
        try:
            dirs.append(str(Path(raw).expanduser().resolve(strict=False)))
        except Exception:
            continue

    deduped: list[str] = []
    seen: set[str] = set()
    for path in dirs:
        key = path.lower() if os.name == "nt" else path
        if key in seen:
            continue
        deduped.append(path)
        seen.add(key)
    return deduped


def _apply_run_local_jupyter_env(env: dict[str, str], run_dir: str | Path | None, cfg: dict[str, Any]) -> None:
    data_dirs = _run_local_jupyter_data_dirs(run_dir, cfg)
    if not data_dirs:
        return
    existing = [part for part in str(env.get("JUPYTER_PATH") or "").split(os.pathsep) if part]
    parts = list(existing)
    for path in reversed(data_dirs):
        if path not in parts:
            parts.insert(0, path)
    env["JUPYTER_PATH"] = os.pathsep.join(parts)


def _truthy_env(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _task_timeouts_disabled() -> bool:
    return _truthy_env("EXECUTION_DISABLE_TASK_TIMEOUT") or _truthy_env("FACTREVIEW_DISABLE_TASK_TIMEOUT")


def _effective_task_timeout(task: dict[str, Any], cfg: dict[str, Any]) -> int:
    if _task_timeouts_disabled():
        return 0
    raw = cfg.get("task_timeout_sec")
    if raw in (None, ""):
        raw = os.environ.get("EXECUTION_TASK_TIMEOUT_SEC") or os.environ.get("FACTREVIEW_TASK_TIMEOUT_SEC")
    if raw not in (None, ""):
        try:
            return max(int(raw), 0)
        except Exception:
            pass
    try:
        return max(int(task.get("timeout_sec") or 3600), 0)
    except Exception:
        return 3600


def _disable_embedded_command_timeouts(cmd: list[str]) -> list[str]:
    rewritten: list[str] = []
    skip_next = False
    for index, token in enumerate(cmd):
        if skip_next:
            skip_next = False
            continue
        text = str(token)
        if text == "--ExecutePreprocessor.timeout":
            rewritten.extend([text, "-1"])
            if index + 1 < len(cmd):
                skip_next = True
            continue
        text = re.sub(r"(--ExecutePreprocessor\.timeout=)(?:-?\d+|None|null)", r"\g<1>-1", text)
        text = re.sub(r"(\bExecutePreprocessor\.timeout\s*=\s*)(?:-?\d+|None|null)", r"\g<1>-1", text)
        rewritten.append(text)
    return rewritten


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
        timeout_sec = _effective_task_timeout(task, cfg)
        if not isinstance(cmd, list) or not all(isinstance(x, str) for x in cmd):
            item = _task_result_base(task, task_id)
            item.update({"success": False, "error": "invalid_cmd"})
            results.append(item)
            continue
        if timeout_sec <= 0:
            cmd = _disable_embedded_command_timeouts(cmd)

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
            reason = str(task.get("disabled_reason") or "enabled=false")
            append_event(
                run_dir,
                "task_skipped",
                {
                    "task": task_id,
                    "task_index": idx,
                    "task_total": total_tasks,
                    "attempt": attempt,
                    "reason": reason,
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
        _apply_execution_python_env_defaults(env)
        env["EXECUTION_RUN_DIR"] = str(run_dir)
        env["EXECUTION_ARTIFACT_DIR"] = str(artifacts_dir)
        env["EXECUTION_PAPER_ROOT"] = pr_host
        env["EXECUTION_ATTEMPT"] = str(attempt)
        env["EXECUTION_TASK_ID"] = task_id
        env["EXECUTION_OUTPUT_DIR"] = str(run_dir / "outputs" / task_id)
        env["EXECUTION_TASK_OUTPUT_DIR"] = str(run_dir / "outputs" / task_id)
        existing_pythonpath = str(env.get("PYTHONPATH") or "")
        pythonpath_parts = [part for part in existing_pythonpath.split(os.pathsep) if part]
        repo_pythonpath = _repo_pythonpath_parts(pr_host, cfg)
        for path in reversed(repo_pythonpath):
            if path not in pythonpath_parts:
                pythonpath_parts.insert(0, path)
        env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
        if not docker_enabled:
            host_venv_python = _run_dir_venv_python(run_dir)
            if host_venv_python:
                host_venv_bin = str(Path(host_venv_python).resolve().parent)
                path_parts = [part for part in str(env.get("PATH") or "").split(os.pathsep) if part]
                if host_venv_bin not in path_parts:
                    path_parts.insert(0, host_venv_bin)
                env["PATH"] = os.pathsep.join(path_parts)
                env.setdefault("VIRTUAL_ENV", str(Path(host_venv_bin).parent))
            _apply_run_local_jupyter_env(env, run_dir, cfg)
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
                    if cfg.get("docker_build_timeout_sec") not in (None, "")
                    else os.environ.get("EXECUTION_DOCKER_BUILD_TIMEOUT_SEC", "3600")
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
                    "PYTHONPATH": _repo_container_pythonpath(pr_host, cfg),
                    "PYTHONIOENCODING": "utf-8",
                    "PYTHONUTF8": "1",
                },
                env_passthrough=_docker_env_passthrough(cfg),
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
            res = run_command(cmd=_resolve_host_python_cmd(cmd, run_dir=run_dir), cwd=cwd, timeout_sec=timeout_sec, env=env)
        persist_command_result(res, logs_dir, prefix=f"{task_id}_attempt{attempt}")
        cmd_log = str(Path(logs_dir) / f"{task_id}_attempt{attempt}_command.txt")
        stdout_log = str(Path(logs_dir) / f"{task_id}_attempt{attempt}_stdout.log")
        stderr_log = str(Path(logs_dir) / f"{task_id}_attempt{attempt}_stderr.log")
        semantic_failure = _semantic_runtime_failure(res.stdout or "", res.stderr or "")
        ok = res.returncode == 0 and not semantic_failure
        item = _task_result_base(task, task_id)
        item.update(
            {
                "success": ok,
                "returncode": res.returncode,
                "duration_sec": res.duration_sec,
                "logs": {"command": cmd_log, "stdout": stdout_log, "stderr": stderr_log},
            }
        )
        if res.returncode != 0 or semantic_failure:
            item["stderr_tail"] = (res.stderr or "")[-2000:]
            item["stdout_tail"] = (res.stdout or "")[-2000:]
        if semantic_failure:
            item["semantic_failure"] = semantic_failure
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
            metric_failure = _semantic_metric_failure(
                task=task,
                task_id=task_id,
                stdout=res.stdout or "",
                stderr=res.stderr or "",
                metric_artifact=metric_artifact,
            )
            if metric_failure:
                semantic_failure = metric_failure
                ok = False
        if metric_artifact:
            item["metric_artifact"] = metric_artifact
        item["success"] = ok
        if semantic_failure:
            item["semantic_failure"] = semantic_failure
        results.append(item)
        task_event = {
            "task": task_id,
            "task_index": idx,
            "task_total": total_tasks,
            "attempt": attempt,
            "success": ok,
            "returncode": res.returncode,
            "duration_sec": res.duration_sec,
            "timeout_sec": timeout_sec,
            "logs": {"command": cmd_log, "stdout": stdout_log, "stderr": stderr_log},
        }
        if semantic_failure:
            task_event["semantic_failure"] = semantic_failure
        append_event(run_dir, "task_done", task_event)

        archived_artifacts = _archive_task_artifacts(
            artifact_paths=artifact_paths,
            artifacts_dir=artifacts_dir,
            run_dir=run_dir,
            task_id=task_id,
            task_index=idx,
            task_total=total_tasks,
            docker_enabled=docker_enabled,
            cwd=cwd,
            cwd_h=cwd_h,
            pr=pr,
            pr_h=pr_h,
            pd=pd,
            pd_h=pd_h,
        )
        declared_metric_artifact = str(task.get("metric_artifact_path") or "").replace("\\", "/").strip()
        if (not metric_artifact) and declared_metric_artifact and declared_metric_artifact in archived_artifacts:
            metric_artifact = declared_metric_artifact
            item["metric_artifact"] = metric_artifact

        if not ok:
            # stop at first failing task (simpler, deterministic); can be extended to continue.
            missing_metrics = semantic_failure == "semantic_no_metrics"
            state["status"] = "inconclusive" if missing_metrics else "failed"
            failure_result = {
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
                "tasks": results,
            }
            if missing_metrics:
                failure_result.update(
                    {
                        "inconclusive": True,
                        "reason": "metric_evidence_missing",
                    }
                )
            if semantic_failure:
                failure_result["semantic_failure"] = semantic_failure
            state["run_result"] = failure_result
            event_kind = "run_inconclusive" if missing_metrics else "run_failed"
            append_event(run_dir, event_kind, state["run_result"])
            state.setdefault("history", []).append({"kind": event_kind, "data": state["run_result"]})
            return state

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

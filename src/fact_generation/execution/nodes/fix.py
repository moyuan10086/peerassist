from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from llm.client import llm_json, resolve_llm_config
from util.fs import ensure_dir, write_text
from util.recorder import append_event
from util.subprocess_runner import persist_command_result, run_command

from ..tools.docker import _docker_env_passthrough, docker_ensure_paper_image, docker_run_paper_image


def _extract_missing_module(stderr: str) -> str | None:
    # ModuleNotFoundError: No module named 'xxx'
    # python -m pytest style: "<python>: No module named pytest"
    m = re.search(
        r"No module named\s+(?:['\"](?P<quoted>[^'\"]+)['\"]|(?P<bare>[A-Za-z_][A-Za-z0-9_.]*))",
        stderr or "",
    )
    if m:
        return m.group("quoted") or m.group("bare")
    return None


def _extract_missing_jupyter_kernel(text: str) -> str | None:
    m = re.search(
        r"(?:NoSuchKernel:\s*)?No such kernel named\s+(?:['\"](?P<quoted>[A-Za-z0-9_.-]+)['\"]|(?P<bare>[A-Za-z0-9_.-]+))",
        str(text or ""),
        re.IGNORECASE,
    )
    if not m:
        return None
    kernel = (m.group("quoted") or m.group("bare") or "").strip().rstrip(".,;:")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", kernel):
        return None
    return kernel


def _extract_failed_import_modules(text: str) -> list[str]:
    raw = str(text or "").strip()
    modules: list[str] = []
    skip_keys = {"error", "errors", "imports", "modules", "ok", "status", "version"}

    def collect_from_obj(obj: Any) -> None:
        if isinstance(obj, dict):
            missing_values = obj.get("missing_modules") or obj.get("missing")
            if isinstance(missing_values, list):
                for value in missing_values:
                    root = _module_root(str(value))
                    if root and root not in skip_keys and _is_external_import_root(root):
                        modules.append(root)
            module_status = obj.get("modules")
            if isinstance(module_status, dict):
                for key, value in module_status.items():
                    root = _module_root(str(key))
                    if root and root not in skip_keys and _is_external_import_root(root) and value is False:
                        modules.append(root)
            imports = obj.get("imports")
            if isinstance(imports, dict):
                for key, value in imports.items():
                    root = _module_root(str(key))
                    if not root or root in skip_keys or not _is_external_import_root(root):
                        continue
                    if value is False or (isinstance(value, dict) and value.get("ok") is False):
                        modules.append(root)
            for value in obj.values():
                collect_from_obj(value)
        elif isinstance(obj, list):
            for value in obj:
                collect_from_obj(value)

    if raw:
        try:
            collect_from_obj(json.loads(raw))
        except Exception:
            pass
    for match in re.finditer(
        r"\b(?:missing[_\s-]*modules?|missing)\b['\"]?\s*[:=]?\s*\[(?P<items>[^\]]{1,2000})\]",
        raw,
        re.IGNORECASE | re.DOTALL,
    ):
        for module in _parse_import_list(
            ",".join(re.findall(r"['\"]([A-Za-z_][A-Za-z0-9_.]*)['\"]", match.group("items")))
        ):
            if module not in skip_keys:
                modules.append(module)
    for match in re.finditer(r"['\"](?P<module>[A-Za-z_][A-Za-z0-9_.]*)['\"]\s*:\s*false\b", raw, re.IGNORECASE):
        root = _module_root(match.group("module"))
        if root and root not in skip_keys and _is_external_import_root(root):
            modules.append(root)
    for match in re.finditer(
        r"['\"](?P<module>[A-Za-z_][A-Za-z0-9_.]*)['\"]\s*:\s*\{[^{}]{0,600}?['\"]ok['\"]\s*:\s*false\b",
        raw,
        re.IGNORECASE | re.DOTALL,
    ):
        root = _module_root(match.group("module"))
        if root and root not in skip_keys and _is_external_import_root(root):
            modules.append(root)
    return _dedupe_modules(modules)


_MODULE_TO_PIP = {
    # common mismatches
    "sklearn": "scikit-learn",
    "cv2": "opencv-python",
    "PIL": "pillow",
    "pyDOE": "pyDOE2",
    "pydoe": "pyDOE2",
    "torchdata.datapipes": "torchdata<0.9",
    "torchdata.dataloader2": "torchdata<0.9",
    "yaml": "pyyaml",
}

_MODULE_IMPORT_ALIAS_TARGETS = {
    # pyDOE2 installs as `pyDOE2`, while some older papers import `pyDOE`.
    "pyDOE": ("pyDOE2", "pydoe"),
}


def _extract_missing_file(stderr: str) -> str | None:
    # Windows python: can't open file 'C:\\path\\to\\x.py': [Errno 2] No such file or directory
    m = re.search(r"can't open file ['\"]([^'\"]+)['\"]", stderr or "", flags=re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def _pick_smoke_entrypoint(paper_root: str) -> str:
    """
    Pick a best-effort entry script for `python <script> --help`.
    Keep it conservative: only choose from known common filenames.
    """
    pr = Path(paper_root or ".")
    for name in ["launcher.py", "run.py", "eval.py", "main.py", "app.py"]:
        if (pr / name).exists():
            return name
    return ""


def _find_unique_repo_file_by_name(paper_root: str, filename: str) -> str:
    name = Path(str(filename or "")).name
    if not name:
        return ""
    repo = Path(paper_root or ".")
    if not repo.exists():
        return ""
    matches: list[Path] = []
    try:
        for path in repo.rglob(name):
            if len(matches) > 1:
                break
            if not path.is_file():
                continue
            try:
                rel = path.relative_to(repo)
            except Exception:
                rel = path
            if _path_has_skipped_part(rel):
                continue
            matches.append(path)
    except Exception:
        return ""
    if len(matches) != 1:
        return ""
    try:
        return matches[0].relative_to(repo).as_posix()
    except Exception:
        return str(matches[0]).replace("\\", "/")


def _patch_tasks_missing_script(
    *,
    cfg: dict[str, Any],
    paper_root: str,
    fixes_dir: Path,
    attempt: int,
    missing_file: str,
) -> tuple[bool, dict[str, Any]]:
    tasks_path = str(cfg.get("tasks_path") or "").strip()
    missing_name = Path(str(missing_file or "")).name
    replacement = _find_unique_repo_file_by_name(paper_root, missing_name)
    if not tasks_path or not missing_name or not replacement or not Path(tasks_path).exists():
        return False, {"missing": missing_name, "replacement": replacement}
    try:
        path = Path(tasks_path)
        original = path.read_text(encoding="utf-8", errors="ignore")
        write_text(fixes_dir / f"fix_{attempt:03d}_tasks_before.txt", original)
        patched = original
        for needle in [str(missing_file).replace("\\", "/"), str(missing_file), missing_name]:
            if needle and needle in patched:
                patched = patched.replace(needle, replacement)
                break
        if patched == original:
            return False, {"missing": missing_name, "replacement": replacement, "reason": "tasks_text_no_match"}
        write_text(path, patched)
        write_text(
            fixes_dir / f"fix_{attempt:03d}_tasks_patch_missing_script.txt",
            "Patched tasks file to fix missing script path:\n"
            f"- path: {tasks_path}\n"
            f"- missing: {missing_file}\n"
            f"- using: {replacement}\n",
        )
        return True, {"path": tasks_path, "missing": missing_name, "replacement": replacement}
    except Exception as exc:
        return False, {"missing": missing_name, "replacement": replacement, "error": f"{type(exc).__name__}: {exc}"}


def _task_family_for_unrecoverable_skip(task: dict[str, Any], task_id: str) -> str:
    family = str(task.get("family") or "").strip().lower()
    ident = str(task_id or "").strip().lower()
    if family:
        return family
    if "smoke" in ident or ident.startswith("check_"):
        return "smoke"
    if ident.startswith(("reproduce_", "reproduction_")):
        return "reproduce"
    if ident.startswith(("eval_", "evaluate_")):
        return "eval"
    if ident.startswith("benchmark_"):
        return "benchmark"
    if ident.startswith("train_"):
        return "train"
    return ""


def _load_tasks_for_edit(tasks_path: Path) -> tuple[list[dict[str, Any]], str]:
    suffix = tasks_path.suffix.lower()
    text = tasks_path.read_text(encoding="utf-8", errors="ignore")
    if suffix in {".yaml", ".yml"}:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
        return (data if isinstance(data, list) else []), "yaml"
    data = json.loads(text)
    return (data if isinstance(data, list) else []), "json"


def _write_tasks_for_edit(tasks_path: Path, tasks: list[dict[str, Any]], fmt: str) -> None:
    if fmt == "yaml":
        import yaml  # type: ignore

        write_text(tasks_path, yaml.safe_dump(tasks, sort_keys=False, allow_unicode=True))
        return
    write_text(tasks_path, json.dumps(tasks, ensure_ascii=False, indent=2) + "\n")


def _disable_unrecoverable_failed_task(
    *,
    cfg: dict[str, Any],
    run_dir: Path,
    fixes_dir: Path,
    failed_task: Any,
    run_result: dict[str, Any],
    plan: dict[str, Any] | None,
    reason: str,
    detail: str,
    attempt: int,
) -> bool:
    tasks_path_raw = str(cfg.get("tasks_path") or "").strip()
    task_id = str(failed_task or "").strip()
    if not tasks_path_raw or not task_id:
        return False
    tasks_path = Path(tasks_path_raw)
    if not tasks_path.exists() or not tasks_path.is_file():
        return False
    try:
        tasks, fmt = _load_tasks_for_edit(tasks_path)
    except Exception:
        return False
    if not tasks:
        return False

    target_index = -1
    for idx, task in enumerate(tasks):
        if isinstance(task, dict) and str(task.get("id") or "").strip() == task_id:
            target_index = idx
            break
    if target_index < 0:
        return False
    task = tasks[target_index]
    family = _task_family_for_unrecoverable_skip(task, task_id)
    if family in {"", "smoke", "prepare", "setup", "install"}:
        return False
    if family == "train" and str(cfg.get("continue_after_unfixable_train") or "").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return False
    has_later_enabled_task = any(
        isinstance(item, dict) and bool(item.get("enabled", True)) for item in tasks[target_index + 1 :]
    )
    if not has_later_enabled_task:
        return False

    try:
        write_text(fixes_dir / f"fix_{attempt:03d}_tasks_before_disable_unrecoverable.txt", tasks_path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        pass
    disabled_reason = f"unrecoverable_after_fix:{reason or 'no_safe_fix'}"
    if detail:
        compact_detail = re.sub(r"\s+", " ", detail).strip()
        if compact_detail:
            disabled_reason = f"{disabled_reason}:{compact_detail[:160]}"
    task["enabled"] = False
    task["disabled_reason"] = disabled_reason
    task["unrecoverable_failure"] = {
        "attempt": attempt,
        "reason": reason,
        "detail": detail,
        "risk": _plan_risk(plan or {}) if isinstance(plan, dict) else "",
        "blocked_by": _plan_blocked_by(plan or {}) if isinstance(plan, dict) else "",
        "root_cause": str((plan or {}).get("root_cause") or "")[:600] if isinstance(plan, dict) else "",
        "failed_task_index": run_result.get("task_index"),
        "semantic_failure": run_result.get("semantic_failure") or "",
    }
    try:
        _write_tasks_for_edit(tasks_path, tasks, fmt)
    except Exception as exc:
        append_event(
            run_dir,
            "fix_disable_unrecoverable_task_failed",
            {"task": task_id, "reason": reason, "error": f"{type(exc).__name__}: {exc}"},
        )
        return False
    write_text(
        fixes_dir / f"fix_{attempt:03d}_disable_unrecoverable_task.txt",
        "Disabled one unrecoverable task so independent later tasks can continue.\n"
        f"- task: {task_id}\n"
        f"- family: {family}\n"
        f"- reason: {reason}\n"
        f"- detail: {detail}\n"
        f"- tasks_path: {tasks_path}\n",
    )
    append_event(
        run_dir,
        "fix_disable_unrecoverable_task",
        {"task": task_id, "family": family, "reason": reason, "detail": detail, "tasks_path": str(tasks_path)},
    )
    return True


def _windows_bash_executable() -> str:
    if os.name != "nt":
        return ""
    explicit = str(os.environ.get("EXECUTION_BASH_PATH") or os.environ.get("FACTREVIEW_BASH_PATH") or "").strip()
    candidates = [
        explicit,
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
    ]
    found = next((p for p in candidates if p and Path(p).exists()), "")
    if found:
        return found
    resolved = shutil.which("bash")
    if resolved and "system32" not in resolved.lower():
        return resolved
    return ""


def _single_string_looks_posix_shell(command: str) -> bool:
    s = str(command or "")
    return any(
        marker in s
        for marker in [
            "&&",
            "||",
            "<<",
            "$(",
            "`",
            "\n",
            "export ",
            "mkdir -p",
            "python - <<",
        ]
    )


def _normalize_llm_cmd_for_platform(cmd: list[str]) -> list[str]:
    """
    LLMs often emit a single-string shell command (a one-item list containing spaces).
    Our runner uses shell=False, so wrap these for the current platform.
    """
    if not cmd:
        return cmd
    if os.name == "nt":
        exe = Path(str(cmd[0] or "")).name.lower()
        if exe in {"bash", "sh"}:
            bash = _windows_bash_executable()
            if bash:
                return [bash, *cmd[1:]]
    # If cmd is a single string with spaces, treat it as a shell command.
    if len(cmd) == 1 and (" " in cmd[0].strip()):
        s = cmd[0].strip()
        if os.name == "nt":
            bash = _windows_bash_executable()
            if bash and _single_string_looks_posix_shell(s):
                return [bash, "-lc", s]
            return ["cmd", "/c", s]
        return ["bash", "-lc", s]
    return cmd


def _to_shell(cmd: list[str]) -> str:
    if not cmd:
        return ""
    if len(cmd) == 1:
        return cmd[0].strip()
    exe = Path(cmd[0]).name.lower()
    if exe in {"sh", "bash"} and len(cmd) >= 3 and cmd[1] in {"-c", "-lc"}:
        shell = str(cmd[2] or "").strip()
        if len(cmd) > 3:
            shell = " ".join([shell, *[shlex.quote(str(x)) for x in cmd[3:]]]).strip()
        return shell
    if exe in {"cmd", "cmd.exe"} and len(cmd) >= 3 and cmd[1].lower() in {"/c", "/s", "/k"}:
        shell = str(cmd[2] or "").strip()
        if len(cmd) > 3:
            shell = " ".join([shell, *[shlex.quote(str(x)) for x in cmd[3:]]]).strip()
        return shell
    return shlex.join([str(x) for x in cmd if str(x).strip()])


def _normalized_shell_for_compare(cmd: list[str]) -> str:
    shell = _to_shell(cmd)
    shell = re.sub(r"\s+", " ", shell).strip()
    return shell


def _is_rerun_failed_task_command(cmd: list[str], failed_cmd: Any) -> bool:
    if not isinstance(failed_cmd, list) or not all(isinstance(x, str) for x in failed_cmd):
        return False
    current = _normalized_shell_for_compare(cmd)
    failed = _normalized_shell_for_compare(failed_cmd)
    if not current or not failed:
        return False
    return current == failed or current in {f"bash -lc {shlex.quote(failed)}", f"sh -lc {shlex.quote(failed)}"}


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _host_dependency_installs_allowed(cfg: dict[str, Any]) -> bool:
    return _truthy(
        cfg.get("allow_host_dependency_installs")
        or os.environ.get("EXECUTION_ALLOW_HOST_DEP_INSTALLS")
        or os.environ.get("FACTREVIEW_ALLOW_HOST_DEP_INSTALLS")
        or ""
    )


def _llm_source_edits_allowed(cfg: dict[str, Any]) -> bool:
    return _truthy(
        cfg.get("allow_llm_source_edits")
        or os.environ.get("EXECUTION_ALLOW_LLM_SOURCE_EDITS")
        or os.environ.get("FACTREVIEW_ALLOW_LLM_SOURCE_EDITS")
        or ""
    )


def _workspace_source_edits_allowed(cfg: dict[str, Any], paper_root: str, run_dir: Path) -> bool:
    raw = (
        cfg.get("allow_workspace_source_edits")
        or os.environ.get("EXECUTION_ALLOW_WORKSPACE_SOURCE_EDITS")
        or os.environ.get("FACTREVIEW_ALLOW_WORKSPACE_SOURCE_EDITS")
        or ""
    )
    if str(raw or "").strip():
        return _truthy(raw)
    if _truthy(
        cfg.get("disable_workspace_source_edits")
        or os.environ.get("EXECUTION_DISABLE_WORKSPACE_SOURCE_EDITS")
        or os.environ.get("FACTREVIEW_DISABLE_WORKSPACE_SOURCE_EDITS")
        or ""
    ):
        return False
    try:
        return _path_inside(Path(paper_root or ".").resolve(strict=False), Path(run_dir).resolve(strict=False))
    except Exception:
        return False


def _semantic_stubs_allowed(cfg: dict[str, Any]) -> bool:
    return _truthy(
        cfg.get("allow_semantic_stubs")
        or os.environ.get("EXECUTION_ALLOW_SEMANTIC_STUBS")
        or os.environ.get("FACTREVIEW_ALLOW_SEMANTIC_STUBS")
        or ""
    )


def _high_risk_fixes_allowed(cfg: dict[str, Any]) -> bool:
    return _truthy(
        cfg.get("allow_high_risk_fixes")
        or os.environ.get("EXECUTION_ALLOW_HIGH_RISK_FIXES")
        or os.environ.get("FACTREVIEW_ALLOW_HIGH_RISK_FIXES")
        or ""
    )


def _fix_system_prompt() -> str:
    return (
        "You are a senior engineer repairing research-code execution for paper review.\n"
        "Your priority order is: (1) preserve experiment semantics and paper-claim validity, "
        "(2) make the run reproducible and auditable, (3) minimize the fix.\n"
        "Produce a fix plan ONLY in JSON. Do not include prose outside JSON.\n"
        "Never fake scientific results, create mock metric values, replace model logic with stubs, "
        "skip evaluation silently, or edit paper source code to make a failure disappear.\n"
        "Prefer deterministic environment/task-wrapper fixes: add missing Docker image dependencies, "
        "normalize paths, repair tasks.yaml wrapper commands, create metric-export artifacts from real logs, "
        "or mark the issue as data/access/metric-unavailable when no valid repair is possible.\n"
        "For missing Python packages in Docker, prefer a command equivalent to `python -m pip install <package>` "
        "so the executor can rebuild the per-paper image instead of mutating a throwaway container.\n"
        "If the task needs unavailable private data, checkpoints, API/model-server access, or a very large download, "
        "do not invent a workaround; return no command action and explain the blocker in root_cause.\n"
        "If a repair is high-risk or blocked by unavailable resources, set risk/blocked_by honestly "
        "and leave actions empty.\n"
        "Use the execution paths supplied in the prompt. When docker is disabled, do not use container "
        "paths such as /app or /workspace/run_dir.\n"
        "When docker is disabled, do not install or upgrade packages in the host Python/system environment "
        "unless the prompt explicitly says host dependency installs are allowed.\n"
        "Commands must be non-destructive: no git reset/clean/checkout, no rm -rf source trees, no curl|bash, "
        "and no writes outside the paper repo or run artifacts.\n"
    )


_SENSITIVE_VALUE_RE = re.compile(
    r"(?i)(api[_-]?key|secret|token|authorization|bearer|password)(['\"\s:=]+)([A-Za-z0-9._~+/=-]{8,})"
)


def _redact_sensitive_text(text: str) -> str:
    return _SENSITIVE_VALUE_RE.sub(r"\1\2[REDACTED]", str(text or ""))


def _compact_prompt_value(value: Any, *, max_chars: int = 1200) -> Any:
    if isinstance(value, dict):
        return {str(k): _compact_prompt_value(v, max_chars=max_chars) for k, v in value.items()}
    if isinstance(value, list):
        return [_compact_prompt_value(v, max_chars=max_chars) for v in value[:12]]
    text = _redact_sensitive_text(str(value))
    if len(text) > max_chars:
        return text[:max_chars] + "...[truncated]"
    return value if isinstance(value, (int, float, bool)) or value is None else text


def _recent_failure_context(state: dict[str, Any], run_dir: Path, *, max_events: int = 10) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in state.get("history") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        if not kind:
            continue
        if any(token in kind for token in ("error", "failed", "failure", "skipped", "stop")):
            events.append({"kind": kind, "data": _compact_prompt_value(item.get("data") or {})})
    issues_path = run_dir / "issues.jsonl"
    if issues_path.exists():
        try:
            lines = issues_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-50:]
        except Exception:
            lines = []
        for raw in lines:
            try:
                item = json.loads(raw)
            except Exception:
                continue
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "").strip()
            if not kind or not any(token in kind for token in ("error", "failed", "failure", "skipped", "stop")):
                continue
            events.append({"kind": kind, "data": _compact_prompt_value(item.get("data") or {})})
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        key = json.dumps(event, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        deduped.append(event)
        seen.add(key)
    return deduped[-max_events:]


def _failed_task_artifact_context(run_result: dict[str, Any], run_dir: Path, *, max_files: int = 4) -> list[dict[str, Any]]:
    failed_task = str(run_result.get("failed_task") or "").strip()
    if not failed_task:
        return []
    tasks = run_result.get("tasks")
    if not isinstance(tasks, list):
        return []
    artifacts_dir = run_dir / "artifacts"
    out: list[dict[str, Any]] = []
    for task in tasks:
        if not isinstance(task, dict) or str(task.get("id") or "").strip() != failed_task:
            continue
        candidates = []
        for key in ("metric_artifact", "diagnostic_artifact"):
            value = str(task.get(key) or "").strip()
            if value:
                candidates.append(value)
        for value in candidates:
            if len(out) >= max_files:
                break
            path = (artifacts_dir / value).resolve(strict=False)
            try:
                path.relative_to(artifacts_dir.resolve(strict=False))
            except Exception:
                continue
            if not path.exists() or not path.is_file() or path.stat().st_size > 1_000_000:
                continue
            text = _redact_sensitive_text(path.read_text(encoding="utf-8", errors="ignore"))
            item: dict[str, Any] = {"path": value}
            try:
                item["json"] = _compact_prompt_value(json.loads(text), max_chars=1200)
            except Exception:
                item["text"] = _compact_prompt_value(text, max_chars=2400)
            out.append(item)
    return out


_NO_PLAN_BLOCKER_VALUES = {
    "",
    "false",
    "n/a",
    "na",
    "no",
    "no blocker",
    "none",
    "not applicable",
    "not_applicable",
    "null",
}


def _plan_blocked_by(plan: dict[str, Any]) -> str:
    raw = plan.get("blocked_by")
    if raw is None:
        return ""
    if isinstance(raw, (list, tuple, set)):
        text = ", ".join(str(x).strip() for x in raw if str(x).strip())
    elif isinstance(raw, dict):
        text = json.dumps(raw, ensure_ascii=False, sort_keys=True) if raw else ""
    else:
        text = str(raw).strip()
    if text.strip().lower() in _NO_PLAN_BLOCKER_VALUES:
        return ""
    return text.strip()


def _plan_risk(plan: dict[str, Any]) -> str:
    risk = str(plan.get("risk") or "").strip().lower()
    if risk.startswith("high"):
        return "high"
    if risk.startswith("medium"):
        return "medium"
    if risk.startswith("low"):
        return "low"
    return ""


def _plan_has_action_items(plan: dict[str, Any]) -> bool:
    actions = plan.get("actions")
    return isinstance(actions, list) and any(isinstance(item, dict) for item in actions)


def _plan_evidence_items(plan: dict[str, Any]) -> list[str]:
    raw = plan.get("evidence")
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


def _fix_plan_auto_apply_block_reason(plan: dict[str, Any], cfg: dict[str, Any]) -> tuple[str, str]:
    blocker = _plan_blocked_by(plan)
    if blocker:
        return "blocked_by", blocker
    risk = _plan_risk(plan)
    if _plan_has_action_items(plan) and not risk:
        return "missing_risk", "LLM fix plans with actions must explicitly set risk=low|medium|high"
    if risk == "high" and not _high_risk_fixes_allowed(cfg):
        return "high_risk", "high-risk fix plan requires EXECUTION_ALLOW_HIGH_RISK_FIXES=1"
    if _plan_has_action_items(plan) and not _plan_evidence_items(plan):
        return "missing_evidence", "LLM fix plans with actions must cite stdout/stderr/task evidence"
    return "", ""


_HOST_DEP_INSTALL_RE = re.compile(
    r"("
    r"\b(?:python(?:\.exe)?|python3|py)\s+(?:-\d+(?:\.\d+)?\s+)?-m\s+pip\s+"
    r"(?:install|uninstall|download|wheel)\b"
    r"|\bpip(?:3|\.exe)?\s+(?:install|uninstall|download|wheel)\b"
    r"|\b(?:conda|mamba|micromamba)\s+(?:install|update|remove|create|env\s+create)\b"
    r"|\buv\s+pip\s+(?:install|uninstall|sync)\b"
    r"|\bpoetry\s+(?:install|add|update)\b"
    r"|\bpipenv\s+(?:install|sync|update)\b"
    r"|\b(?:apt-get|apt|yum|dnf|apk|brew)\s+(?:install|add|update|upgrade)\b"
    r"|\bnpm\s+(?:install|i|ci)\b"
    r"|\byarn\s+(?:install|add)\b"
    r")",
    flags=re.IGNORECASE,
)


def _is_host_dependency_install_command(cmd: list[str]) -> bool:
    return bool(_HOST_DEP_INSTALL_RE.search(_normalized_shell_for_compare(cmd)))


_SOURCE_EDIT_COMMAND_RE = re.compile(
    r"("
    r"\.write_text\s*\("
    r"|\.write_bytes\s*\("
    r"|\bopen\s*\([^)]*,\s*['\"][^'\"]*[wax+]"
    r"|\bsed\s+-i\b"
    r"|\bperl\s+-pi\b"
    r"|\btee\s+"
    r"|(?<!\d)>{1,2}\s*(?!&)\S+"
    r")",
    flags=re.IGNORECASE,
)


_HARD_SOURCE_EDIT_COMMAND_RE = re.compile(r"\b(?:sed\s+-i|perl\s+-pi)\b", flags=re.IGNORECASE)

_PATH_WRITE_TARGET_RE = re.compile(
    r"(?:pathlib\.)?Path\s*\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\.\s*write_(?:text|bytes)\s*\(",
    flags=re.IGNORECASE,
)

_OPEN_WRITE_TARGET_RE = re.compile(
    r"\bopen\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"][^'\"]*[wax+]",
    flags=re.IGNORECASE,
)

_REDIRECT_TARGET_RE = re.compile(r"(?<![<\d])>{1,2}\s*(?!&)(['\"][^'\"]+['\"]|[^\s;&|]+)")

_GENERATED_WRITE_ROOTS = {"artifacts", "metrics", "outputs", "results", "logs", "figs", "figures"}

_GENERATED_WRITE_ENV_PREFIXES = {
    "$EXECUTION_ARTIFACT_DIR",
    "${EXECUTION_ARTIFACT_DIR}",
    "$EXECUTION_OUTPUT_DIR",
    "${EXECUTION_OUTPUT_DIR}",
    "$EXECUTION_TASK_OUTPUT_DIR",
    "${EXECUTION_TASK_OUTPUT_DIR}",
}


def _clean_write_target(raw: str) -> str:
    s = str(raw or "").strip()
    while s and s[0] in {"'", '"'} and s[-1:] == s[0]:
        s = s[1:-1].strip()
    return s.rstrip("),;")


def _is_generated_write_target(raw: str) -> bool:
    target = _clean_write_target(raw)
    if not target:
        return False
    if target.replace("\\", "/") in {"/dev/null", "NUL", "nul"}:
        return True
    normalized = target.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized.split("/", 1)[0] in _GENERATED_WRITE_ROOTS:
        return True
    if any(normalized == prefix or normalized.startswith(prefix + "/") for prefix in _GENERATED_WRITE_ENV_PREFIXES):
        return True
    for root in _GENERATED_WRITE_ROOTS:
        if normalized == f"/workspace/run_dir/{root}" or normalized.startswith(f"/workspace/run_dir/{root}/"):
            return True
        if normalized == f"/app/{root}" or normalized.startswith(f"/app/{root}/"):
            return True
    return bool(
        re.search(
            r"(?i)(?:^|/)runs/[^/]+/(?:artifacts|metrics|outputs|results|logs|figs|figures)(?:/|$)",
            normalized,
        )
    )


def _extract_tee_write_targets(shell: str) -> list[str]:
    targets: list[str] = []
    for match in re.finditer(r"\btee\s+([^;&|]+(?:\s+[^;&|]+)*)", shell, flags=re.IGNORECASE):
        raw_args = match.group(1).strip()
        try:
            tokens = shlex.split(raw_args)
        except ValueError:
            tokens = raw_args.split()
        for token in tokens:
            if not token or token.startswith("-") or token.startswith(">"):
                continue
            targets.append(token)
    return targets


def _extract_source_write_targets(shell: str) -> list[str]:
    targets: list[str] = []
    targets.extend(match.group(1) for match in _PATH_WRITE_TARGET_RE.finditer(shell))
    targets.extend(match.group(1) for match in _OPEN_WRITE_TARGET_RE.finditer(shell))
    targets.extend(match.group(1) for match in _REDIRECT_TARGET_RE.finditer(shell))
    targets.extend(_extract_tee_write_targets(shell))
    return targets


def _is_source_edit_command(cmd: list[str]) -> bool:
    shell = _normalized_shell_for_compare(cmd)
    if not _SOURCE_EDIT_COMMAND_RE.search(shell):
        return False
    if _HARD_SOURCE_EDIT_COMMAND_RE.search(shell):
        return True
    targets = _extract_source_write_targets(shell)
    if not targets:
        return True
    return not all(_is_generated_write_target(target) for target in targets)



def _resolve_max_attempts(state: dict[str, Any], cfg: dict[str, Any]) -> int:
    raw = state.get("max_attempts")
    if raw in (None, ""):
        raw = cfg.get("max_attempts")
    if raw in (None, ""):
        raw = 5
    try:
        return max(0, int(raw))
    except Exception:
        return 5


def _docker_runtime_kwargs(cfg: dict[str, Any]) -> dict[str, str | None]:
    return {
        "gpus": str(cfg.get("docker_gpus") or os.environ.get("EXECUTION_DOCKER_GPUS") or "").strip()
        or None,
        "shm_size": str(
            cfg.get("docker_shm_size") or os.environ.get("EXECUTION_DOCKER_SHM_SIZE") or ""
        ).strip()
        or None,
        "ipc": str(cfg.get("docker_ipc") or os.environ.get("EXECUTION_DOCKER_IPC") or "").strip()
        or None,
    }


def _normalize_shell_for_conda_env(shell: str) -> str:
    """
    In docker mode, commands are executed under `micromamba run ...`.
    Prefer `python -m pip` to avoid using the base image pip/python.
    """
    s = (shell or "").strip()
    if not s:
        return s
    s = re.sub(r"(^|&&\s*)pip3?\s+", r"\1python -m pip ", s)
    return s


def _container_path_pairs(paper_root: str, run_dir: Path) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []

    def add(host_path: str | Path, container_path: str) -> None:
        raw = str(host_path or "").strip()
        if not raw:
            return
        try:
            resolved = str(Path(raw).resolve())
        except Exception:
            resolved = raw
        variants = {raw, resolved, raw.replace("\\", "/"), resolved.replace("\\", "/")}
        variants.update({v.replace("\\", "\\\\") for v in list(variants) if "\\" in v})
        for variant in sorted(variants, key=len, reverse=True):
            if variant:
                pairs.append((variant, container_path))

    run_dir = Path(run_dir)
    add(Path(run_dir) / "artifacts", "/workspace/run_dir/artifacts")
    add(paper_root, "/app")
    add(run_dir, "/workspace/run_dir")

    # Replace deeper paths first so /workspace/source maps to /app before the
    # parent run directory maps to /workspace/run_dir.
    dedup: dict[str, str] = {}
    for host_path, container_path in sorted(pairs, key=lambda item: len(item[0]), reverse=True):
        dedup.setdefault(host_path, container_path)
    return list(dedup.items())


def _normalize_container_path_text(text: str, paper_root: str, run_dir: Path) -> str:
    patched = str(text or "")
    for host_path, container_path in _container_path_pairs(paper_root, run_dir):
        patched = patched.replace(host_path, container_path)
    return patched


def _container_cwd_to_host(cwd: str, paper_root: str, run_dir: Path) -> str:
    raw = str(cwd or "").strip()
    if not raw or raw in {".", "./"}:
        return paper_root or "."
    normalized = raw.replace("\\", "/")
    mappings = [
        ("/workspace/run_dir/artifacts", Path(run_dir) / "artifacts"),
        ("/workspace/run_dir", Path(run_dir)),
        ("/app", Path(paper_root or ".")),
    ]
    for container_prefix, host_prefix in mappings:
        if normalized == container_prefix or normalized.startswith(container_prefix + "/"):
            suffix = normalized[len(container_prefix) :].strip("/")
            return str(host_prefix / Path(*suffix.split("/"))) if suffix else str(host_prefix)
    return raw


def _path_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except Exception:
        return False


def _fix_command_cwd_allowed(cwd: str, paper_root: str, run_dir: Path) -> bool:
    raw = str(cwd or "").strip()
    if not raw:
        return False
    try:
        target = Path(raw).expanduser()
        if not target.is_absolute():
            target = Path(paper_root or ".") / target
        target = target.resolve(strict=False)
    except Exception:
        return False
    allowed_roots = [Path(paper_root or ".").resolve(strict=False), Path(run_dir).resolve(strict=False)]
    return any(_path_inside(target, root) for root in allowed_roots)


def _source_write_targets_inside_workspace(cmd: list[str], cwd: str, paper_root: str, run_dir: Path) -> bool:
    shell = _normalized_shell_for_compare(cmd)
    targets = _extract_source_write_targets(shell)
    if not targets:
        return True
    cwd_path = Path(_container_cwd_to_host(cwd or paper_root, paper_root, run_dir)).resolve(strict=False)
    roots = [Path(paper_root or ".").resolve(strict=False), Path(run_dir).resolve(strict=False)]
    for raw in targets:
        if _is_generated_write_target(raw):
            continue
        target = _clean_write_target(raw)
        normalized = target.replace("\\", "/")
        if normalized.startswith("/app/") or normalized == "/app":
            path = Path(paper_root or ".") / Path(*normalized.removeprefix("/app").strip("/").split("/"))
        elif normalized.startswith("/workspace/run_dir/") or normalized == "/workspace/run_dir":
            path = Path(run_dir) / Path(*normalized.removeprefix("/workspace/run_dir").strip("/").split("/"))
        else:
            path = Path(target)
            if not path.is_absolute():
                path = cwd_path / path
        try:
            resolved = path.resolve(strict=False)
        except Exception:
            return False
        if not any(_path_inside(resolved, root) for root in roots):
            return False
    return True


def _source_edit_command_auto_apply_allowed(
    *,
    cfg: dict[str, Any],
    cmd: list[str],
    cwd: str,
    paper_root: str,
    run_dir: Path,
    docker_enabled: bool,
    plan: dict[str, Any],
) -> bool:
    if _llm_source_edits_allowed(cfg):
        return True
    if docker_enabled:
        return False
    if _plan_risk(plan) not in {"low", "medium"}:
        return False
    if not _workspace_source_edits_allowed(cfg, paper_root, run_dir):
        return False
    mapped_cwd = _container_cwd_to_host(cwd or paper_root, paper_root, run_dir)
    if not _fix_command_cwd_allowed(mapped_cwd, paper_root, run_dir):
        return False
    return _source_write_targets_inside_workspace(cmd, mapped_cwd, paper_root, run_dir)


_SOURCE_EDIT_AUDIT_SKIP_PARTS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "__pycache__",
    ".ipynb_checkpoints",
    "artifacts",
    "deployment",
    "logs",
    "metrics",
    "outputs",
    "results",
}


def _snapshot_source_tree(root: str | Path, *, max_files: int = 4000, max_bytes: int = 5_000_000) -> dict[str, dict[str, Any]]:
    base = Path(root or ".")
    out: dict[str, dict[str, Any]] = {}
    if not base.exists():
        return out
    count = 0
    for path in base.rglob("*"):
        if count >= max_files:
            break
        try:
            rel = path.relative_to(base)
            if any(part in _SOURCE_EDIT_AUDIT_SKIP_PARTS for part in rel.parts):
                continue
            if not path.is_file():
                continue
            size = path.stat().st_size
            if size > max_bytes:
                continue
            data = path.read_bytes()
            out[rel.as_posix()] = {"sha256": hashlib.sha256(data).hexdigest(), "size": size}
            count += 1
        except Exception:
            continue
    return out


def _source_tree_changes(
    before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]
) -> dict[str, list[str]]:
    before_keys = set(before)
    after_keys = set(after)
    modified = sorted(
        key
        for key in before_keys & after_keys
        if before[key].get("sha256") != after[key].get("sha256") or before[key].get("size") != after[key].get("size")
    )
    return {
        "created": sorted(after_keys - before_keys),
        "deleted": sorted(before_keys - after_keys),
        "modified": modified,
    }


_PATH_REWRITE_TEXT_SUFFIXES = {
    "",
    ".bash",
    ".cfg",
    ".conf",
    ".env",
    ".ini",
    ".ipynb",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}

_PIP_INSTALL_OPTION_TAKES_VALUE = {
    "-c",
    "--constraint",
    "-f",
    "--find-links",
    "-i",
    "--index-url",
    "--extra-index-url",
    "--trusted-host",
    "--index",
    "-r",
    "--requirement",
}

_PIP_BOOTSTRAP_PACKAGES = {"pip", "setuptools", "wheel"}


def _rewrite_container_path_leaks(paper_root: str, run_dir: Path) -> list[str]:
    root = Path(paper_root or ".")
    if not root.exists():
        return []
    changed: list[str] = []
    skipped_parts = {".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache"}
    for path in root.rglob("*"):
        try:
            if not path.is_file() or skipped_parts.intersection(path.parts):
                continue
            if path.stat().st_size > 2_000_000:
                continue
            if path.suffix.lower() not in _PATH_REWRITE_TEXT_SUFFIXES and path.name not in {
                "Dockerfile",
                "Makefile",
                "README",
            }:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            patched = _normalize_container_path_text(text, paper_root, run_dir)
            if patched != text:
                path.write_text(patched, encoding="utf-8", errors="ignore")
                try:
                    changed.append(str(path.relative_to(root)))
                except Exception:
                    changed.append(str(path))
        except Exception:
            continue
    return changed


def _pip_package_for_module(module: str) -> str:
    module = str(module or "").strip()
    if not module:
        return ""
    root = module.split(".", 1)[0].strip()
    key = root or module
    return _MODULE_TO_PIP.get(module) or _MODULE_TO_PIP.get(key) or key.replace("_", "-")


def _module_root(module: str) -> str:
    root = str(module or "").strip().split(".", 1)[0]
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", root):
        return ""
    return root


def _is_external_import_root(module: str) -> bool:
    root = _module_root(module)
    if not root or root == "__future__":
        return False
    if root in sys.builtin_module_names:
        return False
    stdlib_names = getattr(sys, "stdlib_module_names", set())
    return root not in stdlib_names


def _dedupe_modules(modules: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for module in modules:
        root = _module_root(module)
        if not root or root.lower() in seen:
            continue
        out.append(root)
        seen.add(root.lower())
    return out


def _parse_import_list(raw: str) -> list[str]:
    modules: list[str] = []
    for item in str(raw or "").split(","):
        token = item.strip()
        if not token:
            continue
        token = token.split("#", 1)[0].strip()
        token = re.split(r"\s+as\s+|\s+", token, maxsplit=1)[0].strip()
        root = _module_root(token)
        if root and _is_external_import_root(root):
            modules.append(root)
    return _dedupe_modules(modules)


def _python_import_groups_from_text(text: str) -> list[list[str]]:
    groups: list[list[str]] = []
    source = str(text or "")
    for match in re.finditer(
        r"\b(?:mods|modules|targets)\s*=\s*\[(?P<items>[^\]]{1,2000})\]",
        source,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        group = _parse_import_list(",".join(re.findall(r"['\"]([A-Za-z_][A-Za-z0-9_.]*)['\"]", match.group("items"))))
        if group:
            groups.append(group)
    for match in re.finditer(
        r"(?m)(?:^|[;\n'\"])\s*import\s+"
        r"(?P<modules>[A-Za-z_][A-Za-z0-9_.]*(?:\s+as\s+[A-Za-z_][A-Za-z0-9_]*)?"
        r"(?:\s*,\s*[A-Za-z_][A-Za-z0-9_.]*(?:\s+as\s+[A-Za-z_][A-Za-z0-9_]*)?)*)",
        source,
    ):
        group = _parse_import_list(match.group("modules"))
        if group:
            groups.append(group)
    for match in re.finditer(r"(?m)(?:^|[;\n'\"])\s*from\s+(?P<module>[A-Za-z_][A-Za-z0-9_.]*)\s+import\b", source):
        root = _module_root(match.group("module"))
        if root and _is_external_import_root(root):
            groups.append([root])
    for match in re.finditer(r"\bimport_module\s*\(\s*['\"](?P<module>[A-Za-z_][A-Za-z0-9_.]*)['\"]", source):
        root = _module_root(match.group("module"))
        if root and _is_external_import_root(root):
            groups.append([root])
    return [_dedupe_modules(group) for group in groups if group]


def _repo_local_module_roots(paper_root: str, *, max_files: int = 3000) -> set[str]:
    root = Path(paper_root or ".")
    local: set[str] = set()
    if not root.exists():
        return local
    skipped = {".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "deployment", "outputs", "logs", "results"}
    count = 0
    for path in root.rglob("*.py"):
        if count >= max_files:
            break
        try:
            rel = path.relative_to(root)
        except Exception:
            continue
        if any(part in skipped for part in rel.parts):
            continue
        count += 1
        if path.name == "__init__.py":
            local.add(path.parent.name)
        else:
            local.add(path.stem)
    return {name for name in local if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name)}


def _notebook_import_modules_from_failed_cmd(failed_task_cmd: Any, paper_root: str) -> list[str]:
    if isinstance(failed_task_cmd, list):
        tokens = [str(x) for x in failed_task_cmd]
        shell = _to_shell(tokens)
    else:
        shell = str(failed_task_cmd or "")
        try:
            tokens = shlex.split(shell)
        except ValueError:
            tokens = shell.split()
    candidates: list[str] = []
    for token in tokens:
        cleaned = token.strip().strip("'\"")
        if cleaned.lower().endswith(".ipynb"):
            candidates.append(cleaned)
    candidates.extend(re.findall(r"(?<![\w.-])([A-Za-z0-9_./\\ -]+?\.ipynb)(?![\w.-])", shell))

    root = Path(paper_root or ".").resolve(strict=False)
    local_roots = _repo_local_module_roots(paper_root)
    modules: list[str] = []
    seen_paths: set[str] = set()
    for raw in candidates:
        try:
            path = Path(raw)
            if not path.is_absolute():
                path = root / path
            path = path.resolve(strict=False)
            if str(path).lower() in seen_paths or not _path_inside(path, root) or not path.exists():
                continue
            seen_paths.add(str(path).lower())
            data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        cells = data.get("cells") if isinstance(data, dict) else None
        if not isinstance(cells, list):
            continue
        for cell in cells:
            if not isinstance(cell, dict) or cell.get("cell_type") != "code":
                continue
            source = cell.get("source")
            text = "".join(str(x) for x in source) if isinstance(source, list) else str(source or "")
            for group in _python_import_groups_from_text(text):
                modules.extend(module for module in group if module not in local_roots)
    return _dedupe_modules(modules)


def _related_import_modules_for_missing(missing: str, context_text: str) -> list[str]:
    missing_root = _module_root(missing)
    if not missing_root or not _is_external_import_root(missing_root):
        return [missing_root] if missing_root else []
    related: list[str] = [missing_root]
    for group in _python_import_groups_from_text(context_text):
        if missing_root in group:
            related.extend(group)
    return _dedupe_modules(related)


def _package_key(package: str) -> str:
    token = str(package or "").strip()
    if not token or token.startswith(("-", ".")):
        return ""
    token = token.split(";", 1)[0].strip()
    if token.startswith(("git+", "http://", "https://")):
        return token.lower()
    name = re.split(r"\s*(?:==|>=|<=|~=|!=|>|<|\[)", token, maxsplit=1)[0].strip()
    return name.lower().replace("_", "-")


def _package_is_specific(package: str) -> bool:
    return bool(re.search(r"(?:==|>=|<=|~=|!=|>|<|\[)", str(package or "")))


def _version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for item in re.findall(r"\d+", str(version or "")):
        try:
            parts.append(int(item))
        except Exception:
            break
    return tuple(parts)


def _dgl_graphbolt_torch_pin_from_verify_failure(text: str) -> str:
    m = re.search(
        r"Cannot find DGL C\+\+ graphbolt library at\s+(?P<path>[^\r\n]*?graphbolt_pytorch_(?P<version>\d+(?:\.\d+){1,3})\.(?:dll|so|dylib))",
        text or "",
        flags=re.IGNORECASE,
    )
    if not m:
        return ""
    path_text = m.group("path").strip().strip("'\"")
    graphbolt_dir = Path(path_text).parent
    if not graphbolt_dir.exists():
        return ""
    versions: list[str] = []
    try:
        for item in graphbolt_dir.glob("graphbolt_pytorch_*.*"):
            vm = re.match(r"graphbolt_pytorch_(\d+(?:\.\d+){1,3})\.(?:dll|so|dylib)$", item.name)
            if vm:
                versions.append(vm.group(1))
    except Exception:
        return ""
    if not versions:
        return ""
    requested = _version_tuple(m.group("version"))
    eligible = [v for v in versions if not requested or _version_tuple(v) <= requested]
    chosen = max(eligible or versions, key=_version_tuple)
    return f"torch=={chosen}"


def _compatibility_pip_specs_from_verify_failure(text: str) -> list[str]:
    lower = (text or "").lower()
    specs: list[str] = []
    torch_pin = _dgl_graphbolt_torch_pin_from_verify_failure(text)
    if torch_pin:
        specs.append(torch_pin)
    if "compiled using numpy 1.x cannot be run in" in lower or "_array_api not found" in lower:
        specs.append("numpy<2")
    return specs


_PYG_NATIVE_PACKAGE_KEYS = {"torch-scatter", "torch-sparse", "torch-cluster", "torch-spline-conv"}


def _host_torch_version_info(venv_python: Path, *, cwd: str, timeout_sec: int) -> tuple[str, str]:
    res = run_command(
        [
            str(venv_python),
            "-c",
            (
                "import torch\n"
                "print((torch.__version__ or '').split('+', 1)[0])\n"
                "print(str(getattr(torch.version, 'cuda', '') or ''))\n"
            ),
        ],
        cwd=cwd,
        timeout_sec=timeout_sec,
    )
    if res.returncode != 0:
        return "", ""
    lines = [ln.strip() for ln in (res.stdout or "").splitlines()]
    version = lines[0] if lines else ""
    cuda = lines[1] if len(lines) > 1 else ""
    if cuda.lower() in {"", "none"}:
        cuda = ""
    return version, cuda


def _pyg_native_install_cmds(*, venv_python: Path, cwd: str, timeout_sec: int, package: str) -> list[tuple[str, list[str]]]:
    if _package_key(package) not in _PYG_NATIVE_PACKAGE_KEYS:
        return []
    torch_version, cuda = _host_torch_version_info(venv_python, cwd=cwd, timeout_sec=timeout_sec)
    if not torch_version:
        return []
    urls: list[str] = []
    if cuda:
        urls.append(f"https://data.pyg.org/whl/torch-{torch_version}+cu{cuda.replace('.', '')}.html")
    urls.append(f"https://data.pyg.org/whl/torch-{torch_version}+cpu.html")
    urls.append(f"https://data.pyg.org/whl/torch-{torch_version}.html")
    commands: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for idx, url in enumerate(urls, 1):
        if url in seen:
            continue
        seen.add(url)
        commands.append(
            (
                f"pyg_wheel_{idx}",
                [str(venv_python), "-m", "pip", "install", "--no-build-isolation", package, "-f", url],
            )
        )
    return commands


def _split_shell_commands(shell: str) -> list[str]:
    pieces = re.split(r"\s*(?:&&|\|\||;|\n)\s*", str(shell or ""))
    return [p.strip() for p in pieces if p.strip()]


def _is_dangerous_fix_command(cmd: list[str]) -> bool:
    shell = _normalized_shell_for_compare(cmd)
    lower = shell.lower()
    if re.search(r"\b(?:curl|wget)\b[^|;&]*(?:\||>)\s*(?:bash|sh|python)\b", lower):
        return True
    if re.search(r"\bgit\s+(?:reset\s+--hard|clean\b|checkout\b|restore\b|pull\b|merge\b|rebase\b)", lower):
        return True
    for segment in _split_shell_commands(shell):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            tokens = segment.split()
        if not tokens:
            continue
        exe = Path(tokens[0]).name.lower()
        if exe in {"rm", "rmdir", "del", "erase"} or tokens[0].lower() == "remove-item":
            options = [t.lower() for t in tokens[1:] if t.startswith(("-", "/"))]
            targets = [t for t in tokens[1:] if not t.startswith(("-", "/"))]
            recursive_or_force = exe in {"rmdir", "del", "erase"} or any(
                opt in {"/s", "/q", "-recurse", "-recursive", "-force"}
                or ("r" in opt.lstrip("-") and opt.startswith("-"))
                or ("f" in opt.lstrip("-") and opt.startswith("-"))
                for opt in options
            )
            if recursive_or_force and (not targets or not all(_is_generated_write_target(t) for t in targets)):
                return True
    return False


def _extract_pip_install_requests(cmd: list[str]) -> tuple[list[str], list[str]]:
    """
    Extract installable package specs from simple pip-install commands.

    In Docker mode these commands would otherwise mutate only a throwaway
    container. We convert them into per-paper image build inputs instead.
    """

    packages: list[str] = []
    extra_indexes: list[str] = []
    seen_packages: set[str] = set()
    seen_indexes: set[str] = set()
    shell = _to_shell(cmd)
    for segment in _split_shell_commands(shell):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            continue
        if not tokens:
            continue
        pip_index = -1
        if Path(tokens[0]).name.lower() in {"pip", "pip3", "pip.exe", "pip3.exe"}:
            pip_index = 0
        elif (
            len(tokens) >= 3
            and Path(tokens[0]).name.lower() == "uv"
            and [t.lower() for t in tokens[1:3]] == ["pip", "install"]
        ):
            pip_index = 1
        else:
            for i in range(len(tokens) - 2):
                if tokens[i + 1] == "-m" and tokens[i + 2] == "pip":
                    pip_index = i + 2
                    break
            if pip_index < 0:
                for i in range(len(tokens) - 3):
                    if (
                        tokens[i + 1] == "-m"
                        and tokens[i + 2].lower() == "uv"
                        and tokens[i + 3].lower() == "pip"
                    ):
                        pip_index = i + 3
                        break
        if pip_index < 0:
            continue
        try:
            install_index = tokens.index("install", pip_index + 1)
        except ValueError:
            continue
        i = install_index + 1
        while i < len(tokens):
            token = tokens[i]
            if token in {"-i", "--index-url", "--extra-index-url"} and i + 1 < len(tokens):
                url = tokens[i + 1].strip()
                if url and url not in seen_indexes:
                    extra_indexes.append(url)
                    seen_indexes.add(url)
                i += 2
                continue
            if token.startswith("--index-url=") or token.startswith("--extra-index-url="):
                url = token.split("=", 1)[1].strip()
                if url and url not in seen_indexes:
                    extra_indexes.append(url)
                    seen_indexes.add(url)
                i += 1
                continue
            if token in _PIP_INSTALL_OPTION_TAKES_VALUE:
                i += 2
                continue
            if any(token.startswith(prefix + "=") for prefix in _PIP_INSTALL_OPTION_TAKES_VALUE if prefix.startswith("--")):
                i += 1
                continue
            if token.startswith("-"):
                i += 1
                continue
            key = _package_key(token)
            if not key or key in _PIP_BOOTSTRAP_PACKAGES or key in seen_packages:
                i += 1
                continue
            packages.append(token)
            seen_packages.add(key)
            i += 1
    return packages, extra_indexes


def _missing_module_looks_local(paper_root: str, module: str) -> bool:
    return bool(_find_local_module_pythonpath_dirs(paper_root, module, max_results=1))


_LOCAL_MODULE_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    "artifacts",
    "checkpoints",
    "logs",
    "outputs",
    "results",
    "runs",
}


def _path_has_skipped_part(path: Path) -> bool:
    return any(part.lower() in _LOCAL_MODULE_SKIP_DIRS for part in path.parts)


def _path_distance(left: Path, right: Path) -> int | None:
    try:
        left_parts = [str(part).lower() if os.name == "nt" else str(part) for part in left.resolve(strict=False).parts]
        right_parts = [str(part).lower() if os.name == "nt" else str(part) for part in right.resolve(strict=False).parts]
    except Exception:
        left_parts = [str(part).lower() if os.name == "nt" else str(part) for part in left.parts]
        right_parts = [str(part).lower() if os.name == "nt" else str(part) for part in right.parts]
    if not left_parts or not right_parts or left_parts[0] != right_parts[0]:
        return None
    common = 0
    for left_part, right_part in zip(left_parts, right_parts, strict=False):
        if left_part != right_part:
            break
        common += 1
    return (len(left_parts) - common) + (len(right_parts) - common)


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            resolved = path.resolve(strict=False)
        except Exception:
            resolved = path
        key = str(resolved).lower() if os.name == "nt" else str(resolved)
        if key in seen:
            continue
        out.append(resolved)
        seen.add(key)
    return out


def _traceback_repo_paths(context_text: str, paper_root: str) -> list[Path]:
    repo = Path(paper_root or ".")
    if not context_text or not repo.exists():
        return []
    repo_resolved = repo.resolve(strict=False)
    paths: list[Path] = []
    for match in re.finditer(r"File\s+['\"](?P<path>[^'\"]+?\.py)['\"]", context_text):
        raw = match.group("path").replace("\\\\", "\\").strip()
        if not raw or raw.startswith("<"):
            continue
        path = Path(raw)
        if not path.is_absolute():
            path = repo_resolved / path
        try:
            resolved = path.resolve(strict=False)
            if _path_inside(resolved, repo_resolved):
                paths.append(resolved)
        except Exception:
            continue
    return _dedupe_paths(paths)


def _import_target_repo_paths(context_text: str, paper_root: str) -> list[Path]:
    repo = Path(paper_root or ".")
    if not context_text or not repo.exists():
        return []
    repo_resolved = repo.resolve(strict=False)
    paths: list[Path] = []
    for match in re.finditer(r"['\"](?P<module>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)['\"]", context_text):
        module = match.group("module")
        parts = module.split(".")
        file_path = repo_resolved.joinpath(*parts).with_suffix(".py")
        pkg_path = repo_resolved.joinpath(*parts, "__init__.py")
        if file_path.is_file():
            paths.append(file_path)
        elif pkg_path.is_file():
            paths.append(pkg_path)
    return _dedupe_paths(paths)


def _rank_local_module_pythonpath_dirs(
    candidates: list[Path],
    paper_root: str,
    *,
    context_text: str = "",
) -> list[Path]:
    candidates = _dedupe_paths(candidates)
    anchors = _dedupe_paths(
        _traceback_repo_paths(context_text, paper_root) + _import_target_repo_paths(context_text, paper_root)
    )
    if not candidates or not anchors:
        return candidates

    scored: list[tuple[int, int, Path]] = []
    for index, candidate in enumerate(candidates):
        distances = [_path_distance(candidate, anchor.parent) for anchor in anchors]
        valid_distances = [distance for distance in distances if distance is not None]
        score = min(valid_distances) if valid_distances else 1_000_000
        scored.append((score, index, candidate))
    scored.sort(key=lambda item: (item[0], item[1]))
    best_score = scored[0][0]
    if len(scored) > 1 and best_score < scored[1][0]:
        return [path for score, _, path in scored if score == best_score]
    return [path for _, _, path in scored]


def _find_local_module_pythonpath_dirs(
    paper_root: str,
    module: str,
    *,
    max_results: int = 8,
    context_text: str = "",
) -> list[str]:
    root_name = str(module or "").strip().split(".", 1)[0]
    if not root_name or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", root_name):
        return []
    repo = Path(paper_root or ".")
    if not repo.exists():
        return []
    candidates: list[Path] = []
    direct_pkg = repo / root_name
    direct_file = repo / f"{root_name}.py"
    if direct_pkg.is_dir() or direct_file.is_file():
        candidates.append(repo)
    patterns = [f"**/{root_name}.py", f"**/{root_name}/__init__.py"]
    for pattern in patterns:
        try:
            iterator = repo.glob(pattern)
            for path in iterator:
                if len(candidates) >= max_results:
                    break
                if _path_has_skipped_part(path.relative_to(repo)):
                    continue
                base = path.parent if path.name == f"{root_name}.py" else path.parent.parent
                if not base.exists() or not _path_inside(base, repo):
                    continue
                candidates.append(base)
        except Exception:
            continue
    out: list[str] = []
    for path in _rank_local_module_pythonpath_dirs(candidates, paper_root, context_text=context_text):
        out.append(str(path))
        if len(out) >= max_results:
            break
    return out


def _add_extra_pythonpath_dir(cfg: dict[str, Any], path: str | Path, *, paper_root: str) -> bool:
    raw_path = Path(path)
    repo = Path(paper_root or ".")
    try:
        resolved = raw_path.resolve(strict=False)
        repo_resolved = repo.resolve(strict=False)
        if not _path_inside(resolved, repo_resolved):
            return False
        stored = "." if resolved == repo_resolved else resolved.relative_to(repo_resolved).as_posix()
    except Exception:
        stored = str(path).replace("\\", "/")
    if not stored or stored == ".":
        return False
    raw_existing = cfg.get("extra_pythonpath_dirs") or []
    if isinstance(raw_existing, str):
        existing = [x.strip() for x in re.split(r"[;\n,]", raw_existing) if x.strip()]
    elif isinstance(raw_existing, (list, tuple, set)):
        existing = [str(x).strip() for x in raw_existing if str(x).strip()]
    else:
        existing = []
    normalized_existing = {x.replace("\\", "/").rstrip("/") for x in existing}
    if stored.rstrip("/") in normalized_existing:
        return False
    existing.append(stored)
    cfg["extra_pythonpath_dirs"] = existing
    return True


def _add_extra_pip_package(cfg: dict[str, Any], package: str) -> bool:
    package = str(package or "").strip()
    if not package:
        return False
    raw = str(cfg.get("docker_extra_pip_packages") or os.getenv("EXECUTION_DOCKER_EXTRA_PIP_PACKAGES") or "")
    existing = [x.strip() for x in raw.split() if x.strip()]
    key = _package_key(package)
    if not key:
        return False
    for i, item in enumerate(existing):
        if _package_key(item) != key:
            continue
        if item == package:
            return False
        if _package_is_specific(package) and not _package_is_specific(item):
            existing[i] = package
            cfg["docker_extra_pip_packages"] = " ".join(existing)
            cfg.pop("docker_paper_image", None)
            return True
        if _package_is_specific(package) and _package_is_specific(item):
            existing[i] = package
            cfg["docker_extra_pip_packages"] = " ".join(existing)
            cfg.pop("docker_paper_image", None)
            return True
        return False
    existing.append(package)
    cfg["docker_extra_pip_packages"] = " ".join(existing)
    # Force docker_ensure_paper_image to compute a fresh tag from the updated cfg.
    cfg.pop("docker_paper_image", None)
    return True


def _add_docker_extra_index_url(cfg: dict[str, Any], url: str) -> bool:
    value = str(url or "").strip()
    if not value:
        return False
    raw = str(cfg.get("docker_pip_extra_index_url") or os.getenv("EXECUTION_DOCKER_PIP_EXTRA_INDEX_URL") or "")
    existing = [x.strip() for x in raw.split() if x.strip()]
    if value in existing:
        return False
    existing.append(value)
    cfg["docker_pip_extra_index_url"] = " ".join(existing)
    cfg.pop("docker_paper_image", None)
    return True


def _candidate_pip_packages_for_module(*, paper_root: str, module: str) -> list[str]:
    package = _pip_package_for_module(module)
    specs = _requirement_specs_for_package(paper_root=paper_root, package=package)
    candidates: list[str] = []
    seen: set[str] = set()
    for item in [*specs, package]:
        value = str(item or "").strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        candidates.append(value)
        seen.add(key)
    return candidates


def _validate_module_in_docker_image(
    *,
    cfg: dict[str, Any],
    image: str,
    paper_root: str,
    run_dir: Path,
    module: str,
) -> tuple[bool, int, str]:
    name = str(module or "").strip().split(".", 1)[0]
    if not name:
        return True, 0, ""
    code = (
        "import importlib.util, sys\n"
        f"name={name!r}\n"
        "spec=importlib.util.find_spec(name)\n"
        "print('module_spec', name, bool(spec))\n"
        "raise SystemExit(0 if spec else 1)\n"
    )
    docker_cmd = docker_run_paper_image(
        image=image,
        paper_root_host=str(Path(paper_root).resolve()),
        run_dir_host=str(run_dir),
        cwd_container="/app",
        cmd=["python", "-c", code],
        env={},
        env_passthrough=_docker_env_passthrough(cfg),
        **_docker_runtime_kwargs(cfg),
    )
    res = run_command(cmd=docker_cmd, cwd=str(run_dir), timeout_sec=180)
    return res.returncode == 0, int(res.returncode), ((res.stdout or "") + (res.stderr or ""))[-800:]


def _docker_build_timeout(cfg: dict[str, Any]) -> int:
    raw = cfg.get("docker_build_timeout_sec")
    if raw in (None, ""):
        raw = os.environ.get("EXECUTION_DOCKER_BUILD_TIMEOUT_SEC", "3600")
    return int(raw)


def _host_venv_install_timeout(cfg: dict[str, Any]) -> int:
    raw = cfg.get("host_venv_install_timeout_sec")
    if raw in (None, ""):
        raw = os.environ.get("EXECUTION_HOST_VENV_INSTALL_TIMEOUT_SEC", "0")
    try:
        return max(0, int(raw))
    except Exception:
        return 0


def _run_venv_python(run_dir: Path) -> Path:
    marker = run_dir / ".host_venv_path"
    if marker.exists():
        try:
            root = Path(marker.read_text(encoding="utf-8", errors="ignore").strip())
            if root:
                if os.name == "nt":
                    return root / "Scripts" / "python.exe"
                return root / "bin" / "python"
        except Exception:
            pass
    if os.name == "nt":
        return run_dir / ".venv" / "Scripts" / "python.exe"
    return run_dir / ".venv" / "bin" / "python"


def _host_venv_dir(*, cfg: dict[str, Any], run_dir: Path) -> Path:
    raw = cfg.get("host_venv_dir") or os.environ.get("EXECUTION_HOST_VENV_DIR") or ""
    if str(raw).strip():
        root = Path(str(raw)).expanduser()
        key = hashlib.sha1(str(run_dir.resolve()).encode("utf-8", errors="ignore")).hexdigest()[:12]
        return root / key
    try:
        run_text = str(run_dir.resolve())
    except Exception:
        run_text = str(run_dir)
    if os.name == "nt" and len(run_text) > 100:
        key = hashlib.sha1(run_text.encode("utf-8", errors="ignore")).hexdigest()[:12]
        drive = Path(tempfile.gettempdir()).drive or os.environ.get("SYSTEMDRIVE") or "C:"
        return Path(f"{drive}\\frv-venvs") / key
    return run_dir / ".venv"


def _write_host_venv_marker(run_dir: Path, venv_dir: Path) -> None:
    try:
        if venv_dir.resolve() == (run_dir / ".venv").resolve():
            return
    except Exception:
        pass
    write_text(run_dir / ".host_venv_path", str(venv_dir.resolve()) + "\n")


def _case_variant_alias_target(module: str) -> tuple[str, str] | None:
    candidates = _import_alias_target_candidates(module)
    if not candidates:
        return None
    root = str(module or "").strip().split(".", 1)[0]
    return root, candidates[0]


def _import_alias_target_candidates(module: str) -> list[str]:
    root = str(module or "").strip().split(".", 1)[0]
    if not root:
        return []
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", root):
        return []
    targets = list(_MODULE_IMPORT_ALIAS_TARGETS.get(root, ()))
    lower = root.lower()
    if lower != root:
        targets.append(lower)
    deduped: list[str] = []
    seen: set[str] = set()
    for target in targets:
        if not target or target == root or target in seen:
            continue
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", target):
            continue
        deduped.append(target)
        seen.add(target)
    return deduped


def _install_case_variant_import_alias(
    *,
    venv_python: Path,
    module: str,
    cwd: str,
    timeout_sec: int,
    logs_dir: Path,
    run_dir: Path,
    log_suffix: str,
) -> bool:
    alias = _case_variant_alias_target(module)
    if alias is None:
        return False
    alias_name, target_name = alias
    target_candidates = _import_alias_target_candidates(module)
    code = (
        "import importlib.util, json, pathlib, site, sys\n"
        f"alias_name={alias_name!r}\n"
        f"target_candidates={target_candidates!r}\n"
        "if importlib.util.find_spec(alias_name):\n"
        "    raise SystemExit(0)\n"
        "target_name = next((name for name in target_candidates if importlib.util.find_spec(name)), '')\n"
        "if not target_name:\n"
        "    raise SystemExit(2)\n"
        "site_dirs = site.getsitepackages() or [site.getusersitepackages()]\n"
        "base = pathlib.Path(site_dirs[0])\n"
        "pkg = base / alias_name\n"
        "pkg.mkdir(parents=True, exist_ok=True)\n"
        "init = pkg / '__init__.py'\n"
        "init.write_text(\n"
        "    'import importlib as _importlib\\n'\n"
        "    f'_mod = _importlib.import_module({target_name!r})\\n'\n"
        "    'globals().update(_mod.__dict__)\\n'\n"
        "    'try:\\n    __path__ = _mod.__path__\\nexcept AttributeError:\\n    pass\\n',\n"
        "    encoding='utf-8',\n"
        ")\n"
        "print(json.dumps({'alias': alias_name, 'target': target_name, 'path': str(pkg)}))\n"
    )
    res = run_command([str(venv_python), "-c", code], cwd=cwd, timeout_sec=timeout_sec)
    persist_command_result(res, logs_dir, prefix=f"fix_host_venv_case_alias_{log_suffix}")
    ok = res.returncode == 0
    append_event(
        run_dir,
        "fix_host_venv_case_variant_alias",
        {
            "ok": ok,
            "module": alias_name,
            "target_module": target_name,
            "target_candidates": target_candidates,
            "rc": res.returncode,
        },
    )
    return ok


_STDLIB_COMPAT_SHIMS = {
    "imp": (
        "from __future__ import annotations\n"
        "import importlib\n"
        "import importlib.util\n"
        "reload = importlib.reload\n"
        "def find_module(name, path=None):\n"
        "    spec = importlib.util.find_spec(name, path)\n"
        "    if spec is None:\n"
        "        raise ImportError(name)\n"
        "    return None, spec.origin, ('', '', 0)\n"
        "def load_module(name):\n"
        "    return importlib.import_module(name)\n"
    ),
}


def _install_stdlib_compat_shim_in_venv(
    *,
    venv_python: Path,
    module: str,
    cwd: str,
    timeout_sec: int,
    logs_dir: Path,
    run_dir: Path,
    log_suffix: str,
) -> bool:
    root = _module_root(module)
    shim = _STDLIB_COMPAT_SHIMS.get(root)
    if not shim:
        return False
    code = (
        "import json, pathlib, site\n"
        f"module={root!r}\n"
        f"shim={shim!r}\n"
        "site_dirs = site.getsitepackages() or [site.getusersitepackages()]\n"
        "target = pathlib.Path(site_dirs[0]) / f'{module}.py'\n"
        "target.write_text(shim, encoding='utf-8')\n"
        "print(json.dumps({'module': module, 'path': str(target)}))\n"
    )
    res = run_command([str(venv_python), "-c", code], cwd=cwd, timeout_sec=timeout_sec)
    persist_command_result(res, logs_dir, prefix=f"fix_host_venv_stdlib_shim_{log_suffix}")
    ok = res.returncode == 0
    append_event(
        run_dir,
        "fix_host_venv_stdlib_compat_shim",
        {"ok": ok, "module": root, "rc": res.returncode},
    )
    return ok


def _requirement_specs_for_package(*, paper_root: str, package: str) -> list[str]:
    root = Path(paper_root or ".")
    wanted = _package_key(package)
    if not wanted:
        return []
    specs: list[str] = []
    seen: set[str] = set()
    for req_file in sorted(root.glob("requirements*.txt")):
        try:
            lines = req_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        for raw in lines:
            spec = raw.split("#", 1)[0].strip()
            if not spec or spec.startswith(("-r", "--requirement", "-c", "--constraint", "--")):
                continue
            if _package_key(spec) != wanted:
                continue
            key = spec.lower()
            if key in seen:
                continue
            specs.append(spec)
            seen.add(key)
    return specs


def _project_dependency_install_cmds(*, paper_root: str, venv_python: Path, package: str) -> list[tuple[str, list[str]]]:
    root = Path(paper_root or ".")
    commands: list[tuple[str, list[str]]] = []
    python = str(venv_python)
    package = str(package or "").strip()
    for spec in _requirement_specs_for_package(paper_root=paper_root, package=package):
        commands.append(("requirement_package", [python, "-m", "pip", "install", spec]))
    if package:
        commands.append(("package", [python, "-m", "pip", "install", package]))
    if (root / "requirements.txt").exists():
        commands.append(("requirements", [python, "-m", "pip", "install", "-r", "requirements.txt"]))
    if (root / "pyproject.toml").exists() or (root / "setup.py").exists() or (root / "setup.cfg").exists():
        commands.append(("project_editable", [python, "-m", "pip", "install", "-e", "."]))

    deduped: list[tuple[str, list[str]]] = []
    seen_cmds: set[tuple[str, ...]] = set()
    for label, cmd in commands:
        key = tuple(cmd)
        if key in seen_cmds:
            continue
        deduped.append((label, cmd))
        seen_cmds.add(key)
    return deduped


def _install_missing_module_in_run_venv(
    *,
    cfg: dict[str, Any],
    run_dir: Path,
    logs_dir: Path,
    paper_root: str,
    module: str,
    context_modules: list[str] | None = None,
    attempt: int,
) -> bool:
    target_module = str(module or "").strip()
    if not target_module:
        return False
    verify_modules = _dedupe_modules([target_module, *(context_modules or [])])
    if not verify_modules:
        verify_modules = [_module_root(target_module)]
    package = _pip_package_for_module(target_module)
    venv_dir = _host_venv_dir(cfg=cfg, run_dir=run_dir)
    _write_host_venv_marker(run_dir, venv_dir)
    venv_python = _run_venv_python(run_dir)
    timeout = _host_venv_install_timeout(cfg)
    if not venv_python.exists():
        venv_dir.parent.mkdir(parents=True, exist_ok=True)
        create = run_command([sys.executable, "-m", "venv", str(venv_dir)], cwd=str(run_dir), timeout_sec=timeout)
        persist_command_result(create, logs_dir, prefix=f"fix_host_venv_create_{attempt}")
        if create.returncode != 0:
            append_event(
                run_dir,
                "fix_host_venv_create_failed",
                {"module": target_module, "package": package, "venv_dir": str(venv_dir), "rc": create.returncode},
            )
            return False

    pending_modules = list(verify_modules)
    pending_specs: list[tuple[str, str]] = []
    seen_modules: set[str] = set()
    seen_specs: set[str] = set()
    attempted: list[dict[str, Any]] = []
    try:
        nested_limit = max(
            8,
            min(
                64,
                int(
                    cfg.get("host_venv_nested_install_limit")
                    or os.environ.get("EXECUTION_HOST_VENV_NESTED_INSTALL_LIMIT")
                    or 24
                ),
            ),
        )
    except Exception:
        nested_limit = 24
    while (pending_modules or pending_specs) and (len(seen_modules) + len(seen_specs) <= nested_limit):
        if pending_specs:
            current_module, current_package = pending_specs.pop(0)
            commands = [("compat_package", [str(venv_python), "-m", "pip", "install", current_package])]
        else:
            current_module = pending_modules.pop(0)
            if current_module in seen_modules:
                continue
            seen_modules.add(current_module)
            current_package = _pip_package_for_module(current_module)
            commands = _project_dependency_install_cmds(
                paper_root=paper_root,
                venv_python=venv_python,
                package=current_package,
            )
            commands = [
                *_pyg_native_install_cmds(
                    venv_python=venv_python,
                    cwd=paper_root or str(run_dir),
                    timeout_sec=120,
                    package=current_package,
                ),
                *commands,
            ]
        for label, install_cmd in commands:
            install = run_command(install_cmd, cwd=paper_root or str(run_dir), timeout_sec=timeout)
            log_suffix = f"{label}_{attempt}"
            if current_module != target_module:
                safe_module = re.sub(r"[^A-Za-z0-9_.-]+", "_", current_module).replace(".", "_")
                log_suffix = f"{label}_{attempt}_nested_{safe_module}"
            persist_command_result(install, logs_dir, prefix=f"fix_host_venv_install_{log_suffix}")
            record: dict[str, Any] = {
                "module": current_module,
                "package": current_package,
                "label": label,
                "cmd": install_cmd,
                "rc": install.returncode,
            }
            attempted.append(record)
            if install.returncode != 0:
                append_event(
                    run_dir,
                    "fix_host_venv_install_candidate_failed",
                    record,
                )
                continue
            if len(verify_modules) == 1:
                verify_code = f"import {verify_modules[0]}; print('module_ok')"
            else:
                verify_code = (
                    "import importlib\n"
                    f"mods={verify_modules!r}\n"
                    "for mod in mods:\n"
                    "    importlib.import_module(mod)\n"
                    "print('module_ok', ','.join(mods))\n"
                )
            verify = run_command(
                [str(venv_python), "-c", verify_code],
                cwd=paper_root or str(run_dir),
                timeout_sec=120,
            )
            persist_command_result(verify, logs_dir, prefix=f"fix_host_venv_verify_{log_suffix}")
            record["verify_rc"] = verify.returncode
            if verify.returncode == 0:
                append_event(
                    run_dir,
                    "fix_host_venv_install",
                    {
                        "ok": True,
                        "module": target_module,
                        "package": package,
                        "label": label,
                        "cmd": install_cmd,
                        "verify_rc": verify.returncode,
                        "verify_modules": verify_modules,
                        "nested_modules_installed": sorted(m for m in seen_modules if m != target_module),
                    },
                )
                return True

            verify_text = f"{verify.stdout or ''}\n{verify.stderr or ''}"
            queued_compat = False
            for compat_spec in _compatibility_pip_specs_from_verify_failure(verify_text):
                compat_key = compat_spec.lower()
                if compat_key in seen_specs:
                    continue
                seen_specs.add(compat_key)
                pending_specs.append((f"compat:{_package_key(compat_spec) or compat_spec}", compat_spec))
                queued_compat = True
                append_event(
                    run_dir,
                    "fix_host_venv_compat_package",
                    {
                        "target_module": target_module,
                        "package": compat_spec,
                        "source_module": current_module,
                    },
                )
            if queued_compat:
                break

            nested_missing = _extract_missing_module(verify_text)
            if nested_missing and _case_variant_alias_target(nested_missing):
                alias_ok = _install_case_variant_import_alias(
                    venv_python=venv_python,
                    module=nested_missing,
                    cwd=paper_root or str(run_dir),
                    timeout_sec=120,
                    logs_dir=logs_dir,
                    run_dir=run_dir,
                    log_suffix=log_suffix,
                )
                if alias_ok:
                    verify = run_command(
                        [str(venv_python), "-c", verify_code],
                        cwd=paper_root or str(run_dir),
                        timeout_sec=120,
                    )
                    persist_command_result(verify, logs_dir, prefix=f"fix_host_venv_verify_{log_suffix}_case_alias")
                    record["case_variant_alias"] = nested_missing
                    record["verify_rc_after_case_alias"] = verify.returncode
                    if verify.returncode == 0:
                        append_event(
                            run_dir,
                            "fix_host_venv_install",
                            {
                                "ok": True,
                                "module": target_module,
                                "package": package,
                                "label": label,
                                "cmd": install_cmd,
                                "verify_rc": verify.returncode,
                                "verify_modules": verify_modules,
                                "nested_modules_installed": sorted(m for m in seen_modules if m != target_module),
                                "case_variant_alias": nested_missing,
                            },
                        )
                        return True
                    verify_text = f"{verify.stdout or ''}\n{verify.stderr or ''}"
                    nested_missing = _extract_missing_module(verify_text)
            if nested_missing and _STDLIB_COMPAT_SHIMS.get(_module_root(nested_missing)):
                shim_ok = _install_stdlib_compat_shim_in_venv(
                    venv_python=venv_python,
                    module=nested_missing,
                    cwd=paper_root or str(run_dir),
                    timeout_sec=120,
                    logs_dir=logs_dir,
                    run_dir=run_dir,
                    log_suffix=log_suffix,
                )
                if shim_ok:
                    verify = run_command(
                        [str(venv_python), "-c", verify_code],
                        cwd=paper_root or str(run_dir),
                        timeout_sec=120,
                    )
                    persist_command_result(verify, logs_dir, prefix=f"fix_host_venv_verify_{log_suffix}_stdlib_shim")
                    record["stdlib_compat_shim"] = nested_missing
                    record["verify_rc_after_stdlib_shim"] = verify.returncode
                    if verify.returncode == 0:
                        append_event(
                            run_dir,
                            "fix_host_venv_install",
                            {
                                "ok": True,
                                "module": target_module,
                                "package": package,
                                "label": label,
                                "cmd": install_cmd,
                                "verify_rc": verify.returncode,
                                "verify_modules": verify_modules,
                                "nested_modules_installed": sorted(m for m in seen_modules if m != target_module),
                                "stdlib_compat_shim": nested_missing,
                            },
                        )
                        return True
                    verify_text = f"{verify.stdout or ''}\n{verify.stderr or ''}"
                    nested_missing = _extract_missing_module(verify_text)
            record["verify_missing_module"] = nested_missing or ""
            append_event(run_dir, "fix_host_venv_verify_failed", record)
            if nested_missing and nested_missing not in seen_modules:
                append_event(
                    run_dir,
                    "fix_host_venv_nested_missing_module",
                    {
                        "target_module": target_module,
                        "module": nested_missing,
                        "source_module": current_module,
                    },
                )
                pending_modules.append(nested_missing)
                break
    append_event(
        run_dir,
        "fix_host_venv_install_failed",
        {
            "module": target_module,
            "package": package,
            "attempted": attempted,
        },
    )
    return False


def _write_jupyter_kernel_marker(run_dir: Path, prefix: Path) -> None:
    write_text(run_dir / ".jupyter_kernel_prefix", str(prefix.resolve()) + "\n")


def _install_run_venv_jupyter_kernel(
    *,
    cfg: dict[str, Any],
    run_dir: Path,
    logs_dir: Path,
    paper_root: str,
    kernel_name: str,
    attempt: int,
) -> bool:
    kernel = str(kernel_name or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", kernel):
        append_event(run_dir, "fix_jupyter_kernel_invalid_name", {"kernel": kernel})
        return False

    if not _install_missing_module_in_run_venv(
        cfg=cfg,
        run_dir=run_dir,
        logs_dir=logs_dir,
        paper_root=paper_root,
        module="ipykernel",
        context_modules=["ipykernel", "jupyter_client"],
        attempt=attempt,
    ):
        append_event(run_dir, "fix_jupyter_kernel_ipykernel_install_failed", {"kernel": kernel})
        return False

    venv_python = _run_venv_python(run_dir)
    prefix = run_dir / "jupyter"
    timeout = _host_venv_install_timeout(cfg)
    prefix.mkdir(parents=True, exist_ok=True)
    install_cmd = [
        str(venv_python),
        "-m",
        "ipykernel",
        "install",
        "--prefix",
        str(prefix),
        "--name",
        kernel,
        "--display-name",
        f"Python ({kernel})",
    ]
    env = os.environ.copy()
    data_dir = str((prefix / "share" / "jupyter").resolve(strict=False))
    existing = [part for part in str(env.get("JUPYTER_PATH") or "").split(os.pathsep) if part]
    env["JUPYTER_PATH"] = os.pathsep.join([data_dir, *[part for part in existing if part != data_dir]])
    res = run_command(install_cmd, cwd=paper_root or str(run_dir), timeout_sec=timeout, env=env)
    persist_command_result(res, logs_dir, prefix=f"fix_jupyter_kernel_install_{attempt}")
    if res.returncode != 0:
        append_event(
            run_dir,
            "fix_jupyter_kernel_install_failed",
            {"kernel": kernel, "prefix": str(prefix), "rc": res.returncode},
        )
        return False

    cfg["jupyter_kernel_prefix"] = str(prefix.resolve())
    _write_jupyter_kernel_marker(run_dir, prefix)
    append_event(
        run_dir,
        "fix_jupyter_kernel_install",
        {"ok": True, "kernel": kernel, "prefix": str(prefix.resolve()), "jupyter_path": data_dir},
    )
    return True


def _torch_scatter_fallback_in_container_shell() -> str:
    """
    Inject a minimal `torch_scatter` python package into the current environment.
    This is a generic last resort when torch-scatter wheels/conda packages are unavailable.
    """
    return (
        "python - <<'PY'\n"
        "import os, site, pathlib\n"
        "sp = site.getsitepackages()[0]\n"
        "pkg = pathlib.Path(sp) / 'torch_scatter'\n"
        "pkg.mkdir(parents=True, exist_ok=True)\n"
        "(pkg / '__init__.py').write_text(\n"
        '    "import torch\\n\\n"\n'
        '    "def _expand_index(index, src, dim):\\n"\n'
        '    "    if index.dtype != torch.long: index = index.long()\\n"\n'
        '    "    if dim < 0: dim = src.dim() + dim\\n"\n'
        '    "    if index.dim() == 1 and src.dim() > 1:\\n"\n'
        '    "        shape = [1] * src.dim()\\n"\n'
        '    "        shape[dim] = index.numel()\\n"\n'
        '    "        index = index.view(*shape)\\n"\n'
        '    "    return index.expand_as(src)\\n\\n"\n'
        '    "def scatter_add(src, index, dim=0, out=None, dim_size=None):\\n"\n'
        '    "    if out is None:\\n"\n'
        '    "        if dim_size is None: dim_size = int(index.max().item()) + 1 if index.numel() else 0\\n"\n'
        '    "        out_shape = list(src.shape); out_shape[dim] = dim_size\\n"\n'
        '    "        out = torch.zeros(*out_shape, dtype=src.dtype, device=src.device)\\n"\n'
        '    "    idx = _expand_index(index, src, dim)\\n"\n'
        '    "    return out.scatter_add(dim, idx, src)\\n\\n"\n'
        '    "def scatter_max(src, index, dim=0, out=None, dim_size=None):\\n"\n'
        '    "    if index.dtype != torch.long: index = index.long()\\n"\n'
        '    "    if dim < 0: dim = src.dim() + dim\\n"\n'
        '    "    if dim_size is None: dim_size = int(index.max().item()) + 1 if index.numel() else 0\\n"\n'
        '    "    if src.dim() == 1:\\n"\n'
        "    \"        outv = torch.full((dim_size,), -float('inf'), dtype=src.dtype, device=src.device)\\n\"\n"
        '    "        arg = torch.full((dim_size,), -1, dtype=torch.long, device=src.device)\\n"\n'
        '    "        for i in range(src.numel()):\\n"\n'
        '    "            j = int(index[i].item()); v = src[i]\\n"\n'
        '    "            if v > outv[j]: outv[j] = v; arg[j] = i\\n"\n'
        '    "        return outv, arg\\n"\n'
        '    "    dims = list(range(src.dim())); dims[0], dims[dim] = dims[dim], dims[0]\\n"\n'
        '    "    inv = [0]*len(dims)\\n"\n'
        '    "    for i,d in enumerate(dims): inv[d]=i\\n"\n'
        '    "    srcp = src.permute(dims)\\n"\n'
        "    \"    outp = torch.full((dim_size, *srcp.shape[1:]), -float('inf'), dtype=src.dtype, device=src.device)\\n\"\n"
        '    "    argp = torch.full((dim_size, *srcp.shape[1:]), -1, dtype=torch.long, device=src.device)\\n"\n'
        '    "    for i in range(srcp.shape[0]):\\n"\n'
        '    "        j = int(index[i].item()); v = srcp[i]\\n"\n'
        '    "        better = v > outp[j]\\n"\n'
        '    "        outp[j] = torch.where(better, v, outp[j])\\n"\n'
        '    "        argp[j] = torch.where(better, torch.full_like(argp[j], i), argp[j])\\n"\n'
        '    "    return outp.permute(inv), argp.permute(inv)\\n\\n"\n'
        "    \"def scatter(src, index, dim=0, out=None, dim_size=None, reduce='sum'):\\n\"\n"
        "    \"    if reduce in {'sum','add'}: return scatter_add(src,index,dim=dim,out=out,dim_size=dim_size)\\n\"\n"
        "    \"    if reduce=='mean':\\n\"\n"
        '    "        outv = scatter_add(src,index,dim=dim,out=out,dim_size=dim_size)\\n"\n'
        '    "        cnt = scatter_add(torch.ones_like(src),index,dim=dim,out=None,dim_size=dim_size).clamp(min=1)\\n"\n'
        '    "        return outv/cnt\\n"\n'
        "    \"    if reduce=='max': return scatter_max(src,index,dim=dim,out=out,dim_size=dim_size)\\n\"\n"
        "    \"    raise ValueError('unsupported reduce')\\n\"\n"
        ")\n"
        "print('torch_scatter_fallback_installed', pkg)\n"
        "PY\n"
    )


def fix_node(state: dict[str, Any]) -> dict[str, Any]:
    cfg = state.get("config", {})
    run_info = state.get("run", {})
    run_dir = Path(run_info.get("dir") or "")
    logs_dir = Path(run_info.get("logs_dir") or (run_dir / "logs"))
    fixes_dir = ensure_dir(run_info.get("fixes_dir") or (run_dir / "fixes"))

    attempt = int(state.get("attempt") or 0) + 1
    state["attempt"] = attempt
    max_attempts = _resolve_max_attempts(state, cfg)

    # Stop condition
    if attempt > max_attempts:
        state["status"] = "failed"
        append_event(
            run_dir,
            "fix_stop",
            {"reason": "max_attempts_exceeded", "attempt": attempt, "max_attempts": max_attempts},
        )
        state.setdefault("history", []).append(
            {"kind": "fix_stop", "data": {"attempt": attempt, "max_attempts": max_attempts}}
        )
        return state

    run_result = state.get("run_result") or {}
    stderr_tail = str(run_result.get("stderr_tail") or "")
    stdout_tail = str(run_result.get("stdout_tail") or "")
    failed_task = run_result.get("failed_task")

    paper_root = (cfg.get("paper_root") or ".").strip() or "."
    docker_enabled = bool(cfg.get("docker_enabled", True))
    python_spec = str(cfg.get("python_spec") or "3.11").strip()

    append_event(run_dir, "fix_start", {"attempt": attempt, "failed_task": failed_task})
    state.setdefault("history", []).append(
        {"kind": "fix_start", "data": {"attempt": attempt, "failed_task": failed_task}}
    )

    failed_task_artifacts = _failed_task_artifact_context(run_result, run_dir)
    failed_task_cmd = run_result.get("failed_task_cmd") or ""
    failed_task_cmd_text = _to_shell([str(x) for x in failed_task_cmd]) if isinstance(failed_task_cmd, list) else str(failed_task_cmd)
    missing_module_context = "\n".join(
        part
        for part in [
            stderr_tail,
            stdout_tail,
            failed_task_cmd_text,
            json.dumps(failed_task_cmd or "", ensure_ascii=False),
            json.dumps(failed_task_artifacts, ensure_ascii=False),
        ]
        if part
    )
    failed_import_modules = _extract_failed_import_modules(missing_module_context)
    missing = (
        _extract_missing_module(stderr_tail)
        or _extract_missing_module(stdout_tail)
        or _extract_missing_module(json.dumps(failed_task_artifacts, ensure_ascii=False))
        or (failed_import_modules[0] if failed_import_modules else None)
    )
    missing_kernel = _extract_missing_jupyter_kernel(missing_module_context)
    notebook_import_modules = _notebook_import_modules_from_failed_cmd(failed_task_cmd, paper_root)
    related_missing_modules = (
        _dedupe_modules(
            [
                *([missing] if missing else []),
                *failed_import_modules,
                *(_related_import_modules_for_missing(missing, missing_module_context) if missing else []),
                *notebook_import_modules,
            ]
        )
        if missing
        else []
    )

    # Deterministic quick-fix: smoke task points to a missing script (common when we used a generic template).
    missing_file = _extract_missing_file(stderr_tail)
    if missing_file:
        ok_patch, patch_detail = _patch_tasks_missing_script(
            cfg=cfg,
            paper_root=paper_root,
            fixes_dir=fixes_dir,
            attempt=attempt,
            missing_file=missing_file,
        )
        if ok_patch:
            append_event(run_dir, "fix_edit_tasks_missing_script", {"ok": True, **patch_detail})
            state.setdefault("history", []).append(
                {"kind": "fix_edit_tasks_missing_script", "data": patch_detail}
            )
            state["status"] = "running"
            return state
        # If the missing file is a known entrypoint name, rewrite tasks.yaml to use an existing entry.
        missing_name = Path(missing_file).name.lower()
        if missing_name in {"launcher.py", "run.py", "eval.py", "main.py", "app.py"}:
            entry = _pick_smoke_entrypoint(paper_root)
            if entry and entry.lower() != missing_name:
                try:
                    tasks_path = str(cfg.get("tasks_path") or "").strip()
                    if tasks_path and Path(tasks_path).exists():
                        txt = Path(tasks_path).read_text(encoding="utf-8", errors="ignore")
                        write_text(fixes_dir / f"fix_{attempt:03d}_tasks_before.txt", txt)
                        # Best-effort: replace the missing script with the discovered one.
                        patched = txt.replace(missing_name, entry)
                        if patched != txt:
                            write_text(Path(tasks_path), patched)
                            write_text(
                                fixes_dir / f"fix_{attempt:03d}_tasks_patch_entrypoint.txt",
                                f"Patched tasks file to fix missing entrypoint:\n- path: {tasks_path}\n- missing: {missing_name}\n- using: {entry}\n",
                            )
                            append_event(
                                run_dir,
                                "fix_edit_tasks_deterministic",
                                {"path": tasks_path, "ok": True, "missing": missing_name, "using": entry},
                            )
                            state.setdefault("history", []).append(
                                {
                                    "kind": "fix_edit_tasks_deterministic",
                                    "data": {"path": tasks_path, "missing": missing_name, "using": entry},
                                }
                            )
                            state["status"] = "running"
                            return state
                except Exception:
                    pass

    if missing_kernel and not docker_enabled:
        append_event(run_dir, "fix_jupyter_kernel_missing", {"kernel": missing_kernel, "environment": "host"})
        state.setdefault("history", []).append(
            {
                "kind": "fix_jupyter_kernel_missing",
                "data": {"kernel": missing_kernel, "environment": "host"},
            }
        )
        if _install_run_venv_jupyter_kernel(
            cfg=cfg,
            run_dir=run_dir,
            logs_dir=logs_dir,
            paper_root=paper_root,
            kernel_name=missing_kernel,
            attempt=attempt,
        ):
            state["config"] = cfg
            state["status"] = "running"
            return state

    if missing_kernel and docker_enabled:
        append_event(run_dir, "fix_jupyter_kernel_missing", {"kernel": missing_kernel, "environment": "docker"})
        if _add_extra_pip_package(cfg, "ipykernel"):
            state["config"] = cfg
            ok_img, img_or_msg = docker_ensure_paper_image(
                cfg,
                paper_key=str(cfg.get("paper_key") or "paper"),
                paper_root_host=str(Path(paper_root).resolve()),
                python_spec=python_spec,
                timeout_sec=_docker_build_timeout(cfg),
            )
            append_event(
                run_dir,
                "fix_rebuild_image_jupyter_kernel",
                {"ok": ok_img, "kernel": missing_kernel, "detail": img_or_msg},
            )
            state.setdefault("history", []).append(
                {
                    "kind": "fix_rebuild_image_jupyter_kernel",
                    "data": {"ok": ok_img, "kernel": missing_kernel, "detail": img_or_msg},
                }
            )
            if ok_img:
                cfg["docker_paper_image"] = img_or_msg
                state["config"] = cfg
                state["status"] = "running"
                return state

    if missing:
        append_event(run_dir, "fix_missing_module", {"module": missing})
        state.setdefault("history", []).append({"kind": "fix_missing_module", "data": {"module": missing}})

    local_module_dirs = (
        _find_local_module_pythonpath_dirs(paper_root, missing, context_text=missing_module_context) if missing else []
    )
    if missing and local_module_dirs:
        added_dirs: list[str] = []
        for local_dir in local_module_dirs:
            if _add_extra_pythonpath_dir(cfg, local_dir, paper_root=paper_root):
                added_dirs.append(local_dir)
        append_event(
            run_dir,
            "fix_missing_module_local_path",
            {
                "module": missing,
                "paper_root": paper_root,
                "candidate_dirs": local_module_dirs,
                "added_dirs": added_dirs,
            },
        )
        state.setdefault("history", []).append(
            {
                "kind": "fix_missing_module_local_path",
                "data": {"module": missing, "paper_root": paper_root, "added_dirs": added_dirs},
            }
        )
        if added_dirs:
            state["config"] = cfg
            state["status"] = "running"
            return state

    if missing and docker_enabled and missing != "torch_scatter" and not local_module_dirs:
        package_modules = related_missing_modules or [missing]
        packages: list[str] = []
        seen_packages: set[str] = set()
        for module_name in package_modules:
            for package in _candidate_pip_packages_for_module(paper_root=paper_root, module=module_name):
                key = _package_key(package)
                if not key or key in seen_packages:
                    continue
                packages.append(package)
                seen_packages.add(key)
        changed = False
        for package in packages:
            changed = _add_extra_pip_package(cfg, package) or changed
        if changed:
            state["config"] = cfg
            ok_img, img_or_msg = docker_ensure_paper_image(
                cfg,
                paper_key=str(cfg.get("paper_key") or "paper"),
                paper_root_host=str(Path(paper_root).resolve()),
                python_spec=python_spec,
                timeout_sec=_docker_build_timeout(cfg),
            )
            append_event(
                run_dir,
                "fix_rebuild_image_extra_pip",
                {"ok": ok_img, "packages": packages, "detail": img_or_msg},
            )
            state.setdefault("history", []).append(
                {
                    "kind": "fix_rebuild_image_extra_pip",
                    "data": {"ok": ok_img, "packages": packages, "detail": img_or_msg},
                }
            )
            if ok_img:
                import_ok, import_rc, import_tail = _validate_module_in_docker_image(
                    cfg=cfg,
                    image=img_or_msg,
                    paper_root=paper_root,
                    run_dir=run_dir,
                    module=missing,
                )
                append_event(
                    run_dir,
                    "fix_rebuild_image_extra_pip_verify",
                    {
                        "ok": import_ok,
                        "packages": packages,
                        "module": missing,
                        "related_modules": related_missing_modules,
                        "rc": import_rc,
                        "tail": import_tail,
                    },
                )
                state.setdefault("history", []).append(
                    {
                        "kind": "fix_rebuild_image_extra_pip_verify",
                        "data": {
                            "ok": import_ok,
                            "packages": packages,
                            "module": missing,
                            "rc": import_rc,
                        },
                    }
                )
                if not import_ok:
                    cfg.pop("docker_paper_image", None)
                    state["config"] = cfg
                else:
                    cfg["docker_paper_image"] = img_or_msg
                    state["config"] = cfg
                    state["status"] = "running"
                    return state

    if missing and not docker_enabled and not local_module_dirs:
        if _install_missing_module_in_run_venv(
            cfg=cfg,
            run_dir=run_dir,
            logs_dir=logs_dir,
            paper_root=paper_root,
            module=missing,
            context_modules=related_missing_modules,
            attempt=attempt,
        ):
            state["status"] = "running"
            return state

    # Deterministic fix: missing torch_scatter.
    # Prefer rebuilding the paper image. A semantic stub can make a paper run
    # while silently changing results, so it is opt-in only.
    if missing == "torch_scatter" and docker_enabled:
        _add_extra_pip_package(cfg, "torch-scatter")
        state["config"] = cfg
        ok_img, img_or_msg = docker_ensure_paper_image(
            cfg,
            paper_key=str(cfg.get("paper_key") or "paper"),
            paper_root_host=str(Path(paper_root).resolve()),
            python_spec=python_spec,
            timeout_sec=_docker_build_timeout(cfg),
        )
        if ok_img:
            import_ok, import_rc, import_tail = _validate_module_in_docker_image(
                cfg=cfg,
                image=img_or_msg,
                paper_root=paper_root,
                run_dir=run_dir,
                module=missing,
            )
            append_event(
                run_dir,
                "fix_rebuild_image_torch_scatter_verify",
                {"ok": import_ok, "rc": import_rc, "tail": import_tail},
            )
            if import_ok:
                cfg["docker_paper_image"] = img_or_msg
                state["config"] = cfg
                append_event(run_dir, "fix_install_torch_scatter", {"ok": True, "strategy": "paper_image"})
                state.setdefault("history", []).append(
                    {"kind": "fix_install_torch_scatter", "data": {"ok": True, "strategy": "paper_image"}}
                )
                state["status"] = "running"
                return state
        if not _semantic_stubs_allowed(cfg):
            append_event(
                run_dir,
                "fix_torch_scatter_semantic_stub_skipped",
                {"reason": "semantic_stubs_disabled", "detail": img_or_msg},
            )
            state.setdefault("history", []).append(
                {
                    "kind": "fix_torch_scatter_semantic_stub_skipped",
                    "data": {"reason": "semantic_stubs_disabled"},
                }
            )
        else:
            shell = (
                _torch_scatter_fallback_in_container_shell()
                + "\npython -c \"import torch_scatter; print('torch_scatter_ok_after_fallback')\""
            )
            if ok_img:
                docker_cmd = docker_run_paper_image(
                    image=img_or_msg,
                    paper_root_host=str(Path(paper_root).resolve()),
                    run_dir_host=str(run_dir),
                    cwd_container="/app",
                    cmd=["bash", "-lc", shell],
                    env={},
                    env_passthrough=_docker_env_passthrough(cfg),
                    **_docker_runtime_kwargs(cfg),
                )
                res = run_command(cmd=docker_cmd, cwd=str(run_dir), timeout_sec=900)
                persist_command_result(res, logs_dir, prefix=f"fix_torch_scatter_stub_{attempt}")
                if res.returncode == 0:
                    append_event(run_dir, "fix_install_torch_scatter_stub", {"ok": True, "strategy": "semantic_stub"})
                    state["status"] = "running"
                    return state

    # LLM triage: propose a fix plan (default enabled)
    if bool(cfg.get("no_llm")):
        state["status"] = "failed"
        append_event(run_dir, "fix_no_llm", {"reason": "no_deterministic_fix_matched"})
        state.setdefault("history", []).append(
            {"kind": "fix_no_llm", "data": {"reason": "no_deterministic_fix_matched"}}
        )
        return state

    llm_cfg = resolve_llm_config(
        cfg.get("llm_provider") or "", cfg.get("llm_model") or "", cfg.get("llm_base_url") or ""
    )
    path_constraints = {
        "paper_root": "/app" if docker_enabled else str(Path(paper_root or ".").resolve()),
        "run_dir": "/workspace/run_dir" if docker_enabled else str(run_dir.resolve()),
        "artifact_dir": "/workspace/run_dir/artifacts"
        if docker_enabled
        else str((run_dir / "artifacts").resolve()),
    }
    system = _fix_system_prompt()
    prompt = {
        "attempt": attempt,
        "paper_root": paper_root,
        "failed_task": failed_task,
        "failed_task_cmd": run_result.get("failed_task_cmd") or [],
        "failed_task_cwd": run_result.get("failed_task_cwd") or "",
        "judge": state.get("judge") or {},
        "baseline": state.get("baseline") or {},
        "paper_metric_targets": cfg.get("paper_metric_targets") or [],
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "recent_failure_context": _recent_failure_context(state, run_dir),
        "failed_task_artifacts": failed_task_artifacts,
        "constraints": {
            "prefer_wrapper_env_fixes": True,
            "avoid_core_source_changes": True,
            "must_be_reproducible": True,
            "preserve_experiment_semantics": True,
            "no_mock_metrics_or_semantic_stubs": not _semantic_stubs_allowed(cfg),
            "no_destructive_commands": True,
            "execution_environment": "docker" if docker_enabled else "host",
            "paths": path_constraints,
            "host_dependency_installs_allowed": _host_dependency_installs_allowed(cfg),
            "primary_goal": "make tasks produce metric artifacts that can be compared against paper_metric_targets",
            "auto_apply_policy": (
                "Only low/medium-risk unblocked fixes with non-empty evidence may be auto-applied. "
                "If the correct diagnosis is private data, missing checkpoint, API/model-server "
                "dependency, or a high-risk semantic change, set blocked_by or risk=high and return "
                "actions=[]."
            ),
        },
        "output_schema": {
            "category": "env|deps|path|encoding|data|runtime|other",
            "root_cause": "short string",
            "evidence": ["specific stdout/stderr/task clue supporting the diagnosis"],
            "risk": "low|medium|high",
            "blocked_by": "empty string or unavailable data/checkpoint/API/private resource",
            "actions": [
                {"type": "command", "cmd": ["..."], "cwd": ".", "timeout_sec": 600, "why": "short"},
                {"type": "edit", "path": "relative/path", "content": "full new file content", "why": "short"},
            ],
            "confidence": 0.0,
        },
    }
    plan = llm_json(prompt=json.dumps(prompt, ensure_ascii=False), system=system, cfg=llm_cfg)
    write_text(
        fixes_dir / f"fix_{attempt:03d}_plan.json",
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
    )
    append_event(run_dir, "fix_plan", {"plan": plan})
    state.setdefault("history", []).append({"kind": "fix_plan", "data": {"plan": plan}})

    # If LLM call failed, stop gracefully with a helpful record.
    if isinstance(plan, dict) and plan.get("status") == "error":
        state["status"] = "failed"
        append_event(
            run_dir,
            "fix_llm_error",
            {"error": plan.get("error"), "provider": plan.get("provider"), "model": plan.get("model")},
        )
        state.setdefault("history", []).append(
            {
                "kind": "fix_llm_error",
                "data": {
                    "error": plan.get("error"),
                    "provider": plan.get("provider"),
                    "model": plan.get("model"),
                },
            }
        )
        write_text(
            fixes_dir / f"fix_{attempt:03d}_llm_error.txt",
            "LLM call failed during fix triage.\n"
            f"provider: {plan.get('provider')}\n"
            f"model: {plan.get('model')}\n"
            f"error: {plan.get('error')}\n"
            "\n"
            "How to proceed:\n"
            "- rerun with --no-llm to disable LLM fixes, OR\n"
            "- set a valid provider/model in env (MODEL_PROVIDER / API_KEY / MODEL), OR\n"
            "- set MODEL_PROVIDER=openai-codex after `codex login`, OR\n"
            "- set OPENAI_MODEL to a model available to your account.\n",
        )
        return state

    if isinstance(plan, dict):
        block_reason, block_detail = _fix_plan_auto_apply_block_reason(plan, cfg)
        if block_reason:
            event_data = {
                "reason": block_reason,
                "detail": block_detail,
                "risk": _plan_risk(plan),
                "blocked_by": _plan_blocked_by(plan),
            }
            append_event(run_dir, "fix_plan_not_auto_applied", event_data)
            state.setdefault("history", []).append({"kind": "fix_plan_not_auto_applied", "data": event_data})
            if _disable_unrecoverable_failed_task(
                cfg=cfg,
                run_dir=run_dir,
                fixes_dir=fixes_dir,
                failed_task=failed_task,
                run_result=run_result,
                plan=plan,
                reason=block_reason,
                detail=block_detail,
                attempt=attempt,
            ):
                state["config"] = cfg
                state["status"] = "running"
                state.setdefault("history", []).append(
                    {"kind": "fix_continue_after_unrecoverable_task", "data": {"task": failed_task}}
                )
                return state
            state["status"] = "failed"
            return state

    # Apply only safe "command" actions automatically; record others for manual review.
    actions = plan.get("actions") if isinstance(plan, dict) else None
    applied_any = False
    if isinstance(actions, list):
        for j, act in enumerate(actions, 1):
            if not isinstance(act, dict):
                continue
            if act.get("type") == "command":
                cmd = act.get("cmd")
                if not isinstance(cmd, list) or not all(isinstance(x, str) for x in cmd):
                    continue
                if _is_rerun_failed_task_command(cmd, run_result.get("failed_task_cmd")):
                    append_event(
                        run_dir,
                        "fix_command_skipped_rerun",
                        {"cmd": cmd, "failed_task": failed_task},
                    )
                    state.setdefault("history", []).append(
                        {
                            "kind": "fix_command_skipped_rerun",
                            "data": {"cmd": cmd, "failed_task": failed_task},
                        }
                    )
                    continue
                if (
                    not docker_enabled
                    and not _host_dependency_installs_allowed(cfg)
                    and _is_host_dependency_install_command(cmd)
                ):
                    append_event(
                        run_dir,
                        "fix_command_skipped_host_dependency_install",
                        {"cmd": cmd, "failed_task": failed_task},
                    )
                    state.setdefault("history", []).append(
                        {
                            "kind": "fix_command_skipped_host_dependency_install",
                            "data": {"cmd": cmd, "failed_task": failed_task},
                        }
                    )
                    continue
                cwd = str(act.get("cwd") or "").strip()
                if not cwd or cwd in {".", "./"}:
                    cwd = paper_root or "."
                timeout = int(act.get("timeout_sec") or 600)
                source_edit = _is_source_edit_command(cmd)
                if source_edit and not _source_edit_command_auto_apply_allowed(
                    cfg=cfg,
                    cmd=cmd,
                    cwd=cwd,
                    paper_root=paper_root,
                    run_dir=run_dir,
                    docker_enabled=docker_enabled,
                    plan=plan,
                ):
                    append_event(
                        run_dir,
                        "fix_command_skipped_source_edit",
                        {
                            "cmd": cmd,
                            "failed_task": failed_task,
                            "workspace_source_edits_allowed": _workspace_source_edits_allowed(
                                cfg, paper_root, run_dir
                            ),
                        },
                    )
                    state.setdefault("history", []).append(
                        {
                            "kind": "fix_command_skipped_source_edit",
                            "data": {"cmd": cmd, "failed_task": failed_task},
                        }
                    )
                    continue
                if source_edit:
                    append_event(
                        run_dir,
                        "fix_command_source_edit_allowed",
                        {
                            "cmd": cmd,
                            "cwd": cwd,
                            "risk": _plan_risk(plan),
                            "workspace_snapshot": _workspace_source_edits_allowed(cfg, paper_root, run_dir),
                        },
                    )
                if _is_dangerous_fix_command(cmd):
                    append_event(
                        run_dir,
                        "fix_command_skipped_dangerous",
                        {"cmd": cmd, "failed_task": failed_task},
                    )
                    state.setdefault("history", []).append(
                        {
                            "kind": "fix_command_skipped_dangerous",
                            "data": {"cmd": cmd, "failed_task": failed_task},
                        }
                    )
                    continue
                if docker_enabled and _is_host_dependency_install_command(cmd):
                    packages, extra_indexes = _extract_pip_install_requests(cmd)
                    changed = False
                    for package in packages:
                        changed = _add_extra_pip_package(cfg, package) or changed
                    for index_url in extra_indexes:
                        changed = _add_docker_extra_index_url(cfg, index_url) or changed
                    if packages:
                        state["config"] = cfg
                        ok_img, img_or_msg = docker_ensure_paper_image(
                            cfg,
                            paper_key=str(cfg.get("paper_key") or "paper"),
                            paper_root_host=str(Path(paper_root).resolve()),
                            python_spec=python_spec,
                            timeout_sec=_docker_build_timeout(cfg),
                        )
                        import_ok = True
                        import_rc = 0
                        import_tail = ""
                        if ok_img and missing:
                            import_ok, import_rc, import_tail = _validate_module_in_docker_image(
                                cfg=cfg,
                                image=img_or_msg,
                                paper_root=paper_root,
                                run_dir=run_dir,
                                module=missing,
                            )
                        append_event(
                            run_dir,
                            "fix_command_rebuilt_image_dependency_install",
                            {
                                "cmd": cmd,
                                "packages": packages,
                                "extra_index_urls": extra_indexes,
                                "changed": changed,
                                "ok": bool(ok_img and import_ok),
                                "detail": img_or_msg,
                                "verify_module": missing or "",
                                "verify_rc": import_rc,
                                "verify_tail": import_tail,
                            },
                        )
                        state.setdefault("history", []).append(
                            {
                                "kind": "fix_command_rebuilt_image_dependency_install",
                                "data": {
                                    "packages": packages,
                                    "extra_index_urls": extra_indexes,
                                    "changed": changed,
                                    "ok": bool(ok_img and import_ok),
                                    "verify_module": missing or "",
                                    "verify_rc": import_rc,
                                },
                            }
                        )
                        if ok_img and import_ok:
                            cfg["docker_paper_image"] = img_or_msg
                            state["config"] = cfg
                            applied_any = True
                        continue
                    append_event(
                        run_dir,
                        "fix_command_skipped_docker_ephemeral_dependency_install",
                        {"cmd": cmd, "failed_task": failed_task},
                    )
                    state.setdefault("history", []).append(
                        {
                            "kind": "fix_command_skipped_docker_ephemeral_dependency_install",
                            "data": {"cmd": cmd, "failed_task": failed_task},
                        }
                    )
                    continue
                source_snapshot_before = _snapshot_source_tree(paper_root) if source_edit and not docker_enabled else {}
                if docker_enabled:
                    ok_img, img_or_msg = docker_ensure_paper_image(
                        cfg,
                        paper_key=str(cfg.get("paper_key") or "paper"),
                        paper_root_host=str(Path(paper_root).resolve()),
                        python_spec=python_spec,
                        timeout_sec=_docker_build_timeout(cfg),
                    )
                    if not ok_img:
                        continue
                    # Per-paper image mode: run fixes inside /app.
                    # Avoid conda-specific wrappers.
                    shell_raw = _to_shell(cmd)
                    shell = _normalize_container_path_text(shell_raw, paper_root, run_dir)
                    if shell != shell_raw:
                        append_event(
                            run_dir,
                            "fix_command_container_paths_normalized",
                            {"cmd_index": j, "cwd": cwd},
                        )
                    docker_cmd = docker_run_paper_image(
                        image=img_or_msg,
                        paper_root_host=str(Path(paper_root).resolve()),
                        run_dir_host=str(run_dir),
                        cwd_container="/app",
                        cmd=["bash", "-lc", shell],
                        env={},
                        env_passthrough=_docker_env_passthrough(cfg),
                        **_docker_runtime_kwargs(cfg),
                    )
                    res = run_command(cmd=docker_cmd, cwd=str(run_dir), timeout_sec=timeout)
                else:
                    cwd_raw = cwd
                    cwd = _container_cwd_to_host(cwd, paper_root, run_dir)
                    if cwd != cwd_raw:
                        append_event(
                            run_dir,
                            "fix_command_cwd_container_path_mapped",
                            {"cmd_index": j, "cwd": cwd_raw, "mapped_cwd": cwd},
                        )
                    if not _fix_command_cwd_allowed(cwd, paper_root, run_dir):
                        append_event(
                            run_dir,
                            "fix_command_skipped_cwd_outside_workspace",
                            {"cmd": cmd, "cwd": cwd, "paper_root": paper_root, "run_dir": str(run_dir)},
                        )
                        state.setdefault("history", []).append(
                            {
                                "kind": "fix_command_skipped_cwd_outside_workspace",
                                "data": {"cmd": cmd, "cwd": cwd},
                            }
                        )
                        continue
                    argv = _normalize_llm_cmd_for_platform(cmd)
                    res = run_command(cmd=argv, cwd=cwd, timeout_sec=timeout)
                persist_command_result(res, logs_dir, prefix=f"fix_cmd_{attempt}_{j}")
                ok = res.returncode == 0
                append_event(run_dir, "fix_command", {"cmd": cmd, "cwd": cwd, "ok": ok, "rc": res.returncode})
                state.setdefault("history", []).append(
                    {"kind": "fix_command", "data": {"cmd": cmd, "cwd": cwd, "ok": ok, "rc": res.returncode}}
                )
                if ok and source_snapshot_before:
                    source_snapshot_after = _snapshot_source_tree(paper_root)
                    changes = _source_tree_changes(source_snapshot_before, source_snapshot_after)
                    write_text(
                        fixes_dir / f"fix_{attempt:03d}_{j:02d}_source_changes.json",
                        json.dumps(changes, ensure_ascii=False, indent=2) + "\n",
                    )
                    append_event(
                        run_dir,
                        "fix_command_source_changes",
                        {
                            "cmd_index": j,
                            "created": changes["created"][:50],
                            "deleted": changes["deleted"][:50],
                            "modified": changes["modified"][:50],
                        },
                    )
                if ok and docker_enabled:
                    rewritten = _rewrite_container_path_leaks(paper_root, run_dir)
                    if rewritten:
                        append_event(
                            run_dir,
                            "fix_rewrite_container_path_leaks",
                            {"count": len(rewritten), "files": rewritten[:25]},
                        )
                        state.setdefault("history", []).append(
                            {
                                "kind": "fix_rewrite_container_path_leaks",
                                "data": {"count": len(rewritten), "files": rewritten[:25]},
                            }
                        )
                applied_any = applied_any or ok
            elif act.get("type") == "edit":
                tasks_path = str(cfg.get("tasks_path") or "").strip()
                path = str(act.get("path") or "").strip()
                content = act.get("content")
                if not path or not isinstance(content, str):
                    continue
                try:
                    target = Path(path)
                    if not target.is_absolute():
                        if tasks_path and (Path(path).name == Path(tasks_path).name or path.replace("\\", "/") in {
                            "tasks.yaml",
                            "tasks.yml",
                        }):
                            target = Path(tasks_path)
                        else:
                            target = Path(paper_root) / target
                    resolved = target.resolve(strict=False)
                    is_tasks_edit = bool(tasks_path) and str(resolved).lower() == str(Path(tasks_path).resolve()).lower()
                    is_workspace_source_edit = (
                        not is_tasks_edit
                        and _workspace_source_edits_allowed(cfg, paper_root, run_dir)
                        and _plan_risk(plan) in {"low", "medium"}
                        and _path_inside(resolved, Path(paper_root or ".").resolve(strict=False))
                        and resolved.exists()
                    )
                    if not is_tasks_edit and not is_workspace_source_edit:
                        continue
                    if docker_enabled:
                        content = _normalize_container_path_text(content, paper_root, run_dir)
                    source_snapshot_before = (
                        _snapshot_source_tree(paper_root) if is_workspace_source_edit and not docker_enabled else {}
                    )
                    write_text(target, content)
                    if is_tasks_edit:
                        write_text(
                            fixes_dir / f"fix_{attempt:03d}_edit_tasks.txt", f"Edited tasks file: {tasks_path}\n"
                        )
                        append_event(run_dir, "fix_edit_tasks", {"path": tasks_path, "ok": True})
                        state.setdefault("history", []).append(
                            {"kind": "fix_edit_tasks", "data": {"path": tasks_path}}
                        )
                    else:
                        changes = _source_tree_changes(source_snapshot_before, _snapshot_source_tree(paper_root))
                        write_text(
                            fixes_dir / f"fix_{attempt:03d}_edit_source_changes.json",
                            json.dumps(changes, ensure_ascii=False, indent=2) + "\n",
                        )
                        append_event(
                            run_dir,
                            "fix_edit_workspace_source",
                            {"path": str(resolved), "ok": True, "modified": changes["modified"][:50]},
                        )
                        state.setdefault("history", []).append(
                            {"kind": "fix_edit_workspace_source", "data": {"path": str(resolved)}}
                        )
                    applied_any = True
                except Exception:
                    continue

    # If nothing applied, stop (we still recorded the plan).
    if not applied_any:
        if isinstance(plan, dict) and _disable_unrecoverable_failed_task(
            cfg=cfg,
            run_dir=run_dir,
            fixes_dir=fixes_dir,
            failed_task=failed_task,
            run_result=run_result,
            plan=plan,
            reason="no_applicable_actions",
            detail=str(plan.get("root_cause") or "").strip(),
            attempt=attempt,
        ):
            state["config"] = cfg
            state["status"] = "running"
            state.setdefault("history", []).append(
                {"kind": "fix_continue_after_unrecoverable_task", "data": {"task": failed_task}}
            )
            return state
        state["status"] = "failed"
        append_event(run_dir, "fix_not_applied", {"reason": "no_applicable_actions"})
        state.setdefault("history", []).append(
            {"kind": "fix_not_applied", "data": {"reason": "no_applicable_actions"}}
        )
        return state

    state["status"] = "running"
    return state

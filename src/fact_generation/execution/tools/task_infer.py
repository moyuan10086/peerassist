from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from llm.client import llm_json, resolve_llm_config


@dataclass(frozen=True)
class InferResult:
    tasks: list[dict[str, Any]]
    evidence: dict[str, Any]


def _read_optional(path: Path, max_chars: int = 12000) -> str:
    try:
        if not path.exists():
            return ""
        txt = path.read_text(encoding="utf-8", errors="ignore")
        if len(txt) > max_chars:
            return txt[:max_chars] + "\n...(truncated)\n"
        return txt
    except Exception:
        return ""


def _guess_entrypoints(repo_root: Path) -> list[str]:
    # Prefer common top-level scripts, then shallow train/eval scripts.
    cands = [
        "launcher.py",
        "run.py",
        "train.py",
        "eval.py",
        "evaluate.py",
        "test.py",
        "main.py",
        "app.py",
    ]
    out: list[str] = []
    for c in cands:
        if (repo_root / c).exists():
            out.append(c)
    if len(out) < 8:
        for p in sorted(repo_root.rglob("*.py")):
            rel = p.relative_to(repo_root)
            parts = set(rel.parts)
            if any(x in parts for x in {".git", "__pycache__", "site-packages", "build", "dist"}):
                continue
            if len(rel.parts) > 3:
                continue
            name = p.name.lower()
            if name in {"train.py", "eval.py", "evaluate.py", "test.py", "run.py", "main.py"}:
                s = str(rel).replace("\\", "/")
                if s not in out:
                    out.append(s)
            if len(out) >= 12:
                break
    return out


def _clean_command_line(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    s = re.sub(r"^\s*(?:\$|>|#)\s*", "", s)
    s = re.sub(r"\s+#\s+.*$", "", s).strip()
    return s


def _join_continuations(lines: list[str]) -> list[str]:
    out: list[str] = []
    buf = ""
    for raw in lines:
        line = _clean_command_line(raw)
        if not line:
            continue
        if buf:
            buf += " " + line
        else:
            buf = line
        if buf.endswith("\\"):
            buf = buf[:-1].rstrip()
            continue
        out.append(buf.strip())
        buf = ""
    if buf:
        out.append(buf.strip())
    return out


def _looks_like_shell_command(s: str) -> bool:
    if not s:
        return False
    first = s.split(maxsplit=1)[0].lower()
    if first in {
        "python",
        "python3",
        "pip",
        "pip3",
        "conda",
        "mamba",
        "micromamba",
        "bash",
        "sh",
        "make",
        "pytest",
        "torchrun",
        "accelerate",
    }:
        return True
    return first.startswith("./") or first.endswith(".sh")


def _extract_example_commands_from_readme(readme_text: str) -> list[str]:
    """
    Extract likely shell commands from README code fences and prompt-like lines.
    """
    txt = readme_text or ""
    cmds: list[str] = []
    for m in re.finditer(r"```(?:bash|sh|shell|console|text|python)?\s*([\s\S]*?)```", txt, flags=re.IGNORECASE):
        block = (m.group(1) or "").strip()
        for s in _join_continuations(block.splitlines()):
            if not _looks_like_shell_command(s):
                continue
            cmds.append(s)
    for raw in txt.splitlines():
        line = raw.strip()
        if not re.match(r"^(?:\$|>)\s+", line):
            continue
        s = _clean_command_line(line)
        if _looks_like_shell_command(s):
            cmds.append(s)
    out: list[str] = []
    seen: set[str] = set()
    for cmd in cmds:
        key = re.sub(r"\s+", " ", cmd).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
        if len(out) >= 80:
            break
    return out


def _detect_benchmark_datasets(repo_root: Path) -> list[str]:
    data_dir = repo_root / "data"
    if not data_dir.exists() or not data_dir.is_dir():
        return []
    out: list[str] = []
    for p in sorted(data_dir.iterdir()):
        if not p.is_dir():
            continue
        name = p.name.strip()
        if not name:
            continue
        out.append(name)
        if len(out) >= 20:
            break
    return out


def _entrypoint_arg_hints(repo_root: Path, entrypoints: list[str], max_chars: int = 4000) -> dict[str, str]:
    hints: dict[str, str] = {}
    for ep in entrypoints[:5]:
        txt = _read_optional(repo_root / ep, max_chars=max_chars)
        if not txt.strip():
            continue
        lines: list[str] = []
        for ln in txt.splitlines():
            s = ln.strip()
            if "add_argument(" in s or "ArgumentParser(" in s:
                lines.append(s)
            if len(lines) >= 40:
                break
        if lines:
            hints[ep] = "\n".join(lines)
    return hints


def _cmd_flag_value(cmd: list[str], *flags: str) -> str:
    for i, tok in enumerate(cmd[:-1]):
        if tok in flags:
            return str(cmd[i + 1])
    return ""


def _strip_flag(cmd: list[str], *flags: str) -> list[str]:
    out: list[str] = []
    skip_next = False
    for i, tok in enumerate(cmd):
        if skip_next:
            skip_next = False
            continue
        if tok in flags:
            if i + 1 < len(cmd):
                skip_next = True
            continue
        out.append(tok)
    return out


def _safe_id_part(raw: str) -> str:
    s = raw.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "task"


def _command_to_argv(raw: str) -> list[str]:
    s = (raw or "").strip()
    if not s:
        return []
    try:
        parts = shlex.split(s, posix=True)
    except Exception:
        return ["bash", "-lc", s]
    if not parts:
        return []
    if parts[0].endswith(".sh") or (parts[0].startswith("./") and parts[0].endswith(".sh")):
        return ["bash", *parts]
    shell_features = any(token in s for token in ["&&", "||", "|", ";", "$(", "`", ">", "<"])
    if shell_features or parts[0] in {"cd", "export"}:
        return ["bash", "-lc", s]
    return parts


def _command_family(raw: str) -> str:
    s = (raw or "").lower()
    if any(tok in s for tok in ["pip install", "conda env", "mamba env", "environment.yml", "requirements.txt"]):
        return "prepare"
    if any(tok in s for tok in ["preprocess", "prepare", "download", "convert"]):
        return "prepare"
    if any(tok in s for tok in ["eval", "evaluate", "test.py", "predict", "inference"]):
        return "eval"
    if any(tok in s for tok in ["train", "finetune", "fit"]) or re.search(r"\b(run|main)\.py\b", s):
        return "train"
    if "--help" in s or "-h" in s:
        return "smoke"
    return "reproduce"


def _target_identity(target: dict[str, Any]) -> str:
    parts = [
        str(target.get("dataset") or ""),
        str(target.get("scoring_function") or ""),
        str(target.get("method") or target.get("paper_claim") or ""),
    ]
    return "_".join(_safe_id_part(p) for p in parts if p).strip("_") or "paper_target"


def _target_command_score(target: dict[str, Any], raw_cmd: str) -> int:
    s = (raw_cmd or "").lower()
    score = 0
    dataset = str(target.get("dataset") or "").strip().lower()
    method = str(target.get("method") or target.get("paper_claim") or "").lower()
    scoring = str(target.get("scoring_function") or "").strip().lower()
    if dataset and dataset in s:
        score += 8
    if scoring and scoring in s:
        score += 5
    for token in re.split(r"[^a-z0-9]+", method):
        if len(token) >= 4 and token in s:
            score += 2
    family = _command_family(raw_cmd)
    if family == "eval":
        score += 4
    elif family == "train":
        score += 2
    return score


def _target_claims(target: dict[str, Any]) -> list[str]:
    claim = str(target.get("paper_claim") or target.get("method") or "").strip()
    return [claim] if claim else []


def _build_target_reproduction_tasks(
    *,
    readme_example_cmds: list[str],
    paper_metric_targets: list[dict[str, Any]],
    mode: str,
) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, target in enumerate(paper_metric_targets[:24], 1):
        if not isinstance(target, dict) or not isinstance(target.get("metrics"), dict):
            continue
        scored = [
            (_target_command_score(target, cmd), cmd)
            for cmd in readme_example_cmds
            if _command_family(cmd) in {"train", "eval", "reproduce"}
        ]
        scored = [(score, cmd) for score, cmd in scored if score > 0]
        if not scored:
            continue
        scored.sort(key=lambda item: item[0], reverse=True)
        score, raw_cmd = scored[0]
        ident = _target_identity(target)
        family = _command_family(raw_cmd)
        task_id = f"{family}_{ident}"
        if task_id in seen:
            task_id = f"{task_id}_{idx}"
        seen.add(task_id)
        tasks.append(
            {
                "id": task_id,
                "family": family,
                "enabled": mode == "full",
                "cwd": "{paper_root}",
                "cmd": _command_to_argv(raw_cmd),
                "timeout_sec": 86400 if family == "train" else 7200,
                "use_conda": True,
                "artifact_paths": ["metrics/**", "results/**", "outputs/**", "logs/**", "checkpoints/**"],
                "metric_artifact_path": f"metrics/{task_id}_metrics.json",
                "dataset": str(target.get("dataset") or ""),
                "method": str(target.get("method") or ""),
                "split": "test" if family == "eval" else "",
                "expected_metrics": dict(target.get("metrics") or {}),
                "paper_targets": [target],
                "claims": _target_claims(target),
                "verification_target": {
                    "source": "paper_metric_target",
                    "match_score": score,
                    "paper_table_id": target.get("paper_table_id", ""),
                },
            }
        )
    return tasks


def _build_generic_readme_tasks(readme_example_cmds: list[str], mode: str) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, raw in enumerate(readme_example_cmds, 1):
        family = _command_family(raw)
        if family not in {"prepare", "train", "eval", "reproduce"}:
            continue
        if family == "prepare":
            # Prepare commands are handled by _build_repo_prepare_tasks so they
            # can be ordered before training and de-duplicated with script files.
            continue
        task_id = f"{family}_readme_{idx}"
        if task_id in seen:
            continue
        seen.add(task_id)
        tasks.append(
            {
                "id": task_id,
                "family": family,
                "enabled": mode == "full",
                "cwd": "{paper_root}",
                "cmd": _command_to_argv(raw),
                "timeout_sec": 86400 if family == "train" else 7200,
                "use_conda": True,
                "artifact_paths": ["metrics/**", "results/**", "outputs/**", "logs/**", "checkpoints/**"],
            }
        )
        if len(tasks) >= 12:
            break
    return tasks


def _is_dependency_install_command(raw: str) -> bool:
    s = (raw or "").strip().lower()
    if not s:
        return False
    return any(
        token in s
        for token in [
            "pip install",
            "conda env",
            "mamba env",
            "micromamba env",
            "environment.yml",
            "requirements.txt",
            "setup.py install",
        ]
    )


def _build_repo_prepare_tasks(repo_root: Path, readme_example_cmds: list[str], mode: str) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    seen_cmds: set[str] = set()

    def add_prepare(raw_cmd: str, raw_id: str) -> None:
        cmd = re.sub(r"\s+", " ", (raw_cmd or "").strip())
        if not cmd or cmd in seen_cmds:
            return
        seen_cmds.add(cmd)
        tasks.append(
            {
                "id": f"prepare_{_safe_id_part(raw_id)}",
                "family": "prepare",
                "enabled": mode == "full",
                "cwd": "{paper_root}",
                "cmd": _command_to_argv(cmd),
                "timeout_sec": 7200,
                "use_conda": True,
                "artifact_paths": ["data/**", "datasets/**", "processed/**", "preprocessed/**"],
            }
        )

    for idx, raw in enumerate(readme_example_cmds, 1):
        if _command_family(raw) != "prepare" or _is_dependency_install_command(raw):
            continue
        add_prepare(raw, f"readme_{idx}")

    for rel in [
        "setup.sh",
        "preprocess.sh",
        "prepare.sh",
        "download.sh",
        "scripts/setup.sh",
        "scripts/preprocess.sh",
        "scripts/prepare.sh",
        "scripts/download.sh",
    ]:
        path = repo_root / rel
        if path.exists() and path.is_file():
            add_prepare(f"bash {rel.replace(os.sep, '/')}", rel)

    return tasks


def _build_readme_matrix_tasks(
    readme_example_cmds: list[str], datasets: list[str], mode: str
) -> list[dict[str, Any]]:
    parsed_runs: list[list[str]] = []
    for raw in readme_example_cmds:
        s = (raw or "").strip()
        if not s.startswith("python run.py "):
            continue
        try:
            parts = shlex.split(s, posix=True)
        except Exception:
            continue
        if len(parts) >= 2 and parts[0] == "python" and parts[1] == "run.py":
            parsed_runs.append(parts)

    if not parsed_runs:
        return []

    multi_dataset = len(datasets) > 1
    expanded_datasets = datasets if datasets else [""]
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for parts in parsed_runs:
        base_cmd = _strip_flag(_strip_flag(parts[2:], "-name", "--name"), "-data", "--data")
        explicit_name = _cmd_flag_value(parts, "-name", "--name").strip()
        score_func = _cmd_flag_value(parts, "-score_func", "--score_func").strip() or "run"
        opn = _cmd_flag_value(parts, "-opn", "--opn").strip()
        explicit_dataset = _cmd_flag_value(parts, "-data", "--data").strip()

        dataset_values = [explicit_dataset] if explicit_dataset else expanded_datasets
        for dataset in dataset_values:
            dataset_tag = _safe_id_part(dataset) if dataset else ""
            name_base = _safe_id_part(explicit_name or "_".join(x for x in [score_func, opn] if x))
            run_name = name_base
            if multi_dataset and dataset_tag:
                run_name = f"{name_base}_{dataset_tag}"
            task_id = f"train_{run_name}"
            if task_id in seen_ids:
                continue

            cmd = ["python", "run.py", "-name", run_name]
            if dataset:
                cmd.extend(["-data", dataset])
            cmd.extend(base_cmd)

            out.append(
                {
                    "id": task_id,
                    "family": "train",
                    "enabled": mode == "full",
                    "cwd": "{paper_root}",
                    "cmd": cmd,
                    "timeout_sec": 86400,
                    "use_conda": True,
                    "artifact_paths": ["checkpoints/**", "log/**"],
                    "dataset": dataset,
                    "method": run_name,
                }
            )
            seen_ids.add(task_id)
    return out


def _is_training_task(task: dict[str, Any]) -> bool:
    cmd = task.get("cmd")
    if not isinstance(cmd, list) or len(cmd) < 2:
        return False
    if cmd[0] != "python":
        return False
    if cmd[1] != "run.py":
        return False
    return "-h" not in cmd and "--help" not in cmd


def _target_matches_cmd(target: dict[str, Any], cmd: list[str]) -> bool:
    dataset = _cmd_flag_value(cmd, "-data", "--data").strip().lower()
    score_func = _cmd_flag_value(cmd, "-score_func", "--score_func").strip().lower()
    opn = _cmd_flag_value(cmd, "-opn", "--opn").strip().lower()
    method = str(target.get("method") or target.get("paper_claim") or "").lower()
    target_dataset = str(target.get("dataset") or "").strip().lower()
    target_score = str(target.get("scoring_function") or "").strip().lower()
    if dataset and target_dataset and dataset != target_dataset:
        return False
    if score_func and target_score and score_func != target_score:
        return False
    if score_func and (score_func not in method) and target_score and score_func != target_score:
        return False
    if opn and method and opn not in method:
        # Keep this soft: many papers omit the opn label in row text.
        return bool(score_func and (score_func in method or score_func == target_score))
    return bool(dataset or score_func or method)


def _best_target_for_command(
    paper_metric_targets: list[dict[str, Any]], cmd: list[str]
) -> dict[str, Any] | None:
    scored: list[tuple[int, dict[str, Any]]] = []
    dataset = _cmd_flag_value(cmd, "-data", "--data").strip().lower()
    score_func = _cmd_flag_value(cmd, "-score_func", "--score_func").strip().lower()
    opn = _cmd_flag_value(cmd, "-opn", "--opn").strip().lower()
    for target in paper_metric_targets:
        if not isinstance(target, dict) or not isinstance(target.get("metrics"), dict):
            continue
        if not _target_matches_cmd(target, cmd):
            continue
        score = len(target.get("metrics") or {})
        tds = str(target.get("dataset") or "").strip().lower()
        tsf = str(target.get("scoring_function") or "").strip().lower()
        method = str(target.get("method") or "").lower()
        if dataset and tds == dataset:
            score += 4
        if score_func and tsf == score_func:
            score += 4
        if score_func and score_func in method:
            score += 2
        if opn and opn in method:
            score += 2
        scored.append((score, target))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _append_eval_export_tasks(
    repo_root: Path,
    tasks: list[dict[str, Any]],
    *,
    paper_metric_targets: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    evaluator = repo_root / "codeeval_eval_ckpt.py"
    if not evaluator.exists():
        return tasks

    existing_ids = {str(t.get("id") or "").strip() for t in tasks if isinstance(t, dict)}
    out: list[dict[str, Any]] = list(tasks)
    for task in tasks:
        if not isinstance(task, dict) or not _is_training_task(task):
            continue
        cmd = task.get("cmd")
        assert isinstance(cmd, list)
        run_name = _cmd_flag_value(cmd, "-name", "--name").strip()
        task_id = str(task.get("id") or "").strip()
        if not run_name or not task_id:
            continue

        eval_id = f"eval_{task_id[6:]}" if task_id.startswith("train_") else f"eval_{task_id}"
        if eval_id in existing_ids:
            continue

        out_path = f"./metrics/{task_id}_test.json"
        target = _best_target_for_command(paper_metric_targets or [], cmd)
        claims = []
        if target:
            claim = str(target.get("paper_claim") or target.get("method") or "").strip()
            if claim:
                claims.append(claim)
        out.append(
            {
                "id": eval_id,
                "family": "eval",
                "enabled": bool(task.get("enabled", True)),
                "cwd": "{paper_root}",
                "cmd": [
                    "python",
                    "codeeval_eval_ckpt.py",
                    "--ckpt-dir",
                    "./checkpoints",
                    "--prefix",
                    run_name,
                    "--out",
                    out_path,
                    "--split",
                    "test",
                ],
                "timeout_sec": 1800,
                "use_conda": True,
                "artifact_paths": [out_path.lstrip("./")],
                "metric_artifact_path": f"metrics/{eval_id}_metrics.json",
                "dataset": str((target or {}).get("dataset") or task.get("dataset") or ""),
                "method": str((target or {}).get("method") or task.get("method") or run_name),
                "split": "test",
                "expected_metrics": dict(target.get("metrics") or {}) if target else {},
                "paper_targets": [target] if target else [],
                "claims": claims,
            }
        )
        existing_ids.add(eval_id)
    return out


def _finalize_tasks(
    *,
    repo_root: Path,
    tasks: list[dict[str, Any]],
    readme_example_cmds: list[str],
    datasets: list[str],
    mode: str,
    paper_metric_targets: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    for task in tasks:
        if not isinstance(task, dict):
            continue
        cmd = task.get("cmd")
        raw = " ".join(cmd) if isinstance(cmd, list) else ""
        task.setdefault("family", _command_family(raw))
        target = None
        if isinstance(cmd, list):
            target = _best_target_for_command(paper_metric_targets or [], cmd)
        if target and not task.get("expected_metrics"):
            task["expected_metrics"] = dict(target.get("metrics") or {})
            task["paper_targets"] = [target]
            task["claims"] = _target_claims(target)
            task["dataset"] = str(target.get("dataset") or task.get("dataset") or "")
            task["method"] = str(target.get("method") or task.get("method") or "")
            task["metric_artifact_path"] = f"metrics/{task.get('id')}_metrics.json"

    existing_ids = {str(t.get("id") or "") for t in tasks if isinstance(t, dict)}
    setup_tasks = _build_repo_prepare_tasks(repo_root, readme_example_cmds, mode)
    for task in setup_tasks:
        tid = str(task.get("id") or "")
        if tid and tid not in existing_ids:
            tasks.append(task)
            existing_ids.add(tid)

    target_tasks = _build_target_reproduction_tasks(
        readme_example_cmds=readme_example_cmds,
        paper_metric_targets=paper_metric_targets or [],
        mode=mode,
    )
    for task in target_tasks:
        tid = str(task.get("id") or "")
        if tid and tid not in existing_ids:
            tasks.append(task)
            existing_ids.add(tid)

    generic_tasks = _build_generic_readme_tasks(readme_example_cmds, mode=mode)
    for task in generic_tasks:
        tid = str(task.get("id") or "")
        if tid and tid not in existing_ids:
            tasks.append(task)
            existing_ids.add(tid)

    matrix_tasks = _build_readme_matrix_tasks(readme_example_cmds, datasets, mode=mode)
    if matrix_tasks:
        non_train = [
            t
            for t in tasks
            if isinstance(t, dict) and (not _is_training_task(t) or bool(t.get("expected_metrics")))
        ]
        tasks = non_train + matrix_tasks
    return _append_eval_export_tasks(repo_root, tasks, paper_metric_targets=paper_metric_targets)


def infer_tasks_heuristic(
    repo_root: str, mode: str = "smoke", paper_metric_targets: list[dict[str, Any]] | None = None
) -> InferResult:
    root = Path(repo_root)
    readme = _read_optional(root / "README.md")
    requirements_path = root / "requirements.txt"
    requirements_present = requirements_path.exists()
    entrypoints = _guess_entrypoints(root)
    examples = _extract_example_commands_from_readme(readme)
    datasets = _detect_benchmark_datasets(root)

    # Default install step. We keep it lightweight and let the framework's prepare/fix deal with stdlib-in-req.
    tasks: list[dict[str, Any]] = []
    if requirements_present:
        tasks.append(
            {
                "id": "install_deps",
                "family": "prepare",
                "cwd": "{paper_root}",
                "cmd": ["python", "-m", "pip", "install", "-r", "{paper_root}/requirements.txt"],
                "timeout_sec": 3600,
                "use_conda": True,
                "enabled": mode == "full",
            }
        )

    # Smoke: check --help for a chosen entrypoint.
    ep = entrypoints[0] if entrypoints else ""
    if not ep:
        # last resort: do nothing but print cwd (still validates the runner)
        tasks.append(
            {
                "id": "repo_smoke",
                "family": "smoke",
                "cwd": "{paper_root}",
                "cmd": ["python", "-c", "import os; print('cwd=', os.getcwd()); print('ok')"],
                "timeout_sec": 60,
                "use_conda": True,
            }
        )
    else:
        tasks.append(
            {
                "id": "repo_smoke",
                "family": "smoke",
                "cwd": "{paper_root}",
                "cmd": ["python", "-m", "py_compile", ep],
                "timeout_sec": 600,
                "use_conda": True,
            }
        )
        if (root / "eval.py").exists() and ep != "eval.py":
            tasks.append(
                {
                    "id": "eval_smoke",
                    "family": "smoke",
                    "cwd": "{paper_root}",
                    "cmd": ["python", "-m", "py_compile", "eval.py"],
                    "timeout_sec": 600,
                    "use_conda": True,
                }
            )

    # Full: propose heavier commands but disable them by default.
    if mode == "full":
        if examples:
            tasks.append(
                {
                    "id": "readme_example_1",
                    "family": _command_family(examples[0]),
                    "enabled": False,
                    "cwd": "{paper_root}",
                    "cmd": _command_to_argv(examples[0]),
                    "timeout_sec": 3600,
                    "use_conda": True,
                    "artifact_paths": ["results/**", "logs/**"],
                }
            )

    tasks = _finalize_tasks(
        repo_root=root,
        tasks=tasks,
        readme_example_cmds=examples,
        datasets=datasets,
        mode=mode,
        paper_metric_targets=paper_metric_targets,
    )
    evidence = {
        "mode": mode,
        "entrypoints": entrypoints,
        "datasets_detected": datasets,
        "readme_has_content": bool(readme.strip()),
        "requirements_present": requirements_present,
        "readme_example_cmds": examples,
        "paper_metric_targets_count": len(paper_metric_targets or []),
    }
    return InferResult(tasks=tasks, evidence=evidence)


def infer_tasks_llm(
    repo_root: str,
    mode: str,
    cfg_provider: str,
    cfg_model: str,
    cfg_base_url: str,
    paper_md_excerpt: str = "",
    paper_metric_targets: list[dict[str, Any]] | None = None,
) -> InferResult:
    """
    LLM-assisted task inference. Must be safe by design:
    - Prefer smoke tasks.
    - Heavy tasks must be generated with enabled=false unless explicitly requested by user.
    - Only wrapper commands (no source edits).
    """
    root = Path(repo_root)
    readme = _read_optional(root / "README.md", max_chars=14000)
    req = _read_optional(root / "requirements.txt", max_chars=8000)
    entrypoints = _guess_entrypoints(root)
    readme_example_cmds = _extract_example_commands_from_readme(readme)
    datasets = _detect_benchmark_datasets(root)
    entrypoint_hints = _entrypoint_arg_hints(root, entrypoints)

    # Keep prompt small but informative. The goal is to produce tasks that actually reflect the repo's README
    # (download/preprocess/train/eval) while staying safe by default.
    prompt = {
        "goal": "Generate tasks.yaml for running/evaluating this repo in a reproducible way.",
        "mode": mode,
        "platform": {
            "host_os": os.name,
            "execution_os": "linux",
            "execution_environment": "docker paper_image",
        },
        "repo_root": str(root),
        "files_top_level": [p.name for p in sorted(root.iterdir())][:200],
        "entrypoints_detected": entrypoints,
        "entrypoint_arg_hints": entrypoint_hints,
        "datasets_detected": datasets,
        "readme_example_commands": readme_example_cmds,
        "readme_md_excerpt": readme,
        "paper_mineru_md_excerpt": (paper_md_excerpt or ""),
        "paper_metric_targets": paper_metric_targets or [],
        "requirements_txt_excerpt": req,
        "schema": {
            "tasks": [
                {
                    "id": "string",
                    "family": "prepare|smoke|train|eval|reproduce",
                    "enabled": True,
                    "cwd": "{paper_root}",
                    "cmd": ["python", "run.py", "--help"],
                    "timeout_sec": 600,
                    "use_conda": True,
                    "artifact_paths": ["results/**"],
                    "metric_artifact_path": "metrics/task_id_metrics.json",
                    "dataset": "dataset name when known",
                    "method": "method/model/variant being evaluated",
                    "split": "train|validation|test when known",
                    "expected_metrics": {"accuracy": 0.0},
                    "paper_targets": [{"paper_table_id": "string", "metrics": {"accuracy": 0.0}}],
                    "claims": ["paper claim text this task evaluates"],
                }
            ],
            "notes": ["string"],
        },
        "constraints": [
            "Return JSON only, no prose outside JSON.",
            "You MUST derive commands from README when possible; do not output generic placeholder tasks if the README provides concrete steps.",
            "Prefer: install deps -> (optional) download/preprocess -> run/eval -> collect artifacts.",
            "Include at least one smoke task (help/print/version) as an early, fast validation step.",
            "If proposing any heavy task (downloads dataset, trains model), set enabled=false unless mode=='full'.",
            "Do not propose source code edits.",
            "Tasks execute inside a Linux Docker container even when the host machine is Windows. Do not emit Windows-only wrappers like ['cmd','/c', ...] unless the repo itself explicitly requires Windows shells.",
            "Commands must be compatible with shell=False: use argv arrays. For multi-step shell pipelines use ['bash','-lc','...'] because execution is Linux-based.",
            "When README lists multiple reproduction commands, preserve the full set of distinct commands instead of sampling only one or two representative examples.",
            "When datasets_detected contains multiple benchmark datasets and the training entrypoint supports a dataset flag, expand reproduction tasks across those datasets unless the README clearly restricts a command to one dataset.",
            "When a training command does not specify a run name but the CLI supports one, add a stable explicit run name so downstream checkpoints/logs can be located deterministically.",
            "If the repo contains a local evaluator/export script that can turn checkpoints into machine-readable metrics, add follow-up eval/export tasks for each training task.",
            "Every task that tests a paper claim must include family, dataset, method, expected_metrics, claims, paper_targets, and metric_artifact_path.",
            "For metric-export/eval tasks, write or collect JSON artifacts that include dataset, split, method/model/variant, and metric keys matching paper_metric_targets whenever possible.",
            "Attach expected_metrics and claims to eval/export tasks when paper_metric_targets identify the paper value being tested.",
            "If you emit a pip install task, prefer id='install_deps'. In paper-image Docker mode, avoid redundant runtime installs unless they are clearly necessary beyond image build.",
            "Use {paper_root} in cwd/cmd paths instead of hardcoding absolute paths.",
        ],
    }
    system = "You are a senior engineer generating a safe, reproducible tasks.yaml for a research repo."
    llm_cfg = resolve_llm_config(cfg_provider, cfg_model, cfg_base_url)
    resp = llm_json(prompt=json.dumps(prompt, ensure_ascii=False), system=system, cfg=llm_cfg)
    if not isinstance(resp, dict) or resp.get("status") == "error":
        # fallback to heuristics if LLM fails
        hr = infer_tasks_heuristic(repo_root, mode=mode, paper_metric_targets=paper_metric_targets)
        ev = dict(hr.evidence)
        ev["llm_error"] = resp
        return InferResult(tasks=hr.tasks, evidence=ev)

    tasks = resp.get("tasks")
    if not isinstance(tasks, list):
        hr = infer_tasks_heuristic(repo_root, mode=mode, paper_metric_targets=paper_metric_targets)
        ev = dict(hr.evidence)
        ev["llm_bad_shape"] = resp
        return InferResult(tasks=hr.tasks, evidence=ev)

    cleaned: list[dict[str, Any]] = []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        tid = t.get("id")
        cmd = t.get("cmd")
        if not isinstance(tid, str) or not isinstance(cmd, list) or not all(isinstance(x, str) for x in cmd):
            continue
        cleaned.append(t)

    finalized = cleaned or infer_tasks_heuristic(
        repo_root, mode=mode, paper_metric_targets=paper_metric_targets
    ).tasks
    finalized = _finalize_tasks(
        repo_root=root,
        tasks=finalized,
        readme_example_cmds=readme_example_cmds,
        datasets=datasets,
        mode=mode,
        paper_metric_targets=paper_metric_targets,
    )
    evidence = {
        "mode": mode,
        "llm_used": True,
        "llm_provider": llm_cfg.provider,
        "llm_model": llm_cfg.model,
        "raw": resp,
        "paper_metric_targets_count": len(paper_metric_targets or []),
    }
    return InferResult(tasks=finalized, evidence=evidence)

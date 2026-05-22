from __future__ import annotations

import ast
import builtins
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
        "working_main_system.py",
        "experiment.py",
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
            if any(
                x in parts
                for x in {
                    ".git",
                    ".idea",
                    "__MACOSX",
                    "__pycache__",
                    "site-packages",
                    "build",
                    "dist",
                    "transformers",
                    "simpletransformers",
                }
            ):
                continue
            if len(rel.parts) > 5:
                continue
            name = p.name.lower()
            if name in {
                "train.py",
                "eval.py",
                "evaluate.py",
                "test.py",
                "run.py",
                "main.py",
                "working_main_system.py",
                "experiment.py",
            } or any(tok in name for tok in ["train", "eval", "experiment", "pipeline", "inference"]):
                s = str(rel).replace("\\", "/")
                if s not in out:
                    out.append(s)
            if len(out) >= 12:
                break
    if not out:
        top_level_py = sorted(
            p
            for p in repo_root.glob("*.py")
            if p.is_file() and not p.name.startswith(".") and p.name != "__init__.py"
        )
        if len(top_level_py) == 1:
            out.append(top_level_py[0].name)
    return out


_IGNORED_SOURCE_DIR_NAMES = {
    ".git",
    ".idea",
    "__MACOSX",
    "__pycache__",
    "build",
    "checkpoints",
    "data",
    "datasets",
    "dist",
    "doc",
    "docs",
    "examples",
    "experiments",
    "figs",
    "logs",
    "notebook",
    "notebooks",
    "outputs",
    "results",
    "runs",
    "scripts",
    "site-packages",
    "simpletransformers",
    "tests",
    "transformers",
}


def _is_identifier_path_part(raw: str) -> bool:
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", raw or ""))


def _detect_python_import_targets(repo_root: Path) -> list[str]:
    """
    Detect importable local packages/modules for repos that ship as a library
    plus notebooks rather than a conventional train.py/eval.py entrypoint.
    """

    roots = [repo_root]
    src_dir = repo_root / "src"
    if src_dir.exists() and src_dir.is_dir():
        roots.append(src_dir)

    out: list[str] = []
    seen: set[str] = set()
    for base in roots:
        try:
            children = sorted(base.iterdir(), key=lambda p: p.name.lower())
        except Exception:
            continue
        for child in children:
            name = child.name
            if (
                not child.is_dir()
                or name.startswith(".")
                or name in _IGNORED_SOURCE_DIR_NAMES
                or not _is_identifier_path_part(name)
            ):
                continue
            direct_py = sorted(
                p for p in child.glob("*.py") if p.is_file() and not p.name.startswith(".")
            )
            if not direct_py:
                continue
            if (child / "__init__.py").exists():
                target = name
            else:
                modules = [
                    p.stem
                    for p in direct_py
                    if _is_identifier_path_part(p.stem) and p.stem not in {"setup", "conftest"}
                ]
                if not modules:
                    continue
                preferred = next(
                    (m for m in ["core", "model", "models", "utils", "main"] if m in modules),
                    modules[0],
                )
                target = f"{name}.{preferred}"
            if target not in seen:
                out.append(target)
                seen.add(target)
            if len(out) >= 8:
                return out

    for p in sorted(repo_root.glob("*.py"), key=lambda item: item.name.lower()):
        if not p.is_file() or p.name.startswith("."):
            continue
        stem = p.stem
        if stem in {"setup", "conftest"} or not _is_identifier_path_part(stem):
            continue
        if stem not in seen:
            out.append(stem)
            seen.add(stem)
        if len(out) >= 8:
            break
    return out


def _namespace_package_roots(repo_root: Path) -> set[str]:
    roots = [repo_root]
    src_dir = repo_root / "src"
    if src_dir.exists() and src_dir.is_dir():
        roots.append(src_dir)
    out: set[str] = set()
    for base in roots:
        try:
            children = list(base.iterdir())
        except Exception:
            continue
        for child in children:
            name = child.name
            if (
                child.is_dir()
                and not name.startswith(".")
                and name not in _IGNORED_SOURCE_DIR_NAMES
                and _is_identifier_path_part(name)
                and not (child / "__init__.py").exists()
                and any(p.is_file() for p in child.glob("*.py"))
            ):
                out.add(name)
    return out


def _import_smoke_cmd(import_target: str) -> list[str]:
    script = (
        "import importlib; "
        f"importlib.import_module({import_target!r}); "
        f"print('import ok: {import_target}')"
    )
    return ["python", "-c", script]


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


def _first_command_token(s: str) -> str:
    parts = (s or "").split()
    while parts and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", parts[0]):
        parts.pop(0)
    return parts[0] if parts else ""


def _looks_like_shell_command(s: str) -> bool:
    if not s:
        return False
    if re.match(r"^\./[^\s]+/$", s.strip()):
        return False
    first_raw = _first_command_token(s)
    if not first_raw:
        return False
    first = first_raw.lower()
    if first == "make" and first_raw != "make":
        return False
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
    if first.endswith(".sh"):
        return True
    if first.startswith("./"):
        basename = Path(first).name.lower()
        return basename in {"run", "train", "eval", "evaluate", "test", "main"} or basename.endswith(
            (".sh", ".py")
        )
    return False


def _extract_example_commands_from_readme(readme_text: str) -> list[str]:
    """
    Extract likely shell commands from README code fences and prompt-like lines.
    """
    txt = readme_text or ""
    cmds: list[str] = []
    for m in re.finditer(r"```(?:bash|sh|shell|console|text|python)?\s*([\s\S]*?)```", txt, flags=re.IGNORECASE):
        block = (m.group(1) or "").strip()
        raw_lines = [line for line in block.splitlines() if not line.strip().startswith("#")]
        for s in _join_continuations(raw_lines):
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


def _choose_readme_placeholder_options(raw: str) -> str:
    def repl(match: re.Match[str]) -> str:
        options = [part.strip() for part in match.group(1).split("/") if part.strip()]
        return options[0] if options else match.group(0)

    return re.sub(r"\[([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)\]", repl, raw or "")


def _command_to_argv(raw: str) -> list[str]:
    s = _choose_readme_placeholder_options(raw).strip()
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
    leading_env_assignment = bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", parts[0]))
    shell_features = any(token in s for token in ["&&", "||", "|", ";", "$", "`", ">", "<"])
    if shell_features or parts[0] in {"cd", "export"}:
        return ["bash", "-lc", s]
    if leading_env_assignment:
        return ["bash", "-lc", s]
    return parts


_README_PLACEHOLDER_WORDS = {
    "ADAPTER",
    "ARG",
    "ARGS",
    "CHECKPOINT",
    "CKPT",
    "CONFIG",
    "CONFIG_FILE",
    "DATA",
    "DATA_DIR",
    "DATA_PATH",
    "DATASET",
    "DEVICE",
    "ENV",
    "EXPERIMENT",
    "FILE",
    "GPU",
    "METHOD",
    "MODEL",
    "MODEL_NAME",
    "NAME",
    "OUTPUT",
    "OUTPUT_DIR",
    "PATH",
    "SEED",
    "SPLIT",
    "TASK",
}


def _has_unresolved_readme_placeholder(raw: str, cmd: list[str]) -> bool:
    s = _choose_readme_placeholder_options(raw or "")
    if re.search(r"\[[A-Za-z0-9_.-]+\]", s):
        return True
    if re.search(r"<[A-Za-z0-9_. -]+>", s):
        return True
    if re.search(r"\$[A-Za-z_][A-Za-z0-9_]*", s):
        return True
    tokens = list(cmd)
    if len(tokens) >= 3 and tokens[0] in {"bash", "sh"} and tokens[1] == "-lc":
        try:
            tokens = shlex.split(tokens[2], posix=True)
        except Exception:
            tokens = tokens[2].split()
    for tok in tokens:
        stripped = str(tok).strip().strip("[]{}<>")
        if not stripped or stripped.startswith("$") or stripped.startswith("-"):
            continue
        upper = stripped.upper()
        if upper.startswith("YOUR_") or upper in _README_PLACEHOLDER_WORDS:
            return True
    return False


def _infer_cwd_for_command(repo_root: Path, cmd: list[str]) -> str:
    if len(cmd) < 2:
        return "{paper_root}"
    executable = cmd[0].lower()
    script = ""
    if (executable in {"python", "python3"} and cmd[1].endswith(".py")) or (
        executable in {"bash", "sh"} and cmd[1].endswith(".sh")
    ):
        script = cmd[1]
    if not script or "/" in script or "\\" in script:
        return "{paper_root}"
    if (repo_root / script).exists():
        return "{paper_root}"
    try:
        matches = [
            p
            for p in repo_root.rglob(script)
            if p.is_file() and ".git" not in p.parts and "deployment" not in p.parts
        ]
    except Exception:
        matches = []
    if len(matches) != 1:
        return "{paper_root}"
    rel_parent = matches[0].parent.relative_to(repo_root).as_posix()
    return "{paper_root}" if rel_parent == "." else f"{{paper_root}}/{rel_parent}"


def _script_path_for_command(repo_root: Path, cmd: list[str]) -> Path | None:
    if len(cmd) < 2:
        return None
    executable = str(cmd[0] or "").strip().lower()
    if executable not in {"python", "python3", "bash", "sh"}:
        return None
    script = str(cmd[1] or "").strip()
    if executable in {"python", "python3"} and not script.endswith(".py"):
        return None
    if executable in {"bash", "sh"} and not script.endswith(".sh"):
        return None
    direct = (repo_root / script).resolve()
    if direct.exists() and direct.is_file():
        return direct
    if "/" in script or "\\" in script:
        return None
    try:
        matches = [
            p
            for p in repo_root.rglob(script)
            if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts
        ]
    except Exception:
        return None
    return matches[0] if len(matches) == 1 else None


def _path_tokens(path: str) -> set[str]:
    return {
        tok
        for tok in re.split(r"[^a-z0-9]+", Path(path).stem.lower())
        if len(tok) >= 2
    }


def _repair_missing_script_command(repo_root: Path, cmd: list[str]) -> list[str]:
    if len(cmd) < 2:
        return cmd
    executable = str(cmd[0] or "").strip().lower()
    if executable not in {"python", "python3", "bash", "sh"}:
        return cmd
    script = str(cmd[1] or "").strip()
    suffix = ".py" if executable in {"python", "python3"} else ".sh"
    if not script.endswith(suffix):
        return cmd
    if (repo_root / script).exists():
        return cmd
    rel = Path(script)
    search_dir = repo_root / rel.parent
    try:
        candidates = sorted(p for p in search_dir.glob(f"*{suffix}") if p.is_file())
    except Exception:
        candidates = []
    if not candidates:
        return cmd
    replacement: Path | None = None
    if len(candidates) == 1:
        replacement = candidates[0]
    else:
        wanted_tokens = _path_tokens(script)
        scored = [
            (len(wanted_tokens & _path_tokens(candidate.name)), candidate)
            for candidate in candidates
        ]
        scored = [(score, candidate) for score, candidate in scored if score > 0]
        scored.sort(key=lambda item: item[0], reverse=True)
        if scored and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            replacement = scored[0][1]
    if replacement is None:
        return cmd
    repaired = list(cmd)
    repaired[1] = replacement.relative_to(repo_root).as_posix()
    return repaired


def _resolve_local_python_module(
    repo_root: Path, script_path: Path, module: str, *, level: int = 0
) -> list[Path]:
    parts = [p for p in (module or "").split(".") if p]
    bases: list[Path] = []
    if level > 0:
        base = script_path.parent
        for _ in range(max(level - 1, 0)):
            base = base.parent
        bases.append(base)
    else:
        bases.extend([script_path.parent, repo_root])

    out: list[Path] = []
    for base in bases:
        target = base.joinpath(*parts) if parts else base
        candidates = [target.with_suffix(".py"), target / "__init__.py"] if parts else []
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
                if resolved.exists() and resolved.is_file() and repo_root.resolve() in resolved.parents:
                    out.append(resolved)
            except Exception:
                continue
    return out


def _local_python_imports(repo_root: Path, script_path: Path, source: str) -> list[Path]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    imports: list[Path] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.extend(_resolve_local_python_module(repo_root, script_path, alias.name.split(".", 1)[0]))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.extend(_resolve_local_python_module(repo_root, script_path, module, level=node.level))
            if node.level and not module:
                for alias in node.names:
                    imports.extend(_resolve_local_python_module(repo_root, script_path, alias.name, level=node.level))
    return imports


def _resolve_script_reference(repo_root: Path, base_dir: Path, ref: str) -> Path | None:
    raw = str(ref or "").strip().strip("'\"")
    if not raw or raw.startswith("-") or raw.startswith("$"):
        return None
    rel = Path(raw)
    candidates = [base_dir / rel, repo_root / rel]
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            if resolved.exists() and resolved.is_file() and repo_root.resolve() in resolved.parents:
                return resolved
        except Exception:
            continue
    return None


def _python_scripts_referenced_by_shell(repo_root: Path, base_dir: Path, source: str) -> list[Path]:
    normalized = re.sub(r"\\\r?\n", " ", source or "")
    scripts: list[Path] = []
    seen: set[Path] = set()
    for raw_line in normalized.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            tokens = shlex.split(line, posix=True)
        except Exception:
            tokens = line.split()
        for idx, token in enumerate(tokens):
            exe = Path(str(token)).name.lower()
            if exe not in {"python", "python3"}:
                continue
            for candidate_token in tokens[idx + 1 :]:
                if candidate_token == "-m":
                    break
                if str(candidate_token).startswith("-"):
                    continue
                if not str(candidate_token).endswith(".py"):
                    break
                path = _resolve_script_reference(repo_root, base_dir, str(candidate_token))
                if path is not None and path not in seen:
                    scripts.append(path)
                    seen.add(path)
                break
        for match in re.finditer(r"(?:^|[\s($;])python3?\s+([A-Za-z0-9_./-]+\.py)\b", line):
            path = _resolve_script_reference(repo_root, base_dir, match.group(1))
            if path is not None and path not in seen:
                scripts.append(path)
                seen.add(path)
    return scripts


def _api_scan_text_for_command(repo_root: Path, raw_cmd: str, script_path: Path | None) -> str:
    haystacks = [str(raw_cmd or "")]
    if script_path is None or script_path.suffix.lower() not in {".py", ".sh"}:
        if script_path is not None:
            haystacks.append(_read_optional(script_path, max_chars=30000))
        return "\n".join(haystacks)

    if script_path.suffix.lower() == ".sh":
        shell_source = _read_optional(script_path, max_chars=60000)
        haystacks.append(shell_source)
        queue = _python_scripts_referenced_by_shell(repo_root, script_path.parent, shell_source)
    else:
        queue = [script_path]
    seen: set[Path] = set()
    while queue and len(seen) < 64:
        path = queue.pop(0)
        try:
            resolved = path.resolve()
        except Exception:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        source = _read_optional(resolved, max_chars=60000)
        haystacks.append(source)
        queue.extend(p for p in _local_python_imports(repo_root, resolved, source) if p not in seen)
    return "\n".join(haystacks)


_EXTERNAL_API_MARKERS = [
    "anthropic",
    "chat.completions",
    "claude",
    "client.chat",
    "dashscope",
    "deepseek",
    "from openai import",
    "gemini",
    "gpt-3.5",
    "gpt-4",
    "gpt-4o",
    "gpt4",
    "google-generativeai",
    "import openai",
    "asyncopenai",
    "llm api",
    "model server",
    "openai(",
    "google-genai",
    "google.genai",
    "genai.client",
    "api_key",
    "api key",
    "apikey",
    "base_url",
    "localhost:8000/v1",
    "127.0.0.1:8000/v1",
    "vllm",
]


def _text_requires_external_api(text: str) -> bool:
    lowered = (text or "").lower()
    if not lowered.strip():
        return False
    if any(marker in lowered for marker in _EXTERNAL_API_MARKERS):
        return True
    return bool(re.search(r"\b(?:openai|anthropic|gemini|claude)_api_key\b", lowered))


def _path_requires_external_api(path: Path) -> bool:
    return _text_requires_external_api(_read_optional(path, max_chars=120000))


def _command_requires_external_api(repo_root: Path, raw_cmd: str, cmd: list[str]) -> bool:
    """
    Conservative guard for README examples that require paid APIs or a local
    model server. These commands are still recorded, but auto-full should skip
    them so later metric/plotting tasks can run.
    """

    script_path = _script_path_for_command(repo_root, cmd)
    text = _api_scan_text_for_command(repo_root, raw_cmd, script_path)
    return _text_requires_external_api(text)


def _external_api_tasks_disabled() -> bool:
    token = (
        os.environ.get("EXECUTION_DISABLE_EXTERNAL_API_TASKS")
        or os.environ.get("FACTREVIEW_DISABLE_EXTERNAL_API_TASKS")
        or ""
    ).strip().lower()
    return token in {"1", "true", "yes", "on"}


def _mark_external_api_task(task: dict[str, Any], requires_api: bool) -> dict[str, Any]:
    if requires_api:
        task["requires_external_api"] = True
        if _external_api_tasks_disabled():
            task["enabled"] = False
            task["disabled_reason"] = "external_api_or_model_server_required"
    return task


def _set_disabled_reason(task: dict[str, Any], reason: str) -> None:
    if reason and not str(task.get("disabled_reason") or "").strip():
        task["disabled_reason"] = reason


def _task_requires_external_api(repo_root: Path, task: dict[str, Any]) -> bool:
    cmd = task.get("cmd")
    cmd_list = cmd if isinstance(cmd, list) and all(isinstance(x, str) for x in cmd) else []
    raw_parts = [
        str(task.get("id") or ""),
        str(task.get("family") or ""),
        str(task.get("method") or ""),
        str(task.get("model") or ""),
        str(task.get("variant") or ""),
        " ".join(cmd_list),
    ]
    claims = task.get("claims")
    if isinstance(claims, list):
        raw_parts.extend(str(item) for item in claims[:8])
    requires = _command_requires_external_api(repo_root, "\n".join(raw_parts), cmd_list) if cmd_list else False
    if not requires and _cmd_executes_notebook(cmd_list):
        for token in cmd_list:
            if str(token).lower().endswith(".ipynb"):
                requires = _path_requires_external_api(repo_root / str(token))
                break
    return bool(task.get("requires_external_api")) or requires


def _apply_external_api_policy(tasks: list[dict[str, Any]], repo_root: Path) -> list[dict[str, Any]]:
    for task in tasks:
        if isinstance(task, dict):
            _mark_external_api_task(task, _task_requires_external_api(repo_root, task))
    return tasks


def _is_environment_management_command(raw: str) -> bool:
    s = re.sub(r"\s+", " ", (raw or "").strip().lower())
    if not s:
        return False
    return bool(
        re.match(r"^(?:conda|mamba|micromamba)\s+(?:activate|deactivate|create|install)\b", s)
        or re.match(r"^(?:conda|mamba|micromamba)\s+env\s+(?:create|update|remove)\b", s)
        or re.match(r"^(?:source|\.)\s+(?:activate|deactivate)\b", s)
    )


def _command_family(raw: str) -> str:
    s = (raw or "").lower()
    if _is_environment_management_command(raw):
        return "prepare"
    if any(tok in s for tok in ["pip install", "conda env", "mamba env", "environment.yml", "requirements.txt"]):
        return "prepare"
    if any(tok in s for tok in ["preprocess", "prepare", "download", "convert"]):
        return "prepare"
    if "--help" in s or "-h" in s:
        return "smoke"
    if (
        any(tok in s for tok in ["--do_train", "train_batch_size", "finetune", "fine-tune", "fit"])
        or re.search(r"\btrain(?:\.py|_|\b)", s)
        or re.search(r"\bmain\.py\b", s)
    ):
        return "train"
    if any(tok in s for tok in ["eval", "evaluate", "test.py", "predict", "inference"]):
        return "eval"
    if re.search(r"\brun\.py\b", s):
        return "train"
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
    repo_root: Path,
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
        cmd = _repair_missing_script_command(repo_root, _command_to_argv(raw_cmd))
        has_placeholder = _has_unresolved_readme_placeholder(raw_cmd, cmd)
        ident = _target_identity(target)
        family = _command_family(raw_cmd)
        task_id = f"{family}_{ident}"
        if task_id in seen:
            task_id = f"{task_id}_{idx}"
        seen.add(task_id)
        tasks.append(
            _mark_external_api_task(
                {
                "id": task_id,
                "family": family,
                "enabled": mode == "full" and not has_placeholder,
                "cwd": _infer_cwd_for_command(repo_root, cmd),
                "cmd": cmd,
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
                },
                _command_requires_external_api(repo_root, raw_cmd, cmd),
            )
        )
        if has_placeholder:
            tasks[-1]["disabled_reason"] = "readme_placeholder_command"
    return tasks


def _build_generic_readme_tasks(repo_root: Path, readme_example_cmds: list[str], mode: str) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    seen: set[str] = set()
    seen_cmds: set[str] = set()
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
        cmd = _repair_missing_script_command(repo_root, _command_to_argv(raw))
        cmd_key = " ".join(cmd)
        if cmd_key in seen_cmds:
            continue
        seen_cmds.add(cmd_key)
        has_placeholder = _has_unresolved_readme_placeholder(raw, cmd)
        tasks.append(
            _mark_external_api_task(
                {
                "id": task_id,
                "family": family,
                "enabled": mode == "full" and not has_placeholder,
                "cwd": _infer_cwd_for_command(repo_root, cmd),
                "cmd": cmd,
                "timeout_sec": 86400 if family == "train" else 7200,
                "use_conda": True,
                "artifact_paths": ["metrics/**", "results/**", "outputs/**", "logs/**", "checkpoints/**"],
                },
                _command_requires_external_api(repo_root, raw, cmd),
            )
        )
        if has_placeholder:
            tasks[-1]["disabled_reason"] = "readme_placeholder_command"
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
            "conda create",
            "conda install",
            "conda env",
            "mamba create",
            "mamba install",
            "mamba env",
            "micromamba create",
            "micromamba install",
            "micromamba env",
            "environment.yml",
            "requirements.txt",
            "setup.py install",
        ]
    )


def _cmd_text(cmd: list[str]) -> str:
    return " ".join(str(x) for x in cmd if str(x).strip())


def _cmd_has_help_flag(cmd: list[str]) -> bool:
    return any(str(x).strip().lower() in {"-h", "--help"} for x in cmd)


def _cmd_is_pip_install(cmd: list[str]) -> bool:
    if not cmd:
        return False
    lowered = [str(x).strip().lower() for x in cmd]
    if len(lowered) >= 4 and lowered[0] in {"python", "python3"} and lowered[1:4] == ["-m", "pip", "install"]:
        return True
    if len(lowered) >= 2 and lowered[0] in {"pip", "pip3"} and lowered[1] == "install":
        return True
    if len(lowered) >= 3 and lowered[0] in {"bash", "sh"} and lowered[1] in {"-c", "-lc"}:
        return bool(re.search(r"\b(?:python(?:3)?\s+-m\s+)?pip(?:3)?\s+install\b", lowered[2]))
    return False


def _cmd_executes_notebook(cmd: list[str]) -> bool:
    if not cmd:
        return False
    lowered = [str(x).strip().lower() for x in cmd]
    text = " ".join(lowered)
    if ".ipynb" not in text:
        return False
    if "nbconvert" in lowered and "--execute" in lowered:
        return True
    if re.search(r"\bjupyter\s+(?:notebook|lab)\b", text):
        return True
    if len(lowered) >= 3 and lowered[0] in {"bash", "sh"} and lowered[1] in {"-c", "-lc"}:
        shell = lowered[2]
        return ".ipynb" in shell and (
            bool(re.search(r"\b(?:jupyter\s+)?nbconvert\b", shell) and "--execute" in shell)
            or bool(re.search(r"\bjupyter\s+(?:notebook|lab)\b", shell))
        )
    return False


def _cmd_imports_from_namespace_root(cmd: list[str], package_names: set[str]) -> bool:
    if not package_names:
        return False
    text = _cmd_text(cmd)
    if len(cmd) >= 3 and (
        (cmd[0] in {"python", "python3"} and cmd[1] == "-c")
        or (cmd[0] in {"bash", "sh"} and cmd[1] in {"-c", "-lc"})
    ):
        text = cmd[2]
    return any(re.search(rf"\bfrom\s+{re.escape(package)}\s+import\b", text) for package in package_names)


def _module_path_for_import(repo_root: Path, module: str) -> Path | None:
    parts = [p for p in (module or "").split(".") if p and _is_identifier_path_part(p)]
    if not parts:
        return None
    candidates = [
        repo_root.joinpath(*parts).with_suffix(".py"),
        repo_root.joinpath(*parts) / "__init__.py",
    ]
    src_root = repo_root / "src"
    if src_root.exists():
        candidates.extend(
            [
                src_root.joinpath(*parts).with_suffix(".py"),
                src_root.joinpath(*parts) / "__init__.py",
            ]
        )
    for candidate in candidates:
        try:
            if candidate.exists() and candidate.is_file() and repo_root.resolve() in candidate.resolve().parents:
                return candidate
        except Exception:
            continue
    return None


def _extract_imported_modules_from_cmd(cmd: list[str]) -> list[str]:
    if len(cmd) >= 3 and (
        (cmd[0] in {"python", "python3"} and cmd[1] == "-c")
        or (cmd[0] in {"bash", "sh"} and cmd[1] in {"-c", "-lc"})
    ):
        text = cmd[2]
    else:
        return []
    modules: list[str] = []
    patterns = [
        r"import_module\(\s*['\"]([A-Za-z_][A-Za-z0-9_.]*)['\"]\s*\)",
        r"\bimport\s+([A-Za-z_][A-Za-z0-9_.]*)",
        r"\bfrom\s+([A-Za-z_][A-Za-z0-9_.]*)\s+import\b",
    ]
    seen: set[str] = set()
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            module = match.group(1).strip()
            if module and module not in seen:
                modules.append(module)
                seen.add(module)
    return modules


def _names_in_ast(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def _target_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        out: set[str] = set()
        for elt in node.elts:
            out.update(_target_names(elt))
        return out
    return set()


def _top_level_unresolved_names(path: Path) -> list[str]:
    source = _read_optional(path, max_chars=200000)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    defined = set(dir(builtins)) | {"__file__", "__name__", "__package__", "__doc__"}
    unresolved: set[str] = set()

    def note_missing(names: set[str]) -> None:
        unresolved.update(name for name in names if name not in defined)

    for stmt in tree.body:
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                defined.add(alias.asname or alias.name.split(".", 1)[0])
        elif isinstance(stmt, ast.ImportFrom):
            for alias in stmt.names:
                if alias.name == "*":
                    continue
                defined.add(alias.asname or alias.name)
        elif isinstance(stmt, ast.ClassDef):
            for item in [*stmt.decorator_list, *stmt.bases, *[kw.value for kw in stmt.keywords]]:
                note_missing(_names_in_ast(item))
            defined.add(stmt.name)
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for item in [*stmt.decorator_list, *stmt.args.defaults, *stmt.args.kw_defaults]:
                if item is not None:
                    note_missing(_names_in_ast(item))
            if stmt.returns is not None:
                note_missing(_names_in_ast(stmt.returns))
            defined.add(stmt.name)
        elif isinstance(stmt, ast.Assign):
            note_missing(_names_in_ast(stmt.value))
            for target in stmt.targets:
                defined.update(_target_names(target))
        elif isinstance(stmt, ast.AnnAssign):
            note_missing(_names_in_ast(stmt.annotation))
            if stmt.value is not None:
                note_missing(_names_in_ast(stmt.value))
            defined.update(_target_names(stmt.target))
        elif isinstance(stmt, ast.Expr):
            note_missing(_names_in_ast(stmt.value))
    return sorted(unresolved)


def _smoke_disabled_reason(task: dict[str, Any]) -> str:
    cmd = task.get("cmd")
    cmd_list = cmd if isinstance(cmd, list) and all(isinstance(x, str) for x in cmd) else []
    family = str(task.get("family") or _command_family(_cmd_text(cmd_list))).strip().lower()
    if _cmd_is_pip_install(cmd_list) or family == "prepare":
        return "smoke_mode_prepare_disabled"
    if _cmd_executes_notebook(cmd_list):
        return "full_mode_required"
    if family in {"train", "eval", "evaluate", "evaluation", "reproduce", "reproduction", "benchmark"}:
        if not _cmd_has_help_flag(cmd_list):
            return "full_mode_required"
    return ""


def _apply_mode_policy(tasks: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    if str(mode or "smoke").strip().lower() == "full":
        return tasks
    for task in tasks:
        if not isinstance(task, dict):
            continue
        reason = _smoke_disabled_reason(task)
        if not reason:
            continue
        task["enabled"] = False
        _set_disabled_reason(task, reason)
    return tasks


def _apply_static_import_policy(tasks: list[dict[str, Any]], repo_root: Path) -> list[dict[str, Any]]:
    namespace_roots = _namespace_package_roots(repo_root)
    for task in tasks:
        if not isinstance(task, dict) or not bool(task.get("enabled", True)):
            continue
        cmd = task.get("cmd")
        cmd_list = cmd if isinstance(cmd, list) and all(isinstance(x, str) for x in cmd) else []
        if namespace_roots and _cmd_imports_from_namespace_root(cmd_list, namespace_roots):
            task["enabled"] = False
            _set_disabled_reason(task, "namespace_package_root_import_unavailable")
            continue
        for module in _extract_imported_modules_from_cmd(cmd_list):
            path = _module_path_for_import(repo_root, module)
            if path is None:
                continue
            missing = _top_level_unresolved_names(path)
            if missing:
                task["enabled"] = False
                _set_disabled_reason(task, "module_import_has_unresolved_top_level_names")
                task["static_import_issues"] = {"module": module, "missing_names": missing[:12]}
                break
    return tasks


def _build_repo_prepare_tasks(repo_root: Path, readme_example_cmds: list[str], mode: str) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    seen_cmds: set[str] = set()

    def add_prepare(raw_cmd: str, raw_id: str) -> None:
        cmd_text = re.sub(r"\s+", " ", (raw_cmd or "").strip())
        if not cmd_text or cmd_text in seen_cmds:
            return
        seen_cmds.add(cmd_text)
        cmd = _repair_missing_script_command(repo_root, _command_to_argv(cmd_text))
        tasks.append(
            {
                "id": f"prepare_{_safe_id_part(raw_id)}",
                "family": "prepare",
                "enabled": mode == "full",
                "cwd": "{paper_root}",
                "cmd": cmd,
                "timeout_sec": 7200,
                "use_conda": True,
                "artifact_paths": ["data/**", "datasets/**", "processed/**", "preprocessed/**"],
            }
        )

    for idx, raw in enumerate(readme_example_cmds, 1):
        if (
            _command_family(raw) != "prepare"
            or _is_dependency_install_command(raw)
            or _is_environment_management_command(raw)
        ):
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


def _notebook_candidates(repo_root: Path) -> list[Path]:
    ignored_parts = {
        ".git",
        ".idea",
        "__MACOSX",
        "__pycache__",
        ".ipynb_checkpoints",
        "site-packages",
        "simpletransformers",
        "build",
        "dist",
        "transformers",
        "runs",
        "outputs",
    }
    out: list[Path] = []
    try:
        paths = sorted(repo_root.rglob("*.ipynb"), key=lambda p: p.relative_to(repo_root).as_posix().lower())
    except Exception:
        return []
    for path in paths:
        rel = path.relative_to(repo_root)
        if any(part in ignored_parts or part.startswith(".") for part in rel.parts):
            continue
        out.append(path)
    return out


def _resolve_notebook_reference(repo_root: Path, ref: str, candidates: list[Path]) -> Path | None:
    cleaned = (ref or "").strip().strip("`'\"()[]{}.,:;")
    if not cleaned or not cleaned.lower().endswith(".ipynb"):
        return None
    cleaned = cleaned.replace("\\", "/")
    direct = repo_root / cleaned
    if direct.exists() and direct.is_file():
        return direct
    basename = Path(cleaned).name.lower()
    matches = [p for p in candidates if p.name.lower() == basename]
    if len(matches) == 1:
        return matches[0]
    suffix_matches = [
        p for p in candidates if p.relative_to(repo_root).as_posix().lower().endswith(cleaned.lower())
    ]
    return suffix_matches[0] if len(suffix_matches) == 1 else None


def _extract_notebook_paths(repo_root: Path, readme_text: str, limit: int = 8) -> list[str]:
    candidates = _notebook_candidates(repo_root)
    if not candidates:
        return []

    seen: set[str] = set()
    out: list[str] = []

    def add(path: Path | None) -> None:
        if path is None:
            return
        rel = path.relative_to(repo_root).as_posix()
        if rel in seen:
            return
        seen.add(rel)
        out.append(rel)

    for match in re.finditer(r"(?P<path>[\w./ -]+\.ipynb)", readme_text or "", flags=re.IGNORECASE):
        add(_resolve_notebook_reference(repo_root, match.group("path"), candidates))
        if len(out) >= limit:
            return out

    # If README names an experiments/notebooks folder but omits exact paths,
    # preserve a few notebooks from that folder as disabled/full-mode candidates.
    text = (readme_text or "").lower()
    folder_hints = []
    if "experiments/" in text or "experiments folder" in text:
        folder_hints.append("experiments/")
    if "notebooks/" in text or "notebooks folder" in text:
        folder_hints.append("notebooks/")
    for path in candidates:
        rel = path.relative_to(repo_root).as_posix()
        if folder_hints and not any(rel.lower().startswith(hint) for hint in folder_hints):
            continue
        add(path)
        if len(out) >= limit:
            break

    if not out and len(candidates) <= 3:
        for path in candidates:
            add(path)
            if len(out) >= limit:
                break
    return out


def _build_notebook_tasks(repo_root: Path, notebook_paths: list[str], mode: str) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for rel in notebook_paths[:8]:
        task_id = f"reproduce_notebook_{_safe_id_part(rel)}"
        output_dir = f"{{run_dir}}/outputs/{task_id}"
        timeout = 7200
        task = {
            "id": task_id,
            "family": "reproduce",
            "enabled": mode == "full",
            "cwd": "{paper_root}",
            "cmd": [
                "python",
                "-m",
                "jupyter",
                "nbconvert",
                "--to",
                "notebook",
                "--execute",
                rel,
                "--output-dir",
                output_dir,
                f"--ExecutePreprocessor.timeout={timeout}",
            ],
            "timeout_sec": timeout + 600,
            "use_conda": True,
            "artifact_paths": [
                f"{output_dir}/*.ipynb",
                f"{output_dir}/*.html",
                "metrics/**",
                "results/**",
                "outputs/**",
                "figs/**",
                "figures/**",
            ],
            "metric_artifact_path": f"metrics/{task_id}_metrics.json",
            "dataset": "",
            "method": "",
            "split": "",
        }
        if mode != "full":
            task["disabled_reason"] = "full_mode_required"
        nb_path = repo_root / rel
        task = _mark_external_api_task(task, _path_requires_external_api(nb_path))
        tasks.append(task)
    return tasks


def _script_text(repo_root: Path, rel_path: str, max_chars: int = 12000) -> str:
    script = repo_root / rel_path
    if not script.exists() or not script.is_file():
        return ""
    return _read_optional(script, max_chars=max_chars)


def _script_has_main_guard(repo_root: Path, rel_path: str) -> bool:
    return "__name__" in _script_text(repo_root, rel_path, max_chars=80000)


def _script_requires_cli_args(repo_root: Path, rel_path: str) -> bool:
    text = _script_text(repo_root, rel_path, max_chars=30000)
    return "add_argument" in text and bool(re.search(r"add_argument\([\s\S]{0,300}?required\s*=\s*True", text))


def _entrypoint_family(rel_path: str) -> str:
    name = Path(rel_path).name.lower()
    if any(tok in name for tok in ["eval", "evaluate", "predict", "inference"]):
        return "eval"
    if any(tok in name for tok in ["train", "main", "experiment", "pipeline"]):
        return "train"
    return "reproduce"


def _build_entrypoint_run_tasks(
    repo_root: Path, entrypoints: list[str], readme_example_cmds: list[str], mode: str
) -> list[dict[str, Any]]:
    if readme_example_cmds:
        return []
    tasks: list[dict[str, Any]] = []
    for rel in entrypoints[:6]:
        if not _script_has_main_guard(repo_root, rel):
            continue
        family = _entrypoint_family(rel)
        task = {
            "id": f"{family}_entrypoint_{_safe_id_part(rel)}",
            "family": family,
            "enabled": mode == "full",
            "cwd": "{paper_root}",
            "cmd": ["python", rel],
            "timeout_sec": 86400 if family == "train" else 7200,
            "use_conda": True,
            "artifact_paths": ["metrics/**", "results/**", "outputs/**", "logs/**", "checkpoints/**", "models/**"],
        }
        if _script_requires_cli_args(repo_root, rel):
            task["enabled"] = False
            task["disabled_reason"] = "required_cli_arguments_missing"
        tasks.append(_mark_external_api_task(task, _command_requires_external_api(repo_root, "python " + rel, task["cmd"])))
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
    entrypoints: list[str],
    readme_example_cmds: list[str],
    notebook_paths: list[str],
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
        repo_root=repo_root,
        readme_example_cmds=readme_example_cmds,
        paper_metric_targets=paper_metric_targets or [],
        mode=mode,
    )
    for task in target_tasks:
        tid = str(task.get("id") or "")
        if tid and tid not in existing_ids:
            tasks.append(task)
            existing_ids.add(tid)

    generic_tasks = _build_generic_readme_tasks(repo_root, readme_example_cmds, mode=mode)
    for task in generic_tasks:
        tid = str(task.get("id") or "")
        if tid and tid not in existing_ids:
            tasks.append(task)
            existing_ids.add(tid)

    entrypoint_tasks = _build_entrypoint_run_tasks(repo_root, entrypoints, readme_example_cmds, mode=mode)
    for task in entrypoint_tasks:
        tid = str(task.get("id") or "")
        if tid and tid not in existing_ids:
            tasks.append(task)
            existing_ids.add(tid)

    notebook_tasks = _build_notebook_tasks(repo_root, notebook_paths, mode=mode)
    for task in notebook_tasks:
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
    tasks = _append_eval_export_tasks(repo_root, tasks, paper_metric_targets=paper_metric_targets)
    tasks = _apply_external_api_policy(tasks, repo_root)
    tasks = _apply_static_import_policy(tasks, repo_root)
    return _apply_mode_policy(tasks, mode)


def infer_tasks_heuristic(
    repo_root: str, mode: str = "smoke", paper_metric_targets: list[dict[str, Any]] | None = None
) -> InferResult:
    root = Path(repo_root)
    readme = _read_optional(root / "README.md")
    requirements_path = root / "requirements.txt"
    requirements_present = requirements_path.exists()
    entrypoints = _guess_entrypoints(root)
    examples = _extract_example_commands_from_readme(readme)
    import_targets = _detect_python_import_targets(root)
    notebook_paths = _extract_notebook_paths(root, readme)
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
        # Library-style research repos often expose package APIs plus notebooks
        # instead of script entrypoints. Import the local package as the smoke.
        cmd = _import_smoke_cmd(import_targets[0]) if import_targets else [
            "python",
            "-c",
            "import os; print('cwd=', os.getcwd()); print('ok')",
        ]
        tasks.append(
            {
                "id": "repo_smoke",
                "family": "smoke",
                "cwd": "{paper_root}",
                "cmd": cmd,
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

    tasks = _finalize_tasks(
        repo_root=root,
        tasks=tasks,
        entrypoints=entrypoints,
        readme_example_cmds=examples,
        notebook_paths=notebook_paths,
        datasets=datasets,
        mode=mode,
        paper_metric_targets=paper_metric_targets,
    )
    evidence = {
        "mode": mode,
        "entrypoints": entrypoints,
        "python_import_targets": import_targets,
        "notebook_paths": notebook_paths,
        "datasets_detected": datasets,
        "readme_has_content": bool(readme.strip()),
        "requirements_present": requirements_present,
        "readme_example_cmds": examples,
        "paper_metric_targets_count": len(paper_metric_targets or []),
        "external_api_tasks_disabled": _external_api_tasks_disabled(),
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
    import_targets = _detect_python_import_targets(root)
    notebook_paths = _extract_notebook_paths(root, readme)
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
        "python_import_targets_detected": import_targets,
        "notebook_paths_detected": notebook_paths,
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
            "For library-plus-notebook repos, include an import smoke task and use jupyter nbconvert execution for notebooks that reproduce paper experiments.",
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
        entrypoints=entrypoints,
        readme_example_cmds=readme_example_cmds,
        notebook_paths=notebook_paths,
        datasets=datasets,
        mode=mode,
        paper_metric_targets=paper_metric_targets,
    )
    evidence = {
        "mode": mode,
        "llm_used": True,
        "llm_provider": llm_cfg.provider,
        "llm_model": llm_cfg.model,
        "python_import_targets": import_targets,
        "notebook_paths": notebook_paths,
        "raw": resp,
        "paper_metric_targets_count": len(paper_metric_targets or []),
    }
    return InferResult(tasks=finalized, evidence=evidence)

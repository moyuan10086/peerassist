from __future__ import annotations

import os
import re
import shlex
import shutil
from pathlib import Path
from typing import Any

from llm.client import llm_json, resolve_llm_config
from util.fs import ensure_dir, write_text
from util.recorder import append_event
from util.subprocess_runner import persist_command_result, run_command

from ..tools.docker import _docker_env_passthrough, docker_ensure_paper_image, docker_run_paper_image


def _extract_missing_module(stderr: str) -> str | None:
    # ModuleNotFoundError: No module named 'xxx'
    m = re.search(r"No module named ['\"]([^'\"]+)['\"]", stderr or "")
    if m:
        return m.group(1)
    return None


_MODULE_TO_PIP = {
    # common mismatches
    "sklearn": "scikit-learn",
    "cv2": "opencv-python",
    "PIL": "pillow",
    "yaml": "pyyaml",
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


def _missing_module_looks_local(paper_root: str, module: str) -> bool:
    root = str(module or "").strip().split(".", 1)[0]
    if not root:
        return False
    pr = Path(paper_root or ".")
    return (pr / root).is_dir() or (pr / f"{root}.py").is_file()


def _add_extra_pip_package(cfg: dict[str, Any], package: str) -> bool:
    package = str(package or "").strip()
    if not package:
        return False
    raw = str(cfg.get("docker_extra_pip_packages") or os.getenv("EXECUTION_DOCKER_EXTRA_PIP_PACKAGES") or "")
    existing = [x.strip() for x in raw.split() if x.strip()]
    if package in existing:
        return False
    existing.append(package)
    cfg["docker_extra_pip_packages"] = " ".join(existing)
    # Force docker_ensure_paper_image to compute a fresh tag from the updated cfg.
    cfg.pop("docker_paper_image", None)
    return True


def _docker_build_timeout(cfg: dict[str, Any]) -> int:
    raw = cfg.get("docker_build_timeout_sec")
    if raw in (None, ""):
        raw = os.environ.get("EXECUTION_DOCKER_BUILD_TIMEOUT_SEC", "3600")
    return int(raw)


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

    missing = _extract_missing_module(stderr_tail)

    # Deterministic quick-fix: smoke task points to a missing script (common when we used a generic template).
    missing_file = _extract_missing_file(stderr_tail)
    if missing_file:
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

    if missing:
        append_event(run_dir, "fix_missing_module", {"module": missing})
        state.setdefault("history", []).append({"kind": "fix_missing_module", "data": {"module": missing}})

    if missing and _missing_module_looks_local(paper_root, missing):
        append_event(run_dir, "fix_missing_module_local_path", {"module": missing, "paper_root": paper_root})
        state.setdefault("history", []).append(
            {"kind": "fix_missing_module_local_path", "data": {"module": missing, "paper_root": paper_root}}
        )
        state["status"] = "running"
        return state

    if missing and docker_enabled and missing != "torch_scatter":
        package = _pip_package_for_module(missing)
        if _add_extra_pip_package(cfg, package):
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
                {"ok": ok_img, "package": package, "detail": img_or_msg},
            )
            state.setdefault("history", []).append(
                {
                    "kind": "fix_rebuild_image_extra_pip",
                    "data": {"ok": ok_img, "package": package, "detail": img_or_msg},
                }
            )
            if ok_img:
                cfg["docker_paper_image"] = img_or_msg
                state["config"] = cfg
                state["status"] = "running"
                return state

    # Deterministic fix: missing torch_scatter.
    # Prefer installing in-container (wheel index), then fallback injection module. Avoid editing paper code.
    if missing == "torch_scatter" and docker_enabled:
        shell = (
            "set -e\n"
            "TV=$(python -c \"import torch; print((torch.__version__ or '').split('+')[0])\" 2>/dev/null || true)\n"
            'if [ -n "$TV" ]; then\n'
            "  python -m pip install --no-cache-dir torch-scatter -f https://data.pyg.org/whl/torch-${TV}+cpu.html -f https://data.pyg.org/whl/torch-${TV}.html || true\n"
            "fi\n"
            "python -c \"import torch_scatter; print('torch_scatter_ok')\" || true\n"
        )
        shell = (
            shell
            + "\n"
            + _torch_scatter_fallback_in_container_shell()
            + "\npython -c \"import torch_scatter; print('torch_scatter_ok_after_fallback')\""
        )
        ok_img, img_or_msg = docker_ensure_paper_image(
            cfg,
            paper_key=str(cfg.get("paper_key") or "paper"),
            paper_root_host=str(Path(paper_root).resolve()),
            python_spec=python_spec,
            timeout_sec=_docker_build_timeout(cfg),
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
            persist_command_result(res, logs_dir, prefix=f"fix_torch_scatter_{attempt}")
            if res.returncode == 0:
                append_event(run_dir, "fix_install_torch_scatter", {"ok": True, "strategy": "paper_image"})
                state.setdefault("history", []).append(
                    {"kind": "fix_install_torch_scatter", "data": {"ok": True, "strategy": "paper_image"}}
                )
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
    system = (
        "You are a senior engineer doing rigorous paper-code reproduction.\n"
        "Produce a fix plan ONLY in JSON. Do not include prose outside JSON.\n"
        "The plan must be safe and reproducible, prefer environment/command fixes before source edits.\n"
        "Use the execution paths supplied in the prompt. When docker is disabled, do not use container "
        "paths such as /app or /workspace/run_dir.\n"
        "When docker is disabled, do not install or upgrade packages in the host Python/system environment "
        "unless the prompt explicitly says host dependency installs are allowed.\n"
    )
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
        "constraints": {
            "prefer_wrapper_env_fixes": True,
            "avoid_core_source_changes": True,
            "must_be_reproducible": True,
            "execution_environment": "docker" if docker_enabled else "host",
            "paths": path_constraints,
            "host_dependency_installs_allowed": _host_dependency_installs_allowed(cfg),
            "primary_goal": "make tasks produce metric artifacts that can be compared against paper_metric_targets",
        },
        "output_schema": {
            "category": "env|deps|path|encoding|data|runtime|other",
            "root_cause": "short string",
            "actions": [
                {"type": "command", "cmd": ["..."], "cwd": ".", "timeout_sec": 600, "why": "short"},
                {"type": "edit", "path": "relative/path", "content": "full new file content", "why": "short"},
            ],
            "confidence": 0.0,
        },
    }
    plan = llm_json(prompt=str(prompt), system=system, cfg=llm_cfg)
    write_text(
        fixes_dir / f"fix_{attempt:03d}_plan.json",
        __import__("json").dumps(plan, ensure_ascii=False, indent=2) + "\n",
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
                    argv = _normalize_llm_cmd_for_platform(cmd)
                    res = run_command(cmd=argv, cwd=cwd, timeout_sec=timeout)
                persist_command_result(res, logs_dir, prefix=f"fix_cmd_{attempt}_{j}")
                ok = res.returncode == 0
                append_event(run_dir, "fix_command", {"cmd": cmd, "cwd": cwd, "ok": ok, "rc": res.returncode})
                state.setdefault("history", []).append(
                    {"kind": "fix_command", "data": {"cmd": cmd, "cwd": cwd, "ok": ok, "rc": res.returncode}}
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
                # Safety: only allow editing the tasks file (wrapper config), not paper code.
                tasks_path = str(cfg.get("tasks_path") or "").strip()
                path = str(act.get("path") or "").strip()
                content = act.get("content")
                if not tasks_path or not path or not isinstance(content, str):
                    continue
                try:
                    # allow absolute or relative path that resolves to the tasks_path
                    target = Path(path)
                    if not target.is_absolute():
                        if Path(path).name == Path(tasks_path).name or path.replace("\\", "/") in {
                            "tasks.yaml",
                            "tasks.yml",
                        }:
                            target = Path(tasks_path)
                        else:
                            target = Path(paper_root) / target
                    if str(target.resolve()).lower() != str(Path(tasks_path).resolve()).lower():
                        continue
                    if docker_enabled:
                        content = _normalize_container_path_text(content, paper_root, run_dir)
                    write_text(target, content)
                    write_text(
                        fixes_dir / f"fix_{attempt:03d}_edit_tasks.txt", f"Edited tasks file: {tasks_path}\n"
                    )
                    append_event(run_dir, "fix_edit_tasks", {"path": tasks_path, "ok": True})
                    state.setdefault("history", []).append(
                        {"kind": "fix_edit_tasks", "data": {"path": tasks_path}}
                    )
                    applied_any = True
                except Exception:
                    continue

    # If nothing applied, stop (we still recorded the plan).
    if not applied_any:
        state["status"] = "failed"
        append_event(run_dir, "fix_not_applied", {"reason": "no_applicable_actions"})
        state.setdefault("history", []).append(
            {"kind": "fix_not_applied", "data": {"reason": "no_applicable_actions"}}
        )
        return state

    state["status"] = "running"
    return state

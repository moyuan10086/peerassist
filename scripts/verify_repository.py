#!/usr/bin/env python3
"""Run the supported PeerAssist repository verification checks."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

Command = tuple[str, ...]
Plan = list[tuple[str, Command]]
Runner = Callable[..., subprocess.CompletedProcess[str]]

REPOSITORY_ROOT = Path(__file__).parents[1]
CHECKS: dict[str, Command] = {
    "python": ("python", "-m", "pytest", "-q"),
    "platform": ("python", "-m", "pytest", "-q", "tests/platform", "tests/repository/test_m1_compose_contract.py"),
    "m1-system": ("bash", "scripts/m1_compose_smoke.sh"),
    "lint": ("python", "-m", "ruff", "check", "src", "tests", "scripts"),
    "frontend": ("npm", "run", "build", "--prefix", "web/peerassist-workspace"),
    "docs": ("python", "scripts/check_docs.py"),
    "secrets": ("python", "scripts/check_secrets.py"),
    "dependencies": ("python", "-m", "pip", "check"),
    "dist": ("git", "diff", "--exit-code", "--", "web/peerassist-workspace/dist"),
}
FAST_CHECKS = ("lint", "python", "docs", "secrets", "frontend")
ALL_CHECKS = (*FAST_CHECKS, "dependencies", "dist")
M1_ALL_CHECKS = (*ALL_CHECKS, "platform", "m1-system")


def command_plan(modes: Sequence[str] | str) -> Plan:
    """Expand group names or preserve the requested individual check order."""
    requested = [modes] if isinstance(modes, str) else list(modes)
    if not requested:
        raise ValueError("at least one check is required")
    groups = {"fast": FAST_CHECKS, "all": ALL_CHECKS, "m1-all": M1_ALL_CHECKS}
    if any(name in groups for name in requested):
        if len(requested) != 1 or requested[0] not in groups:
            raise ValueError("group checks cannot be combined")
        requested = list(groups[requested[0]])
    if any(name not in CHECKS for name in requested):
        raise ValueError("unknown check")
    return [(name, CHECKS[name]) for name in requested]


def run_checks(plan: Plan, *, repo_root: Path = REPOSITORY_ROOT, runner: Runner = subprocess.run) -> int:
    """Stream checks in order and return immediately with the first failure."""
    environment = os.environ.copy()
    source_paths = [str(repo_root / "src"), str(repo_root / "services")]
    existing_pythonpath = environment.get("PYTHONPATH")
    if existing_pythonpath:
        source_paths.append(existing_pythonpath)
    environment["PYTHONPATH"] = os.pathsep.join(source_paths)
    # A developer machine may have a real homepage model configuration. Keep
    # repository checks deterministic and prevent that user state from changing
    # assertions that exercise process-environment defaults.
    if "PEERASSIST_MODEL_SETTINGS_PATH" not in environment:
        isolated_settings = Path(tempfile.gettempdir()) / f"peerassist-verify-model-settings-{os.getpid()}.json"
        isolated_settings.unlink(missing_ok=True)
        environment["PEERASSIST_MODEL_SETTINGS_PATH"] = str(isolated_settings)
    for label, command in plan:
        argv = (sys.executable, *command[1:]) if command[0] == "python" else command
        print(f"[check] {label}", flush=True)
        if runner is subprocess.run:
            result = runner(argv, cwd=repo_root, check=False, env=environment)
        else:
            result = runner(argv, cwd=repo_root, check=False)
        if result.returncode != 0:
            return result.returncode
    return 0


def _usage() -> str:
    names = "|".join(("fast", "all", "m1-all", *CHECKS))
    return f"usage: verify_repository.py <{names}> [check ...]"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        plan = command_plan(args)
    except ValueError:
        print(_usage(), file=sys.stderr)
        return 2
    return run_checks(plan)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run the supported PeerAssist repository verification checks."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

Command = tuple[str, ...]
Plan = list[tuple[str, Command]]
Runner = Callable[..., subprocess.CompletedProcess[str]]

REPOSITORY_ROOT = Path(__file__).parents[1]
CHECKS: dict[str, Command] = {
    "python": ("python", "-m", "pytest", "-q"),
    "lint": ("python", "-m", "ruff", "check", "src", "tests", "scripts"),
    "frontend": ("npm", "run", "build", "--prefix", "web/peerassist-workspace"),
    "docs": ("python", "scripts/check_docs.py"),
    "secrets": ("python", "scripts/check_secrets.py"),
    "dependencies": ("python", "-m", "pip", "check"),
    "dist": ("git", "diff", "--exit-code", "--", "web/peerassist-workspace/dist"),
}
FAST_CHECKS = ("lint", "python", "docs", "secrets", "frontend")
ALL_CHECKS = (*FAST_CHECKS, "dependencies", "dist")


def command_plan(modes: Sequence[str] | str) -> Plan:
    """Expand group names or preserve the requested individual check order."""
    requested = [modes] if isinstance(modes, str) else list(modes)
    if not requested:
        raise ValueError("at least one check is required")
    groups = {"fast": FAST_CHECKS, "all": ALL_CHECKS}
    if any(name in groups for name in requested):
        if len(requested) != 1 or requested[0] not in groups:
            raise ValueError("group checks cannot be combined")
        requested = list(groups[requested[0]])
    if any(name not in CHECKS for name in requested):
        raise ValueError("unknown check")
    return [(name, CHECKS[name]) for name in requested]


def run_checks(plan: Plan, *, repo_root: Path = REPOSITORY_ROOT, runner: Runner = subprocess.run) -> int:
    """Stream checks in order and return immediately with the first failure."""
    for label, command in plan:
        argv = (sys.executable, *command[1:]) if command[0] == "python" else command
        print(f"[check] {label}", flush=True)
        result = runner(argv, cwd=repo_root, check=False)
        if result.returncode != 0:
            return result.returncode
    return 0


def _usage() -> str:
    names = "|".join(("fast", "all", *CHECKS))
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

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from scripts.verify_repository import CHECKS, command_plan, main, run_checks

REPOSITORY_ROOT = Path(__file__).parents[2]


def test_command_plan_expands_fast_and_all_in_stable_order() -> None:
    fast = command_plan(["fast"])
    all_checks = command_plan(["all"])

    assert [label for label, _ in fast] == ["lint", "python", "docs", "secrets", "frontend"]
    assert all_checks == [*fast, ("dependencies", CHECKS["dependencies"]), ("dist", CHECKS["dist"])]
    assert fast[0][1][0] == "python"
    assert fast[1][1] == ("python", "-m", "pytest", "-q")


def test_command_plan_preserves_individual_check_order_and_duplicates() -> None:
    assert command_plan(["docs", "secrets", "docs"]) == [
        ("docs", CHECKS["docs"]),
        ("secrets", CHECKS["secrets"]),
        ("docs", CHECKS["docs"]),
    ]


def test_command_plan_rejects_unknown_or_mixed_group_names() -> None:
    with pytest.raises(ValueError):
        command_plan(["unknown"])
    with pytest.raises(ValueError):
        command_plan(["fast", "docs"])


def test_run_checks_replaces_python_stops_on_first_failure_and_uses_repo_root(tmp_path: Path) -> None:
    calls: list[tuple[tuple[str, ...], Path]] = []

    def runner(argv: tuple[str, ...], *, cwd: Path, check: bool) -> subprocess.CompletedProcess[str]:
        calls.append((argv, cwd))
        return subprocess.CompletedProcess(argv, 7 if len(calls) == 2 else 0)

    result = run_checks(
        [("first", ("python", "one.py")), ("second", ("tool", "two")), ("third", ("tool", "three"))],
        repo_root=tmp_path,
        runner=runner,
    )

    assert result == 7
    assert calls == [
        ((sys.executable, "one.py"), tmp_path),
        (("tool", "two"), tmp_path),
    ]


def test_main_returns_safe_usage_error_for_unknown_check(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["unknown"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("usage: verify_repository.py")


def test_cli_individual_docs_and_secrets_runs_from_subdirectory() -> None:
    result = subprocess.run(
        [sys.executable, str(REPOSITORY_ROOT / "scripts" / "verify_repository.py"), "docs", "secrets"],
        cwd=REPOSITORY_ROOT / "tests",
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "[check] docs" in result.stdout
    assert "[check] secrets" in result.stdout
    assert result.stderr == ""


def test_makefile_compose_aliases_target_development_profile() -> None:
    source = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "docker compose -f infrastructure/compose/compose.yml up --build" in source
    assert "docker compose -f infrastructure/compose/compose.yml down" in source

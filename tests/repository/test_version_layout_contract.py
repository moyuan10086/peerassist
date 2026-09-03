from __future__ import annotations

import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _git_status(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_current_branch_and_history_contract_are_explicit() -> None:
    current_branch = _git("branch", "--show-current")
    if current_branch:
        assert current_branch == "peerassist-m0"

    current_ref = _git_status("rev-parse", "--verify", "refs/heads/peerassist-m0")
    historical_ref = _git_status("rev-parse", "--verify", "refs/heads/peerassist-mvp")
    if current_ref.returncode == 0 and historical_ref.returncode == 0:
        result = subprocess.run(
            [
                "git",
                "merge-base",
                "--is-ancestor",
                "refs/heads/peerassist-mvp",
                "refs/heads/peerassist-m0",
            ],
            cwd=REPOSITORY_ROOT,
            check=False,
        )
        assert result.returncode == 0


def test_only_the_repository_root_is_registered_as_a_worktree() -> None:
    entries = _git("worktree", "list", "--porcelain").splitlines()
    worktree_paths = [line.removeprefix("worktree ") for line in entries if line.startswith("worktree ")]

    assert worktree_paths == [str(REPOSITORY_ROOT)]
    assert not Path("/root/.worktrees/peerassist-m0").exists()
    assert not Path("/root/PeerAssist/current").exists()


def test_project_overview_names_current_and_historical_refs() -> None:
    source = (REPOSITORY_ROOT / "docs/PROJECT_OVERVIEW.md").read_text(encoding="utf-8")

    assert "当前开发分支：`peerassist-m0`" in source
    assert "已归档 | `peerassist-mvp`" in source
    assert "eval/PeerAssist-Eval-v1" in source
    assert "唯一代码仓库" in source

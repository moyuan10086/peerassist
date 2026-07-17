from __future__ import annotations

from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).parents[2]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
SMOKE_PATH = REPOSITORY_ROOT / "scripts" / "bootstrap_smoke.sh"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _steps(job: dict) -> list[dict]:
    return job["steps"]


def _step_uses(job: dict, prefix: str) -> dict:
    return next(step for step in _steps(job) if str(step.get("uses", "")).startswith(prefix))


def _commands(job: dict) -> str:
    return "\n".join(str(step.get("run", "")) for step in _steps(job))


def test_ci_triggers_permissions_and_jobs_are_bounded() -> None:
    workflow = _workflow()

    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["on"]["push"]["branches"] == ["peerassist-mvp", "main"]
    assert workflow["on"]["pull_request"]["branches"] == ["peerassist-mvp", "main"]
    assert set(workflow["jobs"]) == {"python", "frontend", "repository-policy", "clean-checkout-smoke"}


def test_python_job_uses_matrix_official_pypi_and_supported_checks() -> None:
    job = _workflow()["jobs"]["python"]

    assert job["strategy"]["matrix"]["python-version"] == ["3.11", "3.12"]
    assert _step_uses(job, "actions/checkout@v4")["with"]["lfs"] is True
    assert _step_uses(job, "actions/setup-python@v5")["with"]["python-version"] == "${{ matrix.python-version }}"
    commands = _commands(job)
    assert "--index-url https://pypi.org/simple" in commands
    assert ".[runtime,positioning,refcheck,dev]" in commands
    assert "python -m ruff check src tests scripts" in commands
    assert "python -m pytest -q" in commands


def test_frontend_and_repository_policy_jobs_use_locked_commands() -> None:
    jobs = _workflow()["jobs"]
    frontend = jobs["frontend"]
    policy = jobs["repository-policy"]

    assert _step_uses(frontend, "actions/setup-node@v4")["with"]["node-version"] == 24
    assert "npm ci" in _commands(frontend)
    assert "npm run build" in _commands(frontend)
    assert "git diff --exit-code -- web/peerassist-workspace/dist" in _commands(frontend)
    assert "python scripts/check_docs.py" in _commands(policy)
    assert "python scripts/check_secrets.py" in _commands(policy)


def test_clean_checkout_smoke_job_only_invokes_bootstrap_entrypoint() -> None:
    job = _workflow()["jobs"]["clean-checkout-smoke"]

    assert _step_uses(job, "actions/checkout@v4")["with"]["lfs"] is True
    assert _step_uses(job, "actions/setup-python@v5")["with"]["python-version"] == "3.12"
    assert _step_uses(job, "actions/setup-node@v4")["with"]["node-version"] == 24
    run_steps = [str(step["run"]).strip() for step in _steps(job) if "run" in step]
    assert run_steps == ["bash scripts/bootstrap_smoke.sh"]


def test_bootstrap_smoke_contract_is_isolated_bounded_and_does_not_dump_environment() -> None:
    source = SMOKE_PATH.read_text(encoding="utf-8")

    for marker in (
        "set -Eeuo pipefail",
        "mktemp -d",
        'git -C "$source_root" status --porcelain',
        'git -C "$source_root" show "$commit:$PDF_PATH"',
        "oid sha256:",
        "expected_oid",
        "[0-9a-f]{64}",
        "GIT_LFS_SKIP_SMUDGE=1",
        "git clone --local --no-hardlinks",
        "git lfs fetch origin",
        "--include=demos/Text/bert/paper.pdf",
        "git lfs checkout demos/Text/bert/paper.pdf",
        'sha256sum "$PDF_PATH"',
        "PEERASSIST_BOOTSTRAP_INNER=1",
        "git status --porcelain",
        "head -c 4 demos/Text/bert/paper.pdf",
        "python -m venv .venv",
        "--index-url https://pypi.org/simple",
        "npm ci",
        "python scripts/verify_repository.py all",
        "docker compose -f infrastructure/compose/compose.yml",
        "COMPOSE_PROJECT_NAME",
        "ps --status running --services",
        "review-api",
        "workspace",
        "PEERASSIST_WORKSPACE_BIND_PORT=0",
        'docker compose -f "$COMPOSE_FILE" port workspace 8766',
        "seq 1 30",
        'test -n "$workspace_address"',
        'http://$workspace_address/api/health',
        "down -v",
        "Content-Range",
        "206",
        "1024",
        "cmp",
        "trap cleanup EXIT",
    ):
        assert marker in source
    assert "curl -I" not in source
    assert "--object-id" not in source
    assert 'grep -q \'"Health":"healthy"\'' not in source
    assert "printenv" not in source
    assert "env |" not in source


def test_readme_links_development_guidance_and_uses_stable_feishu_chapters() -> None:
    source = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")

    assert "[CONTRIBUTING.md](CONTRIBUTING.md)" in source
    assert "python scripts/verify_repository.py all" in source
    assert "10 个固定章节" in source
    assert "精确更新" in source
    assert "只追加新章节" not in source

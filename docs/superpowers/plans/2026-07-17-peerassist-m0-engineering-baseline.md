# PeerAssist M0 Engineering Baseline Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a green, reproducible, securely configured engineering baseline before FastAPI, Next.js, tenancy, or evidence-canvas feature work begins.

**Architecture:** Preserve current Vite and standard-library HTTP behavior while adding repository guidance, executable policy checks, CI, secure configuration, a two-service Compose profile, and frozen legacy HTTP contracts. Work from the separately committed plan checkpoint in a clean isolated worktree. Treat the original dirty worktree as read-only evidence: hash it before work, validate its uncommitted frontend separately, and never copy its generated `dist` or unrelated changes into M0.

**Tech Stack:** Python 3.11/3.12, pytest, Ruff, Pydantic Settings, React 18, TypeScript, Vite, Docker Compose, GitHub Actions, Git LFS, lark-cli.

**Design spec:** `docs/superpowers/specs/2026-07-17-peerassist-platform-canvas-agent-design.md`

---

## Scope And File Map

M0 does not add FastAPI, Next.js, PostgreSQL, S3, OIDC, or React Flow. It creates the clean baseline those milestones require.

| File | Responsibility |
| --- | --- |
| `AGENTS.md` | Durable repository commands, boundaries, security rules, and definition of done |
| `CONTRIBUTING.md` | Human contribution and verification workflow |
| `SECURITY.md` | Deployment boundary, confidential-paper handling, and reporting policy |
| `docs/product/peerassist-platform-prd.md` | Product scope, personas, invariants, and M0-M5 outcomes |
| `docs/api/conventions.md` | Versioning, tenancy, errors, idempotency, events, and compatibility |
| `docs/adr/*.md` | Accepted architecture decisions and alternatives |
| `scripts/check_docs.py` | Deterministic local Markdown-link checker |
| `scripts/check_secrets.py` | Redacted repository credential-pattern checker |
| `scripts/verify_repository.py` | One entry point for baseline checks |
| `scripts/bootstrap_smoke.sh` | One-command clean-checkout install, verification, Compose, PDF, and Range smoke |
| `Makefile` | Short aliases for verification and Compose |
| `.github/workflows/ci.yml` | Python, frontend, and repository-policy CI |
| `src/common/config.py` | Typed workspace model and Review API connection settings |
| `infrastructure/compose/*` | Reproducible non-root development profile |
| `deploy/systemd/*` | Parameterized, loopback-by-default examples |
| `tests/repository/*` | Repository-policy and runner tests |
| `tests/contracts/*` | Frozen legacy route/response contracts |

## M0-Owned Path Manifest And Staging Rule

Only the paths explicitly named in a task's **Files** list are M0-owned. Existing dirty files in
the source worktree remain user-owned even when a task names the same path; M0 edits are made only
in the clean worktree and must be reviewed as intentional hunks. In particular, M0 does not import
the source worktree's current `web/peerassist-workspace/dist/*`, `src/main.tsx`, `styles.css`, or
unrelated documentation changes.

Every commit uses `git add -- <exact paths>`; directory-wide staging such as `git add docs`,
`git add src`, `git add tests`, `git add -A`, and `git commit -a` is forbidden. Before every
commit, run this gate with that task's exact allowlist as the remaining arguments:

```bash
git diff --cached --check
.venv/bin/python -c 'import subprocess, sys; actual = {p for p in subprocess.check_output(["git", "diff", "--cached", "--name-only", "-z"]).decode().split("\0") if p}; allowed = set(sys.argv[1:]); extra = sorted(actual - allowed); assert actual, "nothing staged"; assert not extra, f"unexpected staged paths: {extra}"' <task allowlist>
git diff --cached --name-status
git diff --cached
```

Do not commit until the complete staged diff has been inspected and contains no manuscript text,
credential, runtime data, absolute private path, or unrelated user hunk.

## Execution Prerequisite

The source worktree contains user changes not included in commit `e8f85ee`. Do not stash, reset,
clean, LFS-checkout, build, or commit them wholesale. Before Task 1, commit this reviewed plan and
only this plan on `peerassist-mvp`; Task 1 captures that current `HEAD` as `M0_PLAN_COMMIT`. Its parent must be
`e8f85ee`, and its changed-path set must contain only this plan. Task 1 creates a clean M0
worktree at that checkpoint. All later repository mutations run there.

## Chunk 1: Preserve And Green The Existing Baseline

### Task 1: Create A Clean Isolated Worktree And Protect The Source

**Files:**
- Create outside repository: `/tmp/peerassist-m0-source/status.before.z`
- Create outside repository: `/tmp/peerassist-m0-source/files.before.sha256`
- Create outside repository: `/tmp/peerassist-m0-source/worktrees.before.txt`
- Create worktree: `/root/.worktrees/peerassist-m0`

- [ ] **Step 1: Verify external prerequisites without modifying the repository**

```bash
python3.12 --version
docker compose version
git lfs version
```

Expected: Python 3.12 and Docker Compose v2 are available. On the current host Git LFS is missing;
install the operating-system `git-lfs` package, but do not run `git lfs pull` or `git lfs
checkout` in the source worktree. Package installation is a host prerequisite, not a repository
edit.

Installing the host package is a privileged machine mutation. If Git LFS is still unavailable at
execution time, pause and obtain explicit user approval for the operating-system package install;
do not silently substitute the pointer file or weaken PDF/Compose acceptance.

- [ ] **Step 2: Record the source state without modifying it**

Run from `/root/peerassist-review-system-20260710/peerassist`:

```bash
mkdir -p /tmp/peerassist-m0-source
M0_PLAN_COMMIT="$(git rev-parse HEAD)"
export M0_PLAN_COMMIT
test "$(git branch --show-current)" = "peerassist-mvp"
test "$(git rev-parse "${M0_PLAN_COMMIT}^")" = "e8f85ee"
test "$(git diff-tree --no-commit-id --name-only -r "${M0_PLAN_COMMIT}")" = \
  "docs/superpowers/plans/2026-07-17-peerassist-m0-engineering-baseline.md"
test ! -e /root/.worktrees/peerassist-m0
! git show-ref --verify --quiet refs/heads/peerassist-m0
git worktree list --porcelain > /tmp/peerassist-m0-source/worktrees.before.txt
git status --porcelain=v1 -z > /tmp/peerassist-m0-source/status.before.z
git ls-files -co --exclude-standard -z | sort -z \
  | while IFS= read -r -d '' path; do
      if test -f "${path}"; then sha256sum -- "${path}"; fi
    done > /tmp/peerassist-m0-source/files.before.sha256
```

Expected: the plan checkpoint has exactly the expected parent/path, the destination branch/path
do not exist, and status plus content hashes are captured before any LFS operation.

- [ ] **Step 3: Create the branch and worktree**

```bash
git lfs fetch origin "${M0_PLAN_COMMIT}" \
  --include='demos/Text/bert/paper.pdf'
GIT_LFS_SKIP_SMUDGE=1 git worktree add -b peerassist-m0 \
  /root/.worktrees/peerassist-m0 "${M0_PLAN_COMMIT}"
cd /root/.worktrees/peerassist-m0
git lfs checkout demos/Text/bert/paper.pdf
test "$(head -c 5 demos/Text/bert/paper.pdf)" = "%PDF-"
git status --short
```

Expected: LFS fetch changes only object storage; the real PDF is materialized only in the clean
worktree; status is empty. Never accept the 131-byte LFS pointer as a PDF.

- [ ] **Step 4: Verify snapshot fidelity**

```bash
cd /root/peerassist-review-system-20260710/peerassist
git status --porcelain=v1 -z > /tmp/peerassist-m0-source/status.after-create.z
git ls-files -co --exclude-standard -z | sort -z \
  | while IFS= read -r -d '' path; do
      if test -f "${path}"; then sha256sum -- "${path}"; fi
    done > /tmp/peerassist-m0-source/files.after-create.sha256
cmp /tmp/peerassist-m0-source/status.before.z \
  /tmp/peerassist-m0-source/status.after-create.z
cmp /tmp/peerassist-m0-source/files.before.sha256 \
  /tmp/peerassist-m0-source/files.after-create.sha256
```

Expected: source status and every tracked/untracked file hash are unchanged. Worktree metadata is
allowed to gain only `/root/.worktrees/peerassist-m0`.

- [ ] **Step 5: Create the worktree environment and record the known red baseline**

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --index-url https://pypi.org/simple --upgrade pip
.venv/bin/python -m pip install --index-url https://pypi.org/simple \
  -e ".[runtime,positioning,refcheck,dev]"
npm ci --prefix web/peerassist-workspace
.venv/bin/python -m pytest -q \
  tests/peerassist/test_confirmation_server.py::test_workspace_frontend_contains_pdfjs_review_reader
.venv/bin/python -m ruff check src tests scripts --output-format concise
```

Expected: the clean checkpoint's focused frontend contract and full suite pass (`865 passed, 1
skipped, 3 deselected` on the recorded host); Ruff 0.15.22 reports 21 findings. The host's default
package mirror returned a wheel whose bytes did not match the index hash, so the reproducible
setup uses explicit official PyPI without weakening hash verification.

### Task 2: Make The Frontend Contract Shape-Stable And Validate User UI Separately

**Files:**
- Modify: `tests/peerassist/test_confirmation_server.py:676`
- Test: `tests/peerassist/test_confirmation_server.py`

- [ ] **Step 1: Replace the brittle exact source-shape assertion**

Replace both duplicate exact-call assertions with a semantic minimum that passes both the clean
two-field callback and the source worktree's richer selection snapshot:

```python
assert "onSelection({" in source
assert "text," in source
assert "page: pageNumber" in source
```

- [ ] **Step 2: Run focused and module tests**

```bash
.venv/bin/python -m pytest -q \
  tests/peerassist/test_confirmation_server.py::test_workspace_frontend_contains_pdfjs_review_reader
.venv/bin/python -m pytest -q tests/peerassist/test_confirmation_server.py
```

Expected: focused test passes on the clean checkpoint; all module tests pass.

- [ ] **Step 3: Validate the dirty user frontend without importing it**

Copy only the frontend package to disposable storage, excluding `dist` and `node_modules`, then
build it there. Run a small source-contract check against the original `src/main.tsx` for
`text`, `page`, `pageId`, `layerId`, span indices, and offsets. Re-run the Task 1 source status
and hash comparison afterwards. Do not apply or stage the dirty frontend patch in M0.

```bash
rm -rf /tmp/peerassist-user-ui-validation
mkdir -p /tmp/peerassist-user-ui-validation
rsync -a --exclude=dist --exclude=node_modules \
  /root/peerassist-review-system-20260710/peerassist/web/peerassist-workspace/ \
  /tmp/peerassist-user-ui-validation/
npm ci --prefix /tmp/peerassist-user-ui-validation
npm run build --prefix /tmp/peerassist-user-ui-validation
python3.12 - <<'PY'
from pathlib import Path

source = Path(
    "/root/peerassist-review-system-20260710/peerassist/"
    "web/peerassist-workspace/src/main.tsx"
).read_text(encoding="utf-8")
for marker in (
    "onSelection({",
    "text,",
    "page: pageNumber",
    "pageId:",
    "layerId:",
    "startSpanIndex:",
    "startOffset:",
    "endSpanIndex:",
    "endOffset:",
):
    assert marker in source, marker
PY
```

Expected: the disposable build passes, its output stays under `/tmp`, and the source worktree
hash/status manifests still match. A failure is reported as a separate user-UI regression and
does not contaminate the canonical clean M0 verification.

- [ ] **Step 4: Commit only the contract repair**

```bash
git add -- tests/peerassist/test_confirmation_server.py
# Run the staging gate with tests/peerassist/test_confirmation_server.py.
git commit -m "test: align PDF selection contract with snapshots"
```

### Task 3: Eliminate The Existing Ruff Baseline

**Files:**
- Modify: `src/agent_runtime/agent_tools.py`
- Modify: `src/common/run_stats.py`
- Modify: `src/common/state.py`
- Modify: `src/fact_generation/execution/tools/task_infer.py`
- Modify: `src/peerassist/mcp_registry.py`
- Modify: `src/peerassist/ocr_providers.py`
- Modify: `src/preprocessing/parse/stage_runner.py`
- Modify: `src/review/teaser/teaser.py`
- Modify: `tests/peerassist/test_capabilities.py`
- Modify: `tests/peerassist/test_concerns.py`
- Modify: `tests/peerassist/test_deterministic_checks.py`
- Modify: `tests/peerassist/test_eval_cli.py`

- [ ] **Step 1: Apply Ruff safe fixes only to the owned files**

```bash
.venv/bin/python -m ruff check --fix \
  src/agent_runtime/agent_tools.py \
  src/common/run_stats.py \
  src/common/state.py \
  src/fact_generation/execution/tools/task_infer.py \
  src/peerassist/mcp_registry.py \
  src/peerassist/ocr_providers.py \
  src/preprocessing/parse/stage_runner.py \
  src/review/teaser/teaser.py \
  tests/peerassist/test_capabilities.py \
  tests/peerassist/test_concerns.py \
  tests/peerassist/test_deterministic_checks.py \
  tests/peerassist/test_eval_cli.py
.venv/bin/python -c 'import subprocess; actual = set(subprocess.check_output(["git", "diff", "--name-only", "-z"]).decode().split("\0")) - {""}; allowed = {"src/agent_runtime/agent_tools.py", "src/common/run_stats.py", "src/common/state.py", "src/fact_generation/execution/tools/task_infer.py", "src/peerassist/mcp_registry.py", "src/peerassist/ocr_providers.py", "src/preprocessing/parse/stage_runner.py", "src/review/teaser/teaser.py", "tests/peerassist/test_capabilities.py", "tests/peerassist/test_concerns.py", "tests/peerassist/test_deterministic_checks.py", "tests/peerassist/test_eval_cli.py"}; assert actual, "Ruff made no changes; inspect the recorded baseline"; assert actual <= allowed, f"Ruff changed paths outside the M0 allowlist: {sorted(actual - allowed)}"'
```

Expected: safe import, unused-import, conversion, annotation, and simplification fixes apply.
Do not use `--unsafe-fixes`.

- [ ] **Step 2: Fix remaining semantic findings explicitly**

Use these exact forms where still reported:

```python
return max(1, math.ceil(len(clean) / 4))
```

```python
return value.endswith(
    (".read_csv", ".read_excel", ".read_table", ".load", ".save", ".dump", ".dumps")
)
```

```python
from typing import ClassVar

expected_artifacts: ClassVar[list[str]] = []
```

Apply `ClassVar[list[str]]` to each provider `expected_artifacts`. Use
`[head, sep, *rows_md]` for the remaining teaser `RUF005` finding.

Re-run the changed-path subset assertion from Step 1 after manual edits. It need not equal all 11
allowed paths, but no path outside the task manifest may be modified.

- [ ] **Step 3: Verify lint and affected tests**

```bash
.venv/bin/python -m ruff check src tests scripts
.venv/bin/python -m pytest -q \
  tests/test_run_stats.py \
  tests/peerassist/test_mcp_registry.py \
  tests/peerassist/test_ocr_providers.py \
  tests/stages/test_execution.py \
  tests/stages/test_teaser.py
```

Expected: Ruff prints `All checks passed!`; tests pass with only platform-specific skips.

- [ ] **Step 4: Commit the lint baseline**

```bash
git add -- \
  src/agent_runtime/agent_tools.py \
  src/common/run_stats.py \
  src/common/state.py \
  src/fact_generation/execution/tools/task_infer.py \
  src/peerassist/mcp_registry.py \
  src/peerassist/ocr_providers.py \
  src/preprocessing/parse/stage_runner.py \
  src/review/teaser/teaser.py \
  tests/peerassist/test_capabilities.py \
  tests/peerassist/test_concerns.py \
  tests/peerassist/test_deterministic_checks.py \
  tests/peerassist/test_eval_cli.py
# Run the staging gate with exactly the paths above.
git commit -m "style: establish clean Ruff baseline"
```

### Task 4: Add Durable Guidance And Architecture Records

**Files:**
- Create: `AGENTS.md`
- Create: `CONTRIBUTING.md`
- Create: `SECURITY.md`
- Create: `docs/product/peerassist-platform-prd.md`
- Create: `docs/api/conventions.md`
- Create: `docs/adr/0001-progressive-modular-monolith.md`
- Create: `docs/adr/0002-provider-ports.md`
- Create: `docs/README.md`
- Test: `tests/repository/test_repository_guidance.py`

- [ ] **Step 1: Write the failing guidance test**

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_required_repository_guidance_exists() -> None:
    required = {
        "AGENTS.md": ("## Verification", "## Security Boundaries", "## Definition Of Done"),
        "CONTRIBUTING.md": ("## Development Setup", "## Pull Request Checks"),
        "SECURITY.md": ("## Supported Deployment Boundary", "## Confidential Manuscripts"),
        "docs/product/peerassist-platform-prd.md": ("## Product Goal", "## Non-Goals"),
        "docs/api/conventions.md": ("## Tenancy", "## Errors", "## Idempotency"),
        "docs/adr/0001-progressive-modular-monolith.md": ("Status: Accepted",),
        "docs/adr/0002-provider-ports.md": ("Status: Accepted",),
    }
    for relative, markers in required.items():
        text = (ROOT / relative).read_text(encoding="utf-8")
        for marker in markers:
            assert marker in text, f"{relative} is missing {marker}"
```

- [ ] **Step 2: Run it to verify failure**

```bash
.venv/bin/python -m pytest -q tests/repository/test_repository_guidance.py
```

Expected: FAIL because files are absent.

- [ ] **Step 3: Write concise guidance**

`AGENTS.md` stays under about 150 lines and contains repository map, supported commands,
verification, architecture boundaries, security boundaries, Feishu synchronization, and
definition of done. Required rules: preserve dirty changes; no manuscript/credential/private
provider data in Git; TDD for behavior; canonical `verify_repository.py all`; provider-free
domain modules; explicit external-transfer approval; stable Feishu section updates rather than
dated append logs.

Write human contribution/security guides. Derive the PRD and ADRs from the approved spec without
duplicating it. API conventions freeze `/api/v1`, tenant-safe `403/404`, the error envelope,
idempotency, aggregate version, event cursor, and compatibility rules.

- [ ] **Step 4: Link and verify documentation**

Add new files to `docs/README.md`, then run:

```bash
.venv/bin/python -m pytest -q tests/repository/test_repository_guidance.py
```

Expected: `1 passed`.

- [ ] **Step 5: Commit guidance**

```bash
git add -- AGENTS.md CONTRIBUTING.md SECURITY.md \
  docs/product/peerassist-platform-prd.md \
  docs/api/conventions.md \
  docs/adr/0001-progressive-modular-monolith.md \
  docs/adr/0002-provider-ports.md \
  docs/README.md \
  tests/repository/test_repository_guidance.py
# Run the staging gate with exactly the paths above.
git commit -m "docs: establish PeerAssist engineering guidance"
```

## Chunk 2: Automate Repository Policy And CI

### Task 5: Add Documentation And Secret Checks

**Files:**
- Create: `scripts/check_docs.py`
- Create: `scripts/check_secrets.py`
- Create: `tests/repository/test_check_docs.py`
- Create: `tests/repository/test_check_secrets.py`

- [ ] **Step 1: Write failing link-check tests**

```python
from pathlib import Path

from scripts.check_docs import find_broken_links


def test_find_broken_links_ignores_remote_and_checks_local(tmp_path: Path) -> None:
    (tmp_path / "ok.md").write_text("# OK\n", encoding="utf-8")
    source = tmp_path / "README.md"
    source.write_text(
        "[ok](ok.md) [section](ok.md#ok) [web](https://example.com) [bad](missing.md)",
        encoding="utf-8",
    )
    assert find_broken_links(tmp_path, [source]) == [(source, "missing.md")]
```

- [ ] **Step 2: Write failing redacted-secret tests**

```python
from pathlib import Path

from scripts.check_secrets import scan_files


def test_scan_files_reports_location_without_secret_value(tmp_path: Path) -> None:
    secret = "sk-" + "A" * 48
    source = tmp_path / "bad.txt"
    source.write_text(f"token={secret}\n", encoding="utf-8")
    findings = scan_files([source])
    assert len(findings) == 1
    assert findings[0].path == source
    assert findings[0].line == 1
    assert secret not in findings[0].render()
```

- [ ] **Step 3: Run both tests to verify failure**

```bash
.venv/bin/python -m pytest -q \
  tests/repository/test_check_docs.py \
  tests/repository/test_check_secrets.py
```

Expected: collection fails because scripts are absent.

- [ ] **Step 4: Implement `check_docs.py`**

Expose:

```python
def find_broken_links(root: Path, files: list[Path]) -> list[tuple[Path, str]]: ...
def main(argv: list[str] | None = None) -> int: ...
```

Scan tracked Markdown by default. Ignore remote/mail/fragment links, images, and fenced code;
URL-decode targets, strip fragments, reject repository escapes, print `path: target`, exit 1 on
failure.

- [ ] **Step 5: Implement `check_secrets.py`**

Expose:

```python
@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    rule: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: possible secret ({self.rule})"


def scan_files(files: Iterable[Path]) -> list[Finding]: ...
def main(argv: list[str] | None = None) -> int: ...
```

Scan tracked/non-ignored files under 2 MiB. Skip `.git`, binaries, LFS pointers, generated
frontend assets, and explicit test fixtures. Detect GitHub token shapes, OpenAI-style keys, PEM
private-key headers, AWS access IDs, and non-placeholder password assignments. Never print the
matched value.

- [ ] **Step 6: Run tests and scans**

```bash
.venv/bin/python -m pytest -q \
  tests/repository/test_check_docs.py \
  tests/repository/test_check_secrets.py
.venv/bin/python scripts/check_docs.py
.venv/bin/python scripts/check_secrets.py
```

Expected: tests and scans pass. If a real credential appears, report only rule/path/line and
rotate/remove it before continuing.

- [ ] **Step 7: Commit policy checks**

```bash
git add -- scripts/check_docs.py scripts/check_secrets.py \
  tests/repository/test_check_docs.py \
  tests/repository/test_check_secrets.py
# Run the staging gate with exactly the paths above.
git commit -m "ci: add documentation and secret policy checks"
```

### Task 6: Add One Verification Entry Point

**Files:**
- Create: `scripts/verify_repository.py`
- Create: `tests/repository/test_verify_repository.py`
- Create: `Makefile`
- Modify: `CONTRIBUTING.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Write the failing plan test**

```python
from scripts.verify_repository import command_plan


def test_fast_plan_is_stable_and_offline() -> None:
    assert command_plan("fast") == [
        ["python", "-m", "ruff", "check", "src", "tests", "scripts"],
        ["python", "-m", "pytest", "-q"],
        ["python", "scripts/check_docs.py"],
        ["python", "scripts/check_secrets.py"],
        ["npm", "run", "build", "--prefix", "web/peerassist-workspace"],
    ]


def test_all_plan_adds_dependency_and_dist_checks() -> None:
    plan = command_plan("all")
    assert ["python", "-m", "pip", "check"] in plan
    assert ["git", "diff", "--exit-code", "--", "web/peerassist-workspace/dist"] in plan
```

The dist assertion applies only to the clean M0 checkpoint: build there, require no diff against
its committed `dist`, and never compare against or import the dirty source worktree's generated
assets. The disposable user-UI build in Task 2 remains a separate regression signal.

- [ ] **Step 2: Run it to verify failure**

```bash
.venv/bin/python -m pytest -q tests/repository/test_verify_repository.py
```

Expected: import failure.

- [ ] **Step 3: Implement the runner**

Expose `fast`, `all`, and individual check names. Replace logical `python` with `sys.executable`,
stream output, stop at first failure, print check names, and never install or mutate. Supported
CLI:

```text
python scripts/verify_repository.py fast
python scripts/verify_repository.py all
python scripts/verify_repository.py python lint frontend docs secrets dependencies dist
```

- [ ] **Step 4: Add Make aliases**

```makefile
.PHONY: verify verify-all test lint frontend docs secrets compose-up compose-down

verify:
	python scripts/verify_repository.py fast

verify-all:
	python scripts/verify_repository.py all

test:
	python -m pytest -q

lint:
	python -m ruff check src tests scripts

frontend:
	npm run build --prefix web/peerassist-workspace

docs:
	python scripts/check_docs.py

secrets:
	python scripts/check_secrets.py

compose-up:
	docker compose -f infrastructure/compose/compose.yml up --build

compose-down:
	docker compose -f infrastructure/compose/compose.yml down
```

- [ ] **Step 5: Verify and document**

```bash
.venv/bin/python -m pytest -q tests/repository/test_verify_repository.py
.venv/bin/python scripts/verify_repository.py fast
```

Expected: focused tests and every fast check pass. Make `all` canonical in `AGENTS.md` and
`CONTRIBUTING.md`.

- [ ] **Step 6: Commit**

```bash
git add -- scripts/verify_repository.py tests/repository/test_verify_repository.py Makefile \
  AGENTS.md CONTRIBUTING.md
# Run the staging gate with exactly the paths above.
git commit -m "ci: add unified repository verification"
```

### Task 7: Add Continuous Integration

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `scripts/bootstrap_smoke.sh`
- Modify: `README.md`
- Test: `tests/repository/test_ci_contract.py`

- [ ] **Step 1: Write the failing workflow test**

```python
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_ci_runs_python_frontend_and_policy_checks() -> None:
    path = ROOT / ".github/workflows/ci.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    assert {"python", "frontend", "repository-policy", "clean-checkout-smoke"} <= set(jobs)
    assert jobs["python"]["strategy"]["matrix"]["python-version"] == ["3.11", "3.12"]
    rendered = path.read_text(encoding="utf-8")
    for command in (
        "python -m ruff check src tests scripts",
        "python -m pytest -q",
        "npm ci",
        "npm run build",
        "python scripts/check_docs.py",
        "python scripts/check_secrets.py",
        "bash scripts/bootstrap_smoke.sh",
    ):
        assert command in rendered
```

- [ ] **Step 2: Run it to verify failure**

```bash
.venv/bin/python -m pytest -q tests/repository/test_ci_contract.py
```

Expected: missing workflow failure.

- [ ] **Step 3: Create `.github/workflows/ci.yml`**

Trigger pull requests and pushes to `peerassist-mvp`/`main`; use read-only permissions,
checkout v4 with `lfs: true`, setup-python v5, Python 3.11/3.12, and install
`.[runtime,positioning,refcheck,dev]`. Run Ruff/default pytest. In a Node 24 job run `npm ci`,
build, and dist drift. Run docs/secrets separately. Upload no manuscripts, auth state, `.env`,
or runtime data.

Add `clean-checkout-smoke` on Ubuntu with LFS checkout and Docker Compose. It runs only:

```bash
bash scripts/bootstrap_smoke.sh
```

The script is the single outer/inner acceptance entry point. On normal invocation it requires the
source checkout itself to be clean, creates a temporary `git clone --local --no-hardlinks` at the
same commit, then runs a restricted `git lfs fetch origin <commit>
--include=demos/Text/bert/paper.pdf` and `git lfs checkout demos/Text/bert/paper.pdf` inside that clone,
then re-executes the cloned script with an internal guard variable. The guarded inner phase must
refuse recursion, verify the fresh clone commit/status and real `%PDF-`, create `.venv`, install
the declared development extras, run `npm ci`, run canonical `verify_repository.py all`, start
Compose with cleanup trapped on exit, wait for health, download `Range: bytes=0-1023` into a
temporary body/header pair, assert status `206`, `Content-Range: bytes 0-1023/<size>`, body length
1024, and exact equality with the first 1024 bytes of the demo PDF. It must never print environment
secrets and must remove the temporary clone and all response files. Thus CI and local acceptance
both exercise clone, LFS, install, canonical verification, Compose, and real Range bytes with the
single command shown above.

- [ ] **Step 4: Run contract and local equivalent**

```bash
.venv/bin/python -m pytest -q tests/repository/test_ci_contract.py
.venv/bin/python scripts/verify_repository.py all
bash -n scripts/bootstrap_smoke.sh
```

Expected: contract and all checks pass.

- [ ] **Step 5: Document and commit**

Add a concise development-status section to `README.md` linking `CONTRIBUTING.md` and the
canonical command. Do not add a badge before remote workflow execution.

```bash
git add -- .github/workflows/ci.yml scripts/bootstrap_smoke.sh \
  tests/repository/test_ci_contract.py README.md
# Run the staging gate with exactly the paths above.
git commit -m "ci: validate Python frontend and repository policy"
```

## Chunk 3: Secure Runtime, Compose, And Compatibility Contracts

### Task 8: Unify Workspace Runtime Configuration

**Files:**
- Modify: `src/common/config.py`
- Modify: `src/peerassist/model_review.py`
- Modify: `src/peerassist/confirmation_server.py`
- Modify: `.env.example`
- Test: `tests/test_pipeline_config.py`
- Test: `tests/peerassist/test_model_review.py`
- Test: `tests/peerassist/test_confirmation_server.py`

- [ ] **Step 1: Write failing typed-settings tests**

Clear `get_settings.cache_clear()` around environment changes and assert safe inactive defaults:

```python
def test_workspace_defaults_are_local_and_inactive(monkeypatch) -> None:
    for name in (
        "PEERASSIST_OPENAI_API_KEY",
        "PEERASSIST_OPENAI_BASE_URL",
        "PEERASSIST_OPENAI_MODEL",
        "PEERASSIST_REVIEW_API_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.peerassist_openai_api_key is None
    assert settings.peerassist_openai_base_url == "https://api.openai.com/v1"
    assert settings.peerassist_openai_model == "gpt-5"
    assert settings.peerassist_review_api_url == "http://127.0.0.1:8767"
```

Add a confirmation-server test that sets `PEERASSIST_REVIEW_API_URL=http://review-api:8767`
and verifies the proxy-base resolver returns it.

- [ ] **Step 2: Run focused tests to verify failure**

```bash
.venv/bin/python -m pytest -q \
  tests/test_pipeline_config.py \
  tests/peerassist/test_model_review.py \
  tests/peerassist/test_confirmation_server.py
```

Expected: missing fields/resolver failures.

- [ ] **Step 3: Add typed settings**

Add these fields to `Settings` with `AliasChoices`:

```python
peerassist_openai_api_key: str | None = Field(
    default=None,
    validation_alias=AliasChoices(
        "PEERASSIST_OPENAI_API_KEY", "EXECUTION_OPENAI_API_KEY", "OPENAI_API_KEY"
    ),
)
peerassist_openai_base_url: str = Field(
    default="https://api.openai.com/v1",
    validation_alias=AliasChoices(
        "PEERASSIST_OPENAI_BASE_URL", "EXECUTION_OPENAI_BASE_URL", "OPENAI_BASE_URL"
    ),
)
peerassist_openai_model: str = Field(
    default="gpt-5",
    validation_alias=AliasChoices(
        "PEERASSIST_OPENAI_MODEL", "EXECUTION_OPENAI_MODEL", "OPENAI_MODEL"
    ),
)
peerassist_openai_timeout_seconds: float = Field(
    default=240.0,
    gt=0,
    validation_alias=AliasChoices("PEERASSIST_OPENAI_TIMEOUT_SECONDS"),
)
peerassist_review_api_url: str = Field(
    default="http://127.0.0.1:8767",
    validation_alias=AliasChoices("PEERASSIST_REVIEW_API_URL"),
)
```

Normalize the Review URL once and reject non-HTTP(S), embedded credentials, query, and fragment.
Use typed fields from `model_review.py` and `confirmation_server.py`; remove the `deepkey.top`
code default.

- [ ] **Step 4: Make proxy base injectable**

```python
def _review_api_base_url() -> str:
    return get_settings().peerassist_review_api_url.rstrip("/")
```

Use `f"{_review_api_base_url()}{self.path}"` in the proxy.

- [ ] **Step 5: Update `.env.example`**

```dotenv
PEERASSIST_REVIEW_API_URL=http://127.0.0.1:8767
# PEERASSIST_OPENAI_API_KEY=
PEERASSIST_OPENAI_BASE_URL=https://api.openai.com/v1
PEERASSIST_OPENAI_MODEL=gpt-5
PEERASSIST_OPENAI_TIMEOUT_SECONDS=240
```

- [ ] **Step 6: Verify and commit**

```bash
.venv/bin/python -m pytest -q \
  tests/test_pipeline_config.py \
  tests/peerassist/test_model_review.py \
  tests/peerassist/test_confirmation_server.py
.venv/bin/python scripts/verify_repository.py fast
git add -- src/common/config.py src/peerassist/model_review.py \
  src/peerassist/confirmation_server.py .env.example \
  tests/test_pipeline_config.py \
  tests/peerassist/test_model_review.py \
  tests/peerassist/test_confirmation_server.py
# Run the staging gate with exactly the paths above.
git commit -m "refactor: centralize secure workspace configuration"
```

Expected: focused and fast checks pass; commit contains no real key.

### Task 9: Add A Reproducible Two-Service Compose Profile

**Files:**
- Create: `infrastructure/compose/Dockerfile`
- Create: `infrastructure/compose/compose.yml`
- Create: `infrastructure/compose/README.md`
- Create: `.dockerignore`
- Create: `tests/repository/test_compose_contract.py`
- Modify: `Makefile`
- Create: `docs/development.md`

- [ ] **Step 1: Write the failing Compose contract**

```python
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_compose_exposes_only_workspace_on_loopback() -> None:
    payload = yaml.safe_load(
        (ROOT / "infrastructure/compose/compose.yml").read_text(encoding="utf-8")
    )
    services = payload["services"]
    assert set(services) == {"review-api", "workspace"}
    assert "ports" not in services["review-api"]
    assert services["workspace"]["ports"] == ["127.0.0.1:8766:8766"]
    assert services["workspace"]["environment"]["PEERASSIST_REVIEW_API_URL"] == (
        "http://review-api:8767"
    )
    assert services["workspace"]["depends_on"]["review-api"]["condition"] == (
        "service_healthy"
    )
```

- [ ] **Step 2: Run it to verify failure**

```bash
.venv/bin/python -m pytest -q tests/repository/test_compose_contract.py
```

Expected: missing file failure.

- [ ] **Step 3: Create a non-root image**

Use `python:3.12-slim`, UID/GID 10001 `peerassist`, install
`.[runtime,positioning,refcheck]`, create `/var/lib/peerassist`, set `PYTHONPATH=/app/src`, and
run as `peerassist`. During build, verify `demos/Text/bert/paper.pdf` starts with `%PDF-`; fail
with a clear `git lfs pull` message otherwise.

- [ ] **Step 4: Create Compose services**

`review-api` runs:

```text
python -m peerassist.review_job_api --data-dir /var/lib/peerassist --host 0.0.0.0 --port 8767
```

It has no host port, uses a named volume, and has a Python/urllib healthcheck.

`workspace` runs:

```text
python -m peerassist.confirmation_server --run-dir /app/demos/Text/bert --paper-id bert --host 0.0.0.0 --port 8766
```

It maps only `127.0.0.1:8766:8766`, points to `http://review-api:8767`, and waits for health.

- [ ] **Step 5: Add ignore rules and instructions**

`.dockerignore` excludes Git, venvs, caches, runs, data, node_modules, auth files, and `.env`,
but retains tracked frontend dist and demo LFS objects. Document:

```bash
git lfs pull
docker compose -f infrastructure/compose/compose.yml up --build -d
curl http://127.0.0.1:8766/api/health
curl -sS -D /tmp/peerassist-range.headers \
  -H 'Range: bytes=0-1023' -o /tmp/peerassist-range.body \
  http://127.0.0.1:8766/paper.pdf
docker compose -f infrastructure/compose/compose.yml down
```

- [ ] **Step 6: Verify structure and runtime**

```bash
.venv/bin/python -m pytest -q tests/repository/test_compose_contract.py
docker compose -f infrastructure/compose/compose.yml config --quiet
docker compose -f infrastructure/compose/compose.yml up --build -d
curl --fail http://127.0.0.1:8766/api/health
curl --fail -sS -D /tmp/peerassist-range.headers \
  -H 'Range: bytes=0-1023' -o /tmp/peerassist-range.body \
  http://127.0.0.1:8766/paper.pdf
test "$(wc -c < /tmp/peerassist-range.body)" -eq 1024
head -c 1024 demos/Text/bert/paper.pdf | cmp - /tmp/peerassist-range.body
rg -i '^HTTP/[^ ]+ 206' /tmp/peerassist-range.headers
rg -i '^Content-Range: bytes 0-1023/[0-9]+' /tmp/peerassist-range.headers
docker compose -f infrastructure/compose/compose.yml down
```

Expected: test/config pass; health 200; actual Range body is the exact first 1024 PDF bytes with
206 and correct `Content-Range`. If Docker is unavailable, record an
unverified external prerequisite and do not claim Compose acceptance.

- [ ] **Step 7: Commit Compose baseline**

```bash
git add -- infrastructure/compose/Dockerfile infrastructure/compose/compose.yml \
  infrastructure/compose/README.md .dockerignore Makefile docs/development.md \
  tests/repository/test_compose_contract.py
# Run the staging gate with exactly the paths above.
git commit -m "build: add secure two-service development profile"
```

### Task 10: Parameterize Systemd And Freeze Legacy HTTP Contracts

**Files:**
- Modify: `deploy/systemd/peerassist-review-api.service`
- Modify: `deploy/systemd/peerassist-ui.service`
- Create: `deploy/systemd/peerassist.env.example`
- Create: `deploy/systemd/smoke_systemd.sh`
- Create: `tests/repository/test_deployment_contract.py`
- Create: `tests/fixtures/contracts/public_test.pdf`
- Create: `tests/fixtures/contracts/review_job.v1.json`
- Create: `tests/fixtures/contracts/concern.v1.json`
- Create: `tests/fixtures/contracts/citation.v1.json`
- Create: `tests/fixtures/contracts/finalized_report.v1.json`
- Create: `tests/fixtures/contracts/legacy_http_contract.v1.json`
- Create: `tests/contracts/test_legacy_http_contract.py`
- Modify: `docs/peerassist_operation_manual.md`

- [ ] **Step 1: Write failing deployment tests**

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_systemd_examples_are_parameterized_and_private() -> None:
    for service in ("peerassist-review-api.service", "peerassist-ui.service"):
        text = (ROOT / "deploy/systemd" / service).read_text(encoding="utf-8")
        assert "EnvironmentFile=/etc/peerassist/peerassist.env" in text
        assert "User=peerassist" in text
        assert "0.0.0.0" not in text
        assert "/root/peerassist-review-system-20260710" not in text
        assert "WorkingDirectory=/opt/peerassist" in text
        assert "ExecStart=/opt/peerassist/.venv/bin/python" in text
        assert "${PEERASSIST_" not in text
```

- [ ] **Step 2: Define the failing route snapshot test**

Commit a tiny public synthetic PDF whose bytes and SHA-256 are fixed, plus exact JSON fixtures for
one persisted ReviewJob, concern, citation, and finalized report. The JSON snapshot lists method,
path template, owner, request fixture, exact status, required normalized fields/headers, mutation
class, idempotency, and expected fixture/digest for:

- workspace `/`, `/paper`, bootstrap/state/events, PDF Range;
- health, upload, review create, jobs/detail/events/workspace;
- decisions, consent, cancel, retry, finalize, artifact list/download.

The test copies the committed fixtures into temporary repositories before starting ephemeral real
legacy servers. Disable background scheduling and inject a no-op external runner so no route can
race a worker or call the network. Fixed vectors must cover:

- PDF full response and `bytes=0-31`: exact status, `Content-Length`, `Content-Range`, SHA-256,
  and exact body bytes;
- upload and create: normalized response fields plus exact stored paper digest;
- concern decision and consent: aggregate revision increments, append-only event cursor, and exact
  open/confirmed/resolved transitions;
- cancel and retry: deterministic allowed/denied transitions without a scheduler;
- finalize: exact terminal ReviewJob state, immutable report digest, citation/evidence references,
  and byte-identical artifact download;
- repeated idempotent requests: same resource/digest and no duplicate event.

Only generated IDs, RFC3339 timestamps, bound ports, and temporary filesystem roots may be
normalized. Never normalize status, aggregate revision, event cursor, hashes, byte ranges,
concern transitions, citation targets, or terminal state. No test may use a private manuscript,
wall-clock race, model provider, or network service.

- [ ] **Step 3: Run tests to verify failure**

```bash
.venv/bin/python -m pytest -q \
  tests/repository/test_deployment_contract.py \
  tests/contracts/test_legacy_http_contract.py
```

Expected: missing environment template/snapshot and hard-coded services fail.

- [ ] **Step 4: Parameterize systemd**

Use the literal `WorkingDirectory=/opt/peerassist` and literal
`ExecStart=/opt/peerassist/.venv/bin/python`, `User=peerassist`, `Group=peerassist`, `UMask=0077`,
`EnvironmentFile=/etc/peerassist/peerassist.env`, and literal loopback hosts/data/run paths in
arguments. Do not put `${PEERASSIST_*}` in `WorkingDirectory`, `ExecStart` executable paths, or
concatenated arguments: systemd does not perform shell-style expansion there. Add
`NoNewPrivileges=true`, `PrivateTmp=true`, `ProtectSystem=strict`, and explicit writable data/run
paths such as `ReadWritePaths=/var/lib/peerassist`. Keep 8767 private.

The example environment contains no secrets:

```dotenv
PEERASSIST_REVIEW_API_URL=http://127.0.0.1:8767
```

Run static validation, then actual start smoke. `smoke_systemd.sh` must first require a systemd
host and refuse to proceed if the `peerassist` user/group, `/opt/peerassist`,
`/etc/peerassist/peerassist.env`, `/var/lib/peerassist`, either named unit, or either port already
exists/is active. It then creates the disposable `peerassist` system identity, links the clean
checkout at `/opt/peerassist`, creates the literal environment/data/run paths expected by the
unit, and copies the two delivered unit files byte-for-byte into `/run/systemd/system/` under their
actual names. After `systemctl daemon-reload`, it starts
`peerassist-review-api.service` and `peerassist-ui.service` themselves, inspects their status,
waits for both loopback health endpoints, then stops them. A trap must remove the copied units,
daemon-reload, identity, link, and created environment/data files. It must never replace an
existing resource or start a reassembled/transient equivalent.

```bash
systemd-analyze verify \
  deploy/systemd/peerassist-review-api.service \
  deploy/systemd/peerassist-ui.service
sudo bash deploy/systemd/smoke_systemd.sh
```

Expected: static units verify and the actual delivered units serve health using the temporary
environment. On a non-systemd host, static verification remains required and actual-start
acceptance stays explicitly unverified; do not claim systemd acceptance.

- [ ] **Step 5: Implement and verify the route snapshot**

Normalize only IDs, timestamps, ports, and filesystem roots. Never normalize status, revisions,
hashes, Range bytes, concern transitions, or terminal ReviewJob state.

```bash
.venv/bin/python -m pytest -q \
  tests/repository/test_deployment_contract.py \
  tests/contracts/test_legacy_http_contract.py
```

Expected: all pass.

- [ ] **Step 6: Document and commit**

Document loopback binding, reverse-proxy requirement, environment file, and lack of built-in
authentication.

```bash
git add -- deploy/systemd/peerassist-review-api.service \
  deploy/systemd/peerassist-ui.service \
  deploy/systemd/peerassist.env.example \
  deploy/systemd/smoke_systemd.sh \
  docs/peerassist_operation_manual.md \
  tests/repository/test_deployment_contract.py \
  tests/contracts/test_legacy_http_contract.py \
  tests/fixtures/contracts/public_test.pdf \
  tests/fixtures/contracts/review_job.v1.json \
  tests/fixtures/contracts/concern.v1.json \
  tests/fixtures/contracts/citation.v1.json \
  tests/fixtures/contracts/finalized_report.v1.json \
  tests/fixtures/contracts/legacy_http_contract.v1.json
# Run the staging gate with exactly the paths above.
git commit -m "test: freeze secure legacy deployment contracts"
```

### Task 11: Final Verification And Stable Feishu Sync

**Files:**
- Create or modify in the clean worktree only: `docs/system_review_2026-07-17.md`
- Create or modify in the clean worktree only: `docs/peerassist_lark_sync.md`
- Modify in Feishu: `XuVIdkaGgoykehxox9Kc3Qnhnw2`

- [ ] **Step 1: Run canonical verification**

```bash
git status --short
.venv/bin/python scripts/verify_repository.py all
```

Expected: clean M0 worktree, all checks exit 0, and no dist drift. The dirty source worktree is
not the canonical verification target.

- [ ] **Step 2: Run one-command clean-checkout, Compose, and source-safety acceptance**

```bash
bash scripts/bootstrap_smoke.sh

cd /root/peerassist-review-system-20260710/peerassist
git status --porcelain=v1 -z > /tmp/peerassist-m0-source/status.final.z
git ls-files -co --exclude-standard -z | sort -z \
  | while IFS= read -r -d '' path; do
      if test -f "${path}"; then sha256sum -- "${path}"; fi
    done > /tmp/peerassist-m0-source/files.final.sha256
cmp /tmp/peerassist-m0-source/status.before.z \
  /tmp/peerassist-m0-source/status.final.z
cmp /tmp/peerassist-m0-source/files.before.sha256 \
  /tmp/peerassist-m0-source/files.final.sha256
```

Expected: that single command creates the fresh clone, performs restricted LFS materialization,
installs dependencies, passes canonical verification, starts Compose, and checks the real PDF and
actual Range bytes. Original user worktree status and every file hash remain unchanged. This is
the documented one-command bootstrap acceptance.

- [ ] **Step 3: Update local records with fresh evidence**

Write or replace stale verification in `docs/system_review_2026-07-17.md` with exact M0
commands/counts. Update the stable index in `docs/peerassist_lark_sync.md`; do not import the
source worktree's uncommitted versions and do not add micro-change headings.

- [ ] **Step 4: Update Feishu surgically**

Use `lark-doc` as user with an optimistic concurrency guard:

1. Fetch the outline with IDs and record revision `R`; assert exactly ten `h2` sections and exactly
   one block ID for each of “三、当前工程基线”, “八、迁移路线与验收”, and “十、当前行动项”.
2. Fetch each section at revision `R` with `--scope section --detail full`. Record its heading ID,
   ordered top-level body IDs, and any resource blocks. Prepare a replacement for the section body,
   not for the heading; all images, files, whiteboards, sheets, citations, and synced references
   must be preserved unchanged.
3. Immediately re-fetch the outline and abort without writing unless its revision is still `R`
   and all three heading IDs are unchanged and unique.
4. Update one section at a time. With the current revision, `block_replace` the first ordinary
   text/list body block with the complete new body, leaving the heading and resource blocks in
   place. Re-fetch that section at the returned revision, then delete only the now-obsolete
   ordinary body blocks by their freshly fetched IDs. Never delete the heading or a resource
   block. Pass the exact current `--revision-id` to every write and abort on drift or a partial
   result. Re-fetch the outline after each section and locate the next unique heading by title;
   do not assume body or replacement block IDs survive.
5. Mark M0 complete only if canonical, fresh-clone, Compose, PDF/Range, and applicable systemd
   checks passed. Record exact evidence and branch/commit without credentials, manuscript text,
   provider responses, or private absolute paths. Do not append a dated section.
6. Re-fetch the final outline and all three sections; assert exactly ten stable sections, unique
   fresh block IDs, expected text, and final revision equal to the last returned write revision.

Any revision drift indicates concurrent editing: stop the sync, preserve local M0 evidence, and
report the conflict rather than retrying against `-1` or overwriting user changes.

- [ ] **Step 5: Commit evidence and review branch**

```bash
git add -- docs/system_review_2026-07-17.md docs/peerassist_lark_sync.md
# Run the staging gate with exactly the paths above.
git commit -m "docs: record verified M0 engineering baseline"
git log --oneline --decorate e8f85ee..HEAD
git diff --stat e8f85ee..HEAD
git status --short
```

Expected: scoped commits only; no credentials, runtime data, private paper, or unrelated user
change. Request code review against `e8f85ee` before merge.

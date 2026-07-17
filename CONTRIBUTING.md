# Contributing to PeerAssist

PeerAssist handles research manuscripts and review evidence. Contributions must preserve confidentiality, reproducibility, and human control as well as functional correctness.

## Development Setup

1. Use Python 3.11 or 3.12 and create a virtual environment.
2. Install the project with its development dependencies from `pyproject.toml`.
3. For the current workspace UI, install the locked packages with `npm ci` in `web/peerassist-workspace`.
4. Keep local credentials in environment variables or an ignored local file. Use synthetic papers and provider responses in tests.
5. Confirm the baseline with focused tests before changing behavior.

Typical checks are:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests scripts
cd web/peerassist-workspace && npm ci && npm run build
```

Develop behavior test-first. Observe the focused test fail for the intended reason, implement the smallest coherent change, and keep existing user changes intact.

## Pull Request Checks

Before opening or updating a pull request:

- explain the user-facing outcome, architecture impact, migrations, and rollback path;
- include tests for behavior, authorization, compatibility, and failure handling as applicable;
- run focused tests, Ruff, and relevant frontend checks;
- run `python scripts/verify_repository.py all` as the canonical final gate once available;
- review both the working-tree diff and staged diff for scope and generated artifacts;
- update PRD, API conventions, ADRs, and operations guidance when contracts change;
- disclose skipped or gated checks and the reason;
- verify that no manuscript, credential, provider private response, private endpoint, or production data is committed.

Do not combine unrelated refactors with a behavior change. A provider integration must implement a domain port and its shared contract suite; it must not introduce provider-specific concepts into domain services.

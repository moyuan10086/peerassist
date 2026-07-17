# PeerAssist Agent Guide

This file defines the repository-wide working agreement for humans and coding Agents. More specific guidance may narrow these rules but must not weaken security or verification requirements.

## Repository Map

- `src/peerassist/`: framework-independent review, evidence, concern, approval, and job domain code.
- `src/common/`: shared configuration, state, storage, and run accounting.
- `src/agent_runtime/`: Agent orchestration and tool adapters.
- `src/fact_generation/`, `src/preprocessing/`, `src/review/`: pipeline stages and application services.
- `web/peerassist-workspace/`: current React/Vite review workspace and compatibility UI.
- `tests/`: unit, integration, stage, and repository-contract tests.
- `scripts/`: supported operational and repository verification entrypoints.
- `docs/`: product requirements, API conventions, ADRs, design records, and operations guidance.
- `deploy/`: deployment adapters; never treat deployment configuration as domain policy.

## Supported Commands

Use the repository virtual environment and locked frontend dependencies:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests scripts
cd web/peerassist-workspace && npm ci && npm run build
```

Run the narrowest relevant test while developing, then the repository verification gate before handoff. Do not invent a second build or test entrypoint when a supported command exists.

## Verification

- Use test-driven development for every behavior change: add a focused failing test, observe the expected failure, implement the smallest change, and rerun it.
- Protect dirty changes. Inspect status before editing and never reset, overwrite, or reformat unrelated user work.
- Keep changes scoped. Review the full diff and staged diff; reject generated files, secrets, and unrelated cleanup.
- Run focused tests for changed behavior and the affected subsystem.
- Run Ruff and any relevant frontend build or contract check.
- The final canonical gate is `python scripts/verify_repository.py all`; the verifier runs every check from the repository root.
- Record skips, deselections, warnings, and unavailable external services in the handoff.

## Architecture Boundaries

- Domain code must not depend on provider implementations. Application services depend on explicit ports; providers implement them at the edge.
- Keep `src/peerassist` independent of FastAPI, Next.js, Supabase, and deployment-specific identity, database, or object-store details.
- PostgreSQL, OIDC, and S3-compatible services are replaceable adapters. Supabase is optional, not a domain primitive.
- Keep long-running review and Agent work outside request handlers.
- Preserve a single write authority per aggregate during migration. Compatibility layers may translate or proxy, but may not become a competing source of truth.
- Treat PDF reading as the primary review flow. The canvas is a relationship view over authoritative review entities and owns only presentation state.
- Agents call audited tools; they never write databases or object stores directly.
- Persisted schemas, API schemas, and generated clients have explicit versions and compatibility tests.

## Security Boundaries

- This alpha has no built-in authentication. Bind local services to loopback or place them behind a trusted authenticated reverse proxy.
- Never commit manuscripts, credentials, access tokens, cookies, private keys, provider private responses, or production data.
- Never place secrets in prompts, logs, fixtures, screenshots, reports, or browser state.
- Tenant and project authorization belongs in repository/query boundaries, not only in UI or route checks.
- Use safe synthetic fixtures. External model, OCR, retrieval, and Docker tests must not receive confidential material.
- Human approval is mandatory before external transmission of manuscript or evidence data.
- Human approval is mandatory before overwriting human-authored content.
- Human approval is mandatory before publishing a final report.
- Human approval is mandatory before sharing artifacts or results outside their authorized scope.
- Reversible drafting, evidence analysis, and canvas layout may proceed autonomously when audited and version checked.

## Feishu Synchronization

- Feishu is a synchronized communication surface, not the source of truth; repository documents remain canonical.
- Update the 10 stable chapters in place. Keep their headings and order stable so synchronization remains deterministic.
- Do not append date-stamped logs, daily progress sections, or duplicate snapshots to the document body.
- Update only the chapters affected by an approved repository change and preserve human-authored material outside the managed sections.
- Preview and compare changes before synchronization. Replacing human-authored content or sharing the result requires approval.
- Never send manuscripts, credentials, provider private responses, or other restricted data to Feishu without an explicit scoped approval.

## Definition Of Done

A change is complete only when:

- acceptance criteria are represented by executable tests or explicit documentation checks;
- the implementation respects domain, provider, tenancy, and approval boundaries;
- focused tests pass and the relevant subsystem has no unexplained regression;
- Ruff, frontend checks, and `python scripts/verify_repository.py all` pass when applicable;
- the complete diff and cached diff contain only intended files;
- documentation and API contracts match behavior;
- no manuscript, credential, private provider response, or generated secret enters Git;
- security-relevant assumptions and all skipped checks are disclosed in the handoff.

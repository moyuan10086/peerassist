# PeerAssist P0 Review Workspace Backend Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the authoritative backend for task-scoped model consent, server-owned review documents, evidence-linked teacher decisions, truthful asynchronous exports, and a teacher-facing workspace facade.

**Architecture:** Extend the existing modular monolith and provider ports. Reuse `Paper/PaperVersion`, `ReviewJob/ReviewEvent`, candidate finding Artifacts, `CommandRecord`, `WorkItem/Outbox`, the existing `report_versions` table, and deterministic Artifact publication. Add only `ExternalServiceConsent` and `ReviewDocument` as new authoritative aggregates; extend project membership with default-workspace metadata instead of adding a third aggregate.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLAlchemy Core, PostgreSQL/Alembic, pytest, React/Vite design-token export.

**Reuse boundary:** OpenReview informs the structured review-form API, Kotahi informs versioned document editing/export, and JOSS informs evidence/checklist transparency. OJS/Janeway remain external workflow targets. Do not import submission, assignment, publication, or role-management subsystems.

---

## Chunk 1: Design Contract and Authoritative Data

### Task 0: Establish the DESIGN.md Contract

**Files:**
- Create: `DESIGN.md`
- Create: `web/peerassist-workspace/design-tokens.json`
- Create: `scripts/check_design_md.py`
- Modify: `docs/superpowers/specs/2026-07-20-peerassist-editorial-workspace-ui-design.md`

- [ ] Write `DESIGN.md` using the official alpha schema. Use a light academic-editorial canvas, deep ink, restrained teal primary action, amber warning, semantic red/green/blue, 4–8px radii, stable dimensions, zero letter-spacing, and local Chinese system fonts. Document the selective Linear density/hairline and Notion document-hierarchy references without copying their dark/purple or pastel marketing palettes.
- [ ] Run `npx -y @google/design.md lint --format json DESIGN.md > /tmp/peerassist-design-lint.json` and verify the command exits 0.
- [ ] Implement `scripts/check_design_md.py` to parse the JSON findings and fail on any error, broken reference, or component contrast below WCAG AA; run `.venv/bin/python scripts/check_design_md.py /tmp/peerassist-design-lint.json` and expect exit 0.
- [ ] Export with `npx -y @google/design.md export --format dtcg DESIGN.md > web/peerassist-workspace/design-tokens.json`; run `.venv/bin/python -m json.tool web/peerassist-workspace/design-tokens.json >/dev/null`.
- [ ] Update the UI spec to make `DESIGN.md` normative while leaving current dirty CSS untouched.
- [ ] Commit only these files with `git commit -m "docs: define PeerAssist design system"`.

### Task 1: Add Consent, Review Document, and Default Workspace Contracts

**Files:**
- Modify: `src/peerassist/platform/models.py`
- Modify: `src/peerassist/platform/ports.py`
- Modify: `src/peerassist/platform/adapters/memory.py`
- Modify: `src/peerassist/platform/adapters/postgres_repositories.py`
- Test: `tests/platform/test_models.py`
- Test: `tests/platform/contracts/test_repository_contract.py`
- Test: `tests/platform/test_repository_ports.py`

- [ ] Write failing tests for consent statuses/generation/effectiveness, the fixed four-section document schema, independent document CAS, immutable finding/evidence references, and exactly one default active project membership.
- [ ] Run `.venv/bin/python -m pytest -q tests/platform/test_models.py tests/platform/contracts/test_repository_contract.py tests/platform/test_repository_ports.py -k 'consent or document or default_project'`; confirm failures are due to missing contracts.
- [ ] Add `ExternalServiceConsent`, `ReviewDocumentBlock`, and `ReviewDocument`; extend `ProjectMembership` with `is_default`. Add `ConsentRepository` and `ReviewDocumentRepository` ports and both repositories to `UnitOfWork`.
- [ ] Implement tenant-checked Memory repositories with CAS, atomic consent supersede/reapply, and one document per job. Update membership adapters so setting one default clears the previous default inside the same UoW.
- [ ] Re-run the focused tests and commit with `git commit -m "feat: add review workspace domain contracts"`.

### Task 2: Persist the New Contracts and Reuse Report Versions

**Files:**
- Modify: `src/peerassist/platform/models.py`
- Modify: `src/peerassist/platform/ports.py`
- Modify: `src/peerassist/platform/adapters/memory.py`
- Modify: `src/peerassist/platform/adapters/postgres_schema.py`
- Modify: `src/peerassist/platform/adapters/postgres_core.py`
- Modify: `src/peerassist/platform/adapters/postgres_review.py`
- Modify: `src/peerassist/platform/adapters/postgres.py`
- Create: `infrastructure/migrations/v0002_review_workspace.py`
- Create: `infrastructure/migrations/versions/0002_review_workspace.py`
- Modify: `tests/platform/test_migration_contract.py`
- Modify: `tests/platform/integration/test_postgres_schema.py`
- Modify: `tests/platform/integration/test_postgres_repository_contract.py`
- Modify: `tests/platform/integration/test_postgres_tenant_isolation.py`
- Modify: `tests/platform/contracts/test_repository_contract.py`

- [ ] Write failing metadata tests for `external_service_consents`, `review_documents`, `project_memberships.is_default`, tenant composite foreign keys, consent generation/current partial uniqueness, one document per job, and revision `0002_review_workspace`.
- [ ] Run `.venv/bin/python -m pytest -q tests/platform/test_migration_contract.py tests/platform/integration/test_postgres_schema.py -k 'consent or document or default or revision'` and confirm failure.
- [ ] Add live metadata and a frozen reversible v0002 migration. Store document sections and consent `data_scope` as JSONB. Add a partial unique index for the current consent and for one default membership per user/organization.
- [ ] Add PostgreSQL repositories with `SELECT ... FOR UPDATE`, versioned updates, and `_project_filter` on every operation. Add a `ReportVersion` model/port/repository for the already-existing table and bind it in both Memory and PostgreSQL UoWs; do not create another export aggregate. Extend repository contract tests so export behavior runs against Memory and PostgreSQL providers.
- [ ] Run focused PostgreSQL repository/isolation tests when the test database is available; otherwise report the environment limitation without claiming integration success.
- [ ] Commit with `git commit -m "feat: persist review workspace state"`.

## Chunk 2: Services, Model Gate, and Compatibility APIs

### Task 3: Add Authorized Consent, Document, and Finding Services

**Files:**
- Create: `src/peerassist/platform/services/review_workspace.py`
- Modify: `src/peerassist/platform/services/__init__.py`
- Modify: `src/peerassist/platform/models.py`
- Modify: `src/peerassist/platform/permissions.py`
- Test: `tests/platform/test_review_workspace_service.py`

- [ ] Write failing service tests for grant, deny, revoke, reapply generation, expiration, PaperVersion mismatch, document CAS, and atomic finding decision plus document projection.
- [ ] Run `.venv/bin/python -m pytest -q tests/platform/test_review_workspace_service.py` and confirm failure.
- [ ] Reuse `ReviewService._require_project_action`, command reservation, audit, and event append patterns. A finding decision validates exact lineage/finding/revision from `review_result.json`, appends the decision event, updates `ReviewDocument.base_decision_event_id`, and commits both atomically.
- [ ] Re-run service and permission tests; commit with `git commit -m "feat: add authorized review workspace services"`.

### Task 4: Version Model Settings and Enforce Consent at Execution Time

**Files:**
- Modify: `src/peerassist/model_settings.py`
- Modify: `src/peerassist/model_review.py`
- Modify: `services/api/routes/model_settings.py`
- Modify: `services/worker/main.py`
- Test: `tests/platform/test_model_settings_api.py`
- Test: `tests/platform/test_worker_service.py`
- Test: `tests/platform/test_permissions.py`

- [ ] Write failing tests for monotonic model-settings revision, enabled/disabled state, administrator-only writes/discovery, redacted reads, stable runtime snapshot, absent/denied/expired/superseded consent, wrong PaperVersion/policy/data scope, revoked-after-claim, and valid grant.
- [ ] Run `.venv/bin/python -m pytest -q tests/platform/test_model_settings_api.py tests/platform/test_worker_service.py -k 'revision or enabled or admin or consent'` and confirm failure.
- [ ] Extend the server-owned settings record with revision, enabled, policy version, and a public configuration identifier; keep keys server-only. Restrict mutation and discovery to organization administrators while ordinary teachers only consume availability.
- [ ] Split local document generation from external generation. Immediately before each model request, open a fresh UoW and validate current consent against job, PaperVersion, provider configuration revision, policy, data scope, status, expiry, and enabled state. Missing/denied consent returns explicit local fallback metadata, not job failure.
- [ ] Re-run focused tests and commit with `git commit -m "fix: gate review models on versioned task consent"`.

### Task 5: Migrate Legacy Consent and Draft Facts Conservatively

**Files:**
- Create: `src/peerassist/platform/services/review_workspace_migration.py`
- Modify: `src/peerassist/platform/services/legacy.py`
- Modify: `services/api/routes/legacy.py`
- Test: `tests/platform/test_review_workspace_migration.py`
- Test: `tests/platform/test_legacy_api.py`

- [ ] Write failing tests for unique content-digest mapping from legacy `paper_id` to one `PaperVersion`, complete still-enabled consent import, draft event import, duplicate/ambiguous mapping, incomplete fields, disabled provider, and already-migrated idempotency.
- [ ] Run `.venv/bin/python -m pytest -q tests/platform/test_review_workspace_migration.py tests/platform/test_legacy_api.py -k workspace` and confirm failure.
- [ ] Implement an administrator-triggered idempotent migration. Import only uniquely mapped and complete records; otherwise leave the job blocked and emit `consent_reauthorization_required`. Mark the platform aggregates authoritative and legacy data read-only after cutover.
- [ ] Re-run tests and commit with `git commit -m "feat: migrate legacy review workspace facts"`.

### Task 6: Expose Compatible and Teacher Aggregate APIs

**Files:**
- Create: `services/api/routes/review_workspace.py`
- Create: `src/peerassist/platform/services/workspace_bootstrap.py`
- Modify: `services/api/routes/__init__.py`
- Modify: `services/api/routes/review_jobs.py`
- Modify: `services/api/routes/papers.py`
- Modify: `services/api/routes/projects.py`
- Modify: `src/peerassist/platform/errors.py`
- Modify: `services/api/errors.py`
- Modify: `contracts/openapi/peerassist-v1.json`
- Create: `tests/platform/test_review_workspace_api.py`
- Modify: `tests/platform/test_openapi_contract.py`

- [ ] Write failing API tests for the facade matrix available before export: workspace GET, paper upload, review create/get/cancel/retry/delete, consent grant/deny/revoke/reapply, evidence GET, document GET/PUT, finding decision, authentication, tenant isolation, idempotency, and stable conflicts. Export submit/list/download belongs to Task 7.
- [ ] Add zero/one/multiple-project tests. Implement `WorkspaceBootstrapService` that uses existing Project/ProjectMembership repositories in one idempotent UoW: zero creates a personal default Project only for an authenticated active user with organization membership, one becomes default, multiple without default returns `workspace_selection_required`, and selection persists via `ProjectMembership.is_default`. Never use list order or browser cache.
- [ ] Run `.venv/bin/python -m pytest -q tests/platform/test_review_workspace_api.py` and confirm missing-route failures.
- [ ] Add typed project-compatible endpoints and the facade. Add stable error classes/mappings carrying `retryable`, current version, and action without leaking resource existence.
- [ ] Export OpenAPI using the repository's existing contract command, update `contracts/openapi/peerassist-v1.json`, and run `.venv/bin/python -m pytest -q tests/platform/test_review_workspace_api.py tests/platform/test_openapi_contract.py tests/platform/test_review_api.py`.
- [ ] Commit with `git commit -m "feat: expose teacher review workspace API"`.

## Chunk 3: Authoritative Export, Verification, and Handoff

### Task 7: Add the Frozen Asynchronous Export Command

**Files:**
- Modify: `src/peerassist/platform/models.py`
- Modify: `src/peerassist/platform/ports.py`
- Modify: `src/peerassist/platform/services/review_workspace.py`
- Modify: `src/peerassist/platform/services/artifacts.py`
- Modify: `services/api/routes/review_workspace.py`
- Modify: `services/worker/main.py`
- Test: `tests/platform/test_review_export.py`
- Modify: `tests/platform/test_review_draft_api.py`
- Modify: `tests/platform/test_openapi_contract.py`
- Modify: `contracts/openapi/peerassist-v1.json`

- [ ] Write failing tests that export freezes `document_version + decision_event_id + finding revisions + format`; duplicate commands replay; stale snapshots conflict; object generation failure leaves the job incomplete; object success plus database failure is reconciled; concurrent retries produce one ReportVersion and one Artifact.
- [ ] Run `.venv/bin/python -m pytest -q tests/platform/test_review_export.py tests/platform/test_review_draft_api.py -k export` and confirm failure.
- [ ] Make `POST /export` reserve a `CommandRecord` and enqueue an export `WorkItem` carrying only immutable snapshot identifiers. Deprecate direct finalize behavior but keep the old route as a compatibility wrapper that submits the export command.
- [ ] Add export submit/list/download routes to the teacher facade, export the updated OpenAPI contract, and keep Task 6 routes unchanged.
- [ ] In the Worker, render from the frozen ReviewDocument/decision/finding snapshot, publish to a deterministic staging/object key, then in one UoW add/reconcile ReportVersion and Artifact, append ReviewEvent/Outbox, complete WorkItem/CommandRecord, and set ReviewJob completed. Retry reuses a verified published object; generation failure cleans staging.
- [ ] Run export, draft, Worker, idempotency, and concurrency tests; commit with `git commit -m "fix: complete reviews through authoritative export"`.

### Task 8: Focused and Full Verification, Documentation, and Feishu Sync

**Files:**
- Modify: `docs/peerassist_operation_manual.md`
- Modify: `docs/peerassist_lark_sync.md`

- [ ] Run `.venv/bin/python -m pytest -q tests/platform/test_review_workspace_service.py tests/platform/test_review_workspace_api.py tests/platform/test_review_export.py tests/platform/test_review_draft_api.py tests/platform/test_worker_service.py tests/platform/test_tenant_isolation.py`.
- [ ] Run `.venv/bin/python -m ruff check src/peerassist/platform services/api services/worker tests/platform`.
- [ ] Run `.venv/bin/python scripts/check_docs.py`, `.venv/bin/python scripts/verify_repository.py all`, and `git diff --check`. Report any unavailable external dependency honestly.
- [ ] Perform adversarial review of malformed/empty document blocks, stale finding revisions, duplicate idempotency keys, expired/revoked consent, provider changes after queueing, object-store/database partial failure, concurrent tabs, legacy ambiguity, cross-project IDs, and PDF prompt injection.
- [ ] Update operator docs and Feishu chapters 三、七、八、十 in place using a fresh revision guard. Preserve exactly ten h2 headings and keep history under chapter 九.
- [ ] Commit with `git commit -m "docs: record P0 review workspace backend"`.

---

## Deferred Frontend Plan Gate

The four-entry teacher UI will receive a separate implementation plan after the current uncommitted changes in `web/peerassist-workspace/src/main.tsx`, `styles.css`, Keycloak theme files, and generated assets are committed or explicitly reconciled. That plan must consume `DESIGN.md`, include exact `npm ci`/`npm run build` and Playwright desktop/mobile commands on port 8766, and must not overwrite the existing login work.

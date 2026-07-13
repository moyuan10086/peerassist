# PeerAssist Milestone A Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a real PDF upload-to-human-confirmation workflow with durable jobs, evidence-linked paper profiling, cancellation/retry/recovery, and versioned final report export.

**Architecture:** Keep manuscript identity under `data/papers/<sha256>/` and each review execution under `data/jobs/<uuid>/`. A file-backed repository provides cross-process locks, revision CAS, monotonic events, attempt-isolated outputs, and committed stage manifests. A background runner executes a stage DAG and stops at human confirmation; final report export is a separate finalize operation.

**Tech Stack:** Python 3.12, Pydantic v2, `fcntl`, `python-multipart`, existing FactReview parse/PeerAssist modules, React 18, TypeScript, PDF.js, SSE.

**Design spec:** `docs/superpowers/specs/2026-07-13-peerassist-end-to-end-review-design.md`

---

## File Map

**Create**

- `src/schemas/peerassist_jobs.py`: Paper, ReviewJob, event, checkpoint, report manifest and authorization contracts.
- `src/peerassist/job_repository.py`: file-backed Paper/ReviewJob repository, locking, CAS, events and committed pointers.
- `src/peerassist/upload.py`: bounded streaming PDF upload and SHA-256 manuscript persistence.
- `src/peerassist/local_pdf_parser.py`: default no-upload parser for selectable-text PDFs using PyMuPDF.
- `src/peerassist/paper_profile.py`: paper profile, claim graph, experiment inventory and review plan builders.
- `src/peerassist/review_context.py`: claim-prioritized model context construction and traceable evidence selection.
- `src/peerassist/job_runner.py`: stage DAG, background worker registry, cancellation, retry and recovery.
- `src/peerassist/auth.py`: principal/session/resource-grant checks for remote mode.
- `src/peerassist/auth_repository.py`: hashed sessions, grants, revocation and authorization audit persistence.
- `tests/peerassist/test_job_repository.py`
- `tests/peerassist/test_upload.py`
- `tests/peerassist/test_paper_profile.py`
- `tests/peerassist/test_local_pdf_parser.py`
- `tests/peerassist/test_review_job_runner.py`
- `tests/peerassist/test_review_job_api.py`
- `web/peerassist-workspace/src/review-workspace.spec.ts`: Playwright upload, task recovery and profile UI tests.
- `web/peerassist-workspace/src/review-workspace.integration.spec.ts`: real frontend plus HTTP/SSE integration using fake parser/model adapters.
- `web/peerassist-workspace/playwright.config.ts`: deterministic frontend test server and browser configuration.

**Modify**

- `src/common/storage.py`: unique atomic temporary files and reusable fsync helper.
- `src/common/types.py`: legacy JobState schema version and migration-compatible optional fields.
- `src/pipeline_full.py`: accept a preallocated run directory without generating another run ID.
- `src/peerassist/stage_runner.py`: split candidate generation from final report export.
- `src/peerassist/confirmation_workflow.py`: expose confirmation revision and unresolved core-finding counts.
- `src/peerassist/concerns.py`, `src/peerassist/agents.py`, `src/peerassist/citation_concerns.py`, `src/peerassist/confirmations.py`: emit and consume the unified Finding identity.
- `src/peerassist/confirmation_server.py`: upload, paper, job, event, cancel, retry and finalize endpoints.
- `web/peerassist-workspace/src/main.tsx`: paper selector/upload, task timeline, cancel/retry/finalize and paper profile UI.
- `web/peerassist-workspace/src/styles.css`: task/upload/profile responsive layouts.
- `pyproject.toml`: add `python-multipart`.
- `web/peerassist-workspace/package.json`: add a focused Playwright test script and dev dependency.
- `README.md`, `docs/peerassist_operation_manual.md`, `docs/peerassist_lark_sync.md`.

---

## Chunk 1: Durable Paper And Job Storage

### Task 1: Define Review Job Contracts

**Files:**

- Create: `src/schemas/peerassist_jobs.py`
- Modify: `src/common/types.py`
- Test: `tests/peerassist/test_job_repository.py`

- [ ] **Step 1: Write the failing schema tests**

Cover `ReviewJobState` full paper SHA/run directory, revision defaults, checkpoint validation, and legacy JobState migration without moving artifacts.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/pytest -q tests/peerassist/test_job_repository.py -k 'contract or legacy'
```

Expected: import or validation failures because `schemas.peerassist_jobs` does not exist.

- [ ] **Step 3: Implement contracts**

Define `PaperRecord`, `ReviewJobState`, `ReviewStage`, `ReviewJobStatus`, `StageCheckpoint`, `StageManifest`, `ReviewJobEvent`, `FinalReportManifest`, `ExternalServiceConsent`, `Principal`, `SessionRecord`, and `ResourceGrant`. Required job fields include schema version, paper ID, run directory, mode, stage, revision, attempt ID, cancel flag, last event ID, confirmation revision, current stage manifests, per-service parse/search/model consent decisions, `blocked_reason`, `required_consents`, and `resume_stage`. Consent waiting is a persisted state, not an in-memory exception.

Keep existing `common.types.JobState` loadable; add optional migration metadata instead of breaking the existing runtime.

- [ ] **Step 4: Verify GREEN and commit**

```bash
.venv/bin/pytest -q tests/peerassist/test_job_repository.py -k 'contract or legacy'
.venv/bin/ruff check src/schemas/peerassist_jobs.py src/common/types.py
git add src/schemas/peerassist_jobs.py src/common/types.py tests/peerassist/test_job_repository.py
git commit -m "feat: define durable review job contracts"
```

### Task 2: Implement Cross-Process Repository Semantics

**Files:**

- Create: `src/peerassist/job_repository.py`
- Modify: `src/common/storage.py`
- Test: `tests/peerassist/test_job_repository.py`

- [ ] **Step 1: Write failing repository tests**

Cover stale CAS rejection, monotonic event IDs across two processes, damaged event-tail recovery, exclusive Worker claims, and unique atomic temporary files.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/pytest -q tests/peerassist/test_job_repository.py -k 'cas or event or claim or atomic'
```

- [ ] **Step 3: Implement repository behavior**

Implement complete-SHA `PaperRepository.create_or_get()`, ReviewJob CRUD/CAS, per-job `flock`, unique temporary files with fsync, event replay after `Last-Event-ID`, and Worker claim leases. Add `commit_stage_outputs()` that validates every output size/SHA, writes an immutable manifest, atomically switches `current_stages/<stage>.json`, and only then materializes the read-only compatibility view under `run/stages`.

- [ ] **Step 4: Verify race stability and commit**

Run the repository tests five times; every run must pass without duplicate event IDs or multiple successful claims. Inject failure before pointer replacement and assert the previously committed output remains readable.

```bash
.venv/bin/pytest -q tests/peerassist/test_job_repository.py
for i in 1 2 3 4 5; do .venv/bin/pytest -q tests/peerassist/test_job_repository.py -k 'processes or claim or manifest'; done
git add src/common/storage.py src/peerassist/job_repository.py tests/peerassist/test_job_repository.py
git commit -m "feat: add revisioned review job repository"
```

### Task 3: Persist Bounded PDF Uploads

**Files:**

- Create: `src/peerassist/upload.py`
- Modify: `pyproject.toml`
- Test: `tests/peerassist/test_upload.py`

- [ ] **Step 1: Write failing upload tests**

Cover valid PDF, invalid/forged server-side MIME and header combinations, empty file, path traversal filename, configured size limit, missing or dishonest length metadata, interrupted cleanup, and duplicate content deduplication.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/pytest -q tests/peerassist/test_upload.py
```

- [ ] **Step 3: Implement streaming upload**

Use `python-multipart` callbacks to write chunks into a unique temporary file while hashing and enforcing the byte limit. Validate `%PDF-`, fsync, then atomically move into `papers/<full_sha>/source/source.pdf` with mode `0600`.

- [ ] **Step 4: Verify GREEN and commit**

```bash
.venv/bin/pytest -q tests/peerassist/test_upload.py
git add pyproject.toml src/peerassist/upload.py tests/peerassist/test_upload.py
git commit -m "feat: persist bounded manuscript uploads"
```

---

## Chunk 2: Evidence-Linked Candidate Review Pipeline

### Task 4: Support A Preallocated Pipeline Run Directory

**Files:**

- Modify: `src/pipeline_full.py`
- Test: `tests/test_pipeline_full.py`

- [ ] **Step 1: Write a failing test**

Assert a supplied `run_dir_override` is used exactly and no nested/generated run directory is created.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/pytest -q tests/test_pipeline_full.py -k preallocated
```

- [ ] **Step 3: Implement the override**

Extract run-directory resolution into a helper. Existing CLI behavior remains unchanged when no override is supplied. The review job runner passes `job.run_dir` and records it before execution.

- [ ] **Step 4: Verify GREEN and commit**

```bash
.venv/bin/pytest -q tests/test_pipeline_full.py -k preallocated
git add src/pipeline_full.py tests/test_pipeline_full.py
git commit -m "feat: allow preallocated review run directories"
```

### Task 4A: Add The Default Local PDF Parser

**Files:**

- Create: `src/peerassist/local_pdf_parser.py`
- Modify: `pyproject.toml`
- Create: `tests/peerassist/test_local_pdf_parser.py`

- [ ] **Step 1: Write failing parser tests**

Use a small generated selectable-text PDF. Assert page text, blocks, line locators and bbox output; corrupted/encrypted/image-only PDFs return explicit unsupported or OCR-required states without external calls.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/pytest -q tests/peerassist/test_local_pdf_parser.py
```

- [ ] **Step 3: Implement local-first parsing**

Move `pymupdf>=1.26.0` into the Milestone A required dependency set, or define a required `peerassist` extra and make the server refuse task startup when it is absent. Use PyMuPDF in a timeout/resource-limited subprocess. This is the Milestone A default and requires no external consent. MinerU/Baidu remain optional providers selected only after task consent. Emit parse artifacts compatible with the evidence ledger adapter.

- [ ] **Step 4: Verify GREEN and commit**

```bash
.venv/bin/pip install -e .
.venv/bin/pytest -q tests/peerassist/test_local_pdf_parser.py
.venv/bin/ruff check src/peerassist/local_pdf_parser.py
git add pyproject.toml src/peerassist/local_pdf_parser.py tests/peerassist/test_local_pdf_parser.py
git commit -m "feat: parse selectable PDFs locally"
```

Also create a fresh temporary virtual environment, install the declared Milestone A dependency set, and run `test_local_pdf_parser.py` there so the developer environment cannot hide a missing dependency.

### Task 5: Build Paper Profile, Claim Graph And Experiment Inventory

**Files:**

- Create: `src/peerassist/paper_profile.py`
- Modify: `src/schemas/peerassist.py`
- Test: `tests/peerassist/test_paper_profile.py`

- [ ] **Step 1: Write failing profile tests**

Use evidence containing abstract, introduction, method, dataset, results, figure caption and conclusion. Build complete Pydantic contract tests for all four artifacts. Assert every populated field has `evidence_ids`, otherwise `needs_human_review`. Cover title/abstract/domain/type, research question, contributions, section roles, method inputs/outputs/assumptions, conclusion boundaries, parse warnings, claim support edges/status, and experiment dataset/sample size/splits/baselines/metrics/seeds/statistics/ablations/key figures/tables. Assert reported-versus-inferred provenance, stable claim IDs/centrality, core-first ranking, and no invented content when sections are absent.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/pytest -q tests/peerassist/test_paper_profile.py
```

- [ ] **Step 3: Implement deterministic extraction first**

Create section-aware evidence grouping, title/abstract/contribution candidates, method/claim support edges, complete experiment-field extraction and stable claim anchors. Task 5 is deterministic only; optional model enrichment is implemented in Task 7 and requires model consent.

- [ ] **Step 4: Write attempt-isolated artifacts**

Write `paper_profile.json`, `claim_graph.json`, `experiment_inventory.json`, and `review_plan.json` into the current stage attempt output directory.

- [ ] **Step 5: Verify GREEN and commit**

```bash
.venv/bin/pytest -q tests/peerassist/test_paper_profile.py
git add src/schemas/peerassist.py src/peerassist/paper_profile.py tests/peerassist/test_paper_profile.py
git commit -m "feat: build evidence-linked paper profiles"
```

### Task 6: Split Candidate Generation From Final Export

**Files:**

- Modify: `src/peerassist/stage_runner.py`
- Modify: `src/peerassist/report_export.py`
- Modify: `src/peerassist/confirmation_workflow.py`
- Modify: `src/peerassist/confirmation_server.py` (manual concern producer only)
- Modify: `src/peerassist/concerns.py`
- Modify: `src/peerassist/agents.py`
- Modify: `src/peerassist/citation_concerns.py`
- Modify: `src/peerassist/confirmations.py`
- Modify: `src/schemas/peerassist.py`
- Test: `tests/peerassist/test_stage_runner.py`
- Test: `tests/peerassist/test_confirmation_workflow.py`

- [ ] **Step 1: Write failing candidate/final tests**

Assert candidate execution creates evidence, citation audit, checks, profile, concerns and confirmation queue but no final manifest. Add non-citation, citation and manual concern fixtures proving all producers emit lineage/revision/finding ID and old confirmation actions remain readable. Assert finalize rejects unresolved core findings unless an override reason is supplied. Add a barrier-based concurrency test where confirmation CAS and finalize pointer commit race and exactly one mutation commits. If finalize wins, its manifest must match the frozen confirmation revision and the concurrent confirmation CAS must conflict. If confirmation wins, finalize must return `revision_conflict` and create no new report pointer. Neither outcome may leave a partial current pointer.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/pytest -q tests/peerassist/test_stage_runner.py tests/peerassist/test_confirmation_workflow.py -k 'candidate or finalize or lineage'
```

- [ ] **Step 3: Extract stage functions**

Separate `build_evidence`, `build_profile`, deterministic checks, citation audit, fast integrator, candidate queue and confirmed report finalization. Keep existing `run_peerassist_stage()` as a compatibility wrapper.

- [ ] **Step 4: Implement stable finding lineage**

Place the unified Finding fields in `schemas/peerassist.py`; update every producer in concerns, agents and citation_concerns, plus manual concern creation in the server, to emit them. Update confirmations and confirmation_workflow to bind lineage/revision/finding ID while preserving old-action read compatibility.

- [ ] **Step 5: Implement versioned finalize and commit**

Write final output to `reports/<version>/` with confirmation revision and localized paths, then atomically update the current final pointer. Task 6 pure-finalizer tests must prove: no final before finalize; Chinese and English JSON/Markdown exist; previous final versions are not overwritten; manifest matches the frozen confirmation revision; repeated finalize of the same revision is idempotent; stale revisions conflict; export failure leaves the previous pointer intact. Job status transitions for export success/failure belong to Task 7.

```bash
.venv/bin/pytest -q tests/peerassist/test_stage_runner.py tests/peerassist/test_confirmation_workflow.py -k 'candidate or finalize or lineage'
.venv/bin/ruff check src/peerassist/stage_runner.py src/peerassist/report_export.py src/peerassist/confirmation_workflow.py src/peerassist/confirmation_server.py src/peerassist/concerns.py src/peerassist/agents.py src/peerassist/citation_concerns.py src/peerassist/confirmations.py
git add src/peerassist/stage_runner.py src/peerassist/report_export.py src/peerassist/confirmation_workflow.py src/peerassist/confirmation_server.py src/peerassist/concerns.py src/peerassist/agents.py src/peerassist/citation_concerns.py src/peerassist/confirmations.py src/schemas/peerassist.py tests/peerassist/test_stage_runner.py tests/peerassist/test_confirmation_workflow.py
git commit -m "feat: separate candidate review from final export"
```

### Task 7: Implement The Recoverable Stage DAG

**Files:**

- Create: `src/peerassist/job_runner.py`
- Create: `src/peerassist/review_context.py`
- Modify: `src/peerassist/confirmation_server.py` (replace the legacy first-80 evidence context builder)
- Modify: `src/schemas/citation.py`
- Modify: `src/peerassist/citation_audit.py`
- Modify: `src/peerassist/citation_pipeline.py`
- Modify: `src/peerassist/citation_verification.py`
- Modify: `src/fact_generation/refcheck/stage_runner.py`
- Test: `tests/peerassist/test_review_job_runner.py`
- Create: `tests/peerassist/test_citation_pipeline.py`

- [ ] **Step 1: Write failing runner tests**

Use fake stages to test every state transition, successful stop at `awaiting_human_confirmation`, cancellation before the next stage, retry with a new attempt, downstream invalidation after an upstream hash change, orphan recovery, concurrent Worker claims, finalize `exporting_report → completed`, and export failure `exporting_report → failed`. Add approval-wait tests proving local parse, evidence, deterministic profile, claims, experiment inventory, review plan and deterministic checks are committed before the first consent gate. Missing consent commits `blocked_reason=approval_required`, required service and exact `resume_stage`, releases the Worker lease, and performs zero external calls; grant atomically clears the block and requeues from that persisted stage; denial preserves all local artifacts and commits a durable audited degraded result that can proceed without the denied service; concurrent repeated grants are idempotent.

Add a context regression fixture with more than 80 evidence items and place the highest-centrality claim evidence at the end. Assert the model input still contains that evidence plus paper profile, claim graph, experiment inventory, review plan and selected evidence IDs; assert the old sequential first-80 builder is no longer used.

Add citation RED tests proving RefCopilot/refcheck, Semantic Scholar and future adapters produce versioned observations only; canonical findings can only be emitted by citation pipeline and use the unified lineage/revision identity.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/pytest -q tests/peerassist/test_review_job_runner.py tests/peerassist/test_citation_pipeline.py
```

- [ ] **Step 3: Implement runner and registry**

Use a bounded `ThreadPoolExecutor` only as an in-process scheduler. Correctness must come from repository state, leases and locks. Long external work runs in cancellable subprocesses or timeout-bounded calls.

- [ ] **Step 4: Connect real stage adapters**

Wire the complete node table:

| Node | Job state | Depends on | Committed outputs |
| --- | --- | --- | --- |
| validate | `validating_input` | uploaded paper | validation manifest |
| parse | `parsing` | validate | local PyMuPDF parse artifacts, or explicitly consented external parse artifacts |
| evidence | `evidence_building` | parse | evidence ledger |
| profile | `profiling` | evidence | profile, claims, experiments |
| plan | `planning_review` | profile | review plan |
| deterministic | `deterministic_checking` | evidence, plan | deterministic checks |
| citation | `citation_checking` | evidence, plan | canonical citation audit/observations |
| agents | `agents_running` | profile, plan, checks, citation | fast integrator result |
| integrate | `integrating` | agents | findings, candidate queue, draft preview |
| await | `awaiting_human_confirmation` | integrate | confirmation revision |
| finalize | `exporting_report` | frozen confirmations | versioned final report |

Each node writes attempt outputs and calls repository `commit_stage_outputs()`. Tests assert the old current pointer remains readable until the new pointer is committed. A node that needs external consent must persist and release its claim before returning; granting consent schedules the job through the repository, not by retaining an old thread.

The local path must commit parse, evidence, deterministic profile/claims/experiments, review plan and deterministic checks before entering any consent-gated adapter. Optional model enrichment is part of the model-gated `agents` stage, not `profile`; external citation retrieval is similarly gated at `citation` while local citation extraction remains available. Build model input in `review_context.py` from the already committed profile/claims/experiments/review plan and claim-prioritized evidence slices. A model denial commits an auditable no-model/degraded agent result without deleting or replacing those local artifacts. Record each start/completion/failure in job events and PeerAssist tool trace. Trace tests must assert model, prompt version, input evidence IDs, start time, duration, status, output/artifact IDs, error and retry count. Citation tests must prove RefCopilot/refcheck only contributes observations and cannot create a competing final finding outside `citation_audit.json`.

- [ ] **Step 5: Verify GREEN and commit**

```bash
.venv/bin/pytest -q tests/peerassist/test_review_job_runner.py
.venv/bin/pytest -q tests/peerassist/test_citation_pipeline.py -k 'observation or canonical'
.venv/bin/ruff check src/peerassist/job_runner.py src/peerassist/review_context.py src/peerassist/confirmation_server.py src/peerassist/citation_audit.py src/peerassist/citation_pipeline.py src/peerassist/citation_verification.py src/fact_generation/refcheck/stage_runner.py
git add src/peerassist/job_runner.py src/peerassist/review_context.py src/peerassist/confirmation_server.py src/schemas/citation.py src/peerassist/citation_audit.py src/peerassist/citation_pipeline.py src/peerassist/citation_verification.py src/fact_generation/refcheck/stage_runner.py tests/peerassist/test_review_job_runner.py tests/peerassist/test_citation_pipeline.py
git commit -m "feat: run recoverable review stage DAG"
```

---

## Chunk 3: Secure Upload And Job Control API

### Task 8: Add Principal And Resource Authorization

**Files:**

- Create: `src/peerassist/auth.py`
- Create: `src/peerassist/auth_repository.py`
- Modify: `src/peerassist/confirmation_server.py`
- Test: `tests/peerassist/test_review_job_api.py`

- [ ] **Step 1: Write failing authorization tests**

Cover loopback default, refusal to bind publicly without auth, token-to-session exchange, session expiry/revocation, CSRF/Origin rejection, owner/reviewer/read-only grant creation/change/revocation, authorization audit, and unsafe-demo restrictions.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/pytest -q tests/peerassist/test_review_job_api.py -k auth
```

- [ ] **Step 3: Implement minimal remote auth**

Use an environment bootstrap token only to create a short-lived HttpOnly SameSite session. `auth_repository.py` stores hashed session tokens, ResourceGrant records, revocations and audit events using the same lock/atomic-write primitives as the job repository. Do not expose or persist the bootstrap token. Require TLS forwarding metadata in remote mode.

- [ ] **Step 4: Verify GREEN and commit**

```bash
.venv/bin/pytest -q tests/peerassist/test_review_job_api.py -k auth
.venv/bin/ruff check src/peerassist/auth.py src/peerassist/auth_repository.py
git add src/peerassist/auth.py src/peerassist/auth_repository.py src/peerassist/confirmation_server.py tests/peerassist/test_review_job_api.py
git commit -m "feat: authorize remote manuscript access"
```

### Task 9: Add Paper And Job Endpoints

**Files:**

- Modify: `src/peerassist/confirmation_server.py`
- Modify: `src/peerassist/job_runner.py`
- Test: `tests/peerassist/test_review_job_api.py`
- Test: `tests/peerassist/test_review_job_runner.py`

- [ ] **Step 1: Write failing API tests**

Cover upload/list/detail, idempotent review start, active-review conflict, job status, event replay, cancel, retry, finalize, authorized PDF access, deletion audit, and per-task external-service consent grant/deny/revoke. Add request-boundary tests for oversized JSON with an honest `Content-Length`, missing or forged `Content-Length`, and chunked bodies that cross the configured limit after parsing starts. Every overflow path must stop reading, remove temporary/request artifacts, and return the same structured `413 payload_too_large` response without invoking a route handler.

Add server-lifecycle tests that persist every nonterminal state, construct a fresh application/server instance, and run startup recovery using this matrix:

| Persisted state | Startup action |
| --- | --- |
| `queued` | schedule exactly once through a repository claim |
| active stage or `interrupted` with a reusable committed checkpoint | preserve incomplete attempt artifacts, transition/reclaim from the latest committed checkpoint, and schedule exactly once |
| active stage with no safe committed checkpoint | preserve forensic artifacts and transition to `failed` with a durable actionable recovery event |
| `cancel_requested` | preserve committed and incomplete attempt artifacts, durably transition the orphaned request to `cancelled`, and start no stage worker |
| `approval_required` | remain parked with required consent and exact resume stage; start no worker |
| `awaiting_human_confirmation` | remain parked for the reviewer; start no worker |

Starting the server repeatedly or concurrently must not enqueue, claim or create a stage attempt for the same job more than once.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/pytest -q tests/peerassist/test_review_job_api.py -k 'paper or job or event or finalize'
.venv/bin/pytest -q tests/peerassist/test_review_job_runner.py -k 'startup or recover or cancel_requested or duplicate'
```

- [ ] **Step 3: Implement thin routes**

Route state changes through repository and runner services. Add `POST /api/jobs/{job_id}/consents/{service}` with grant/deny/revoke and actor/reason audit. Parse, search and model nodes must read committed consent before any external request; absent or denied consent returns `approval_required` or uses an explicitly tested local fallback. Return structured errors: `mode_unavailable`, `review_in_progress`, `revision_conflict`, `approval_required`, `unresolved_core_findings`, and `forbidden`.

Apply one bounded streaming request reader to JSON endpoints before decoding. Treat `Content-Length` only as an early rejection hint, never as proof of size; count actual bytes for fixed-length and chunked bodies, reject overflow consistently, and clean up parser/request state in `finally` blocks.

- [ ] **Step 4: Recover persisted jobs during server startup**

During application/server initialization, scan all nonterminal jobs through `ReviewJobRunner.recover_nonterminal_jobs()` and apply the matrix above. Recovery must use repository leases/CAS rather than an in-memory registry. Never rewrite or delete an incomplete attempt during recovery. Make startup recovery idempotent so repeated initialization cannot create duplicate workers or duplicate stage attempts.

- [ ] **Step 5: Implement SSE replay**

Honor `Last-Event-ID`, replay durable events, emit heartbeat, then close/reconnect using the current server model. Never hold a Worker lock while writing to a client.

- [ ] **Step 6: Verify GREEN and commit**

```bash
.venv/bin/pytest -q tests/peerassist/test_review_job_api.py -k 'paper or job or event or finalize or consent'
.venv/bin/pytest -q tests/peerassist/test_review_job_api.py -k 'payload_too_large or content_length or chunked'
.venv/bin/pytest -q tests/peerassist/test_review_job_runner.py -k 'startup or recover or duplicate'
.venv/bin/pytest -q tests/peerassist/test_confirmation_workflow.py -k finalize
.venv/bin/ruff check src/peerassist/confirmation_server.py src/peerassist/job_runner.py tests/peerassist/test_review_job_api.py tests/peerassist/test_review_job_runner.py
git add src/peerassist/confirmation_server.py src/peerassist/job_runner.py tests/peerassist/test_review_job_api.py tests/peerassist/test_review_job_runner.py
git commit -m "feat: expose manuscript review job API"
```

---

## Chunk 4: Upload, Paper Profile And Task Timeline UI

### Task 10: Add Paper Selection And Upload

**Files:**

- Modify: `web/peerassist-workspace/src/main.tsx`
- Modify: `web/peerassist-workspace/src/styles.css`
- Modify: `web/peerassist-workspace/package.json`
- Create: `web/peerassist-workspace/src/review-workspace.spec.ts`

- [ ] **Step 0: Prepare test infrastructure**

Add `@playwright/test`, an `npm test` script and `playwright.config.ts` with a deterministic local frontend/API test server. Add one smoke test that opens the existing paper page and passes before writing feature assertions.

```bash
cd web/peerassist-workspace && npm test -- --grep 'workspace smoke'
```

Expected: PASS, proving later RED failures are behavioral rather than missing tooling.

- [ ] **Step 1: Add failing frontend contract assertions**

Create a Playwright test with a stub API server. Assert upload selection, request body, progress state, paper selection, invalid MIME/size errors and Chinese labels.

- [ ] **Step 2: Verify RED**

```bash
cd web/peerassist-workspace && npm test -- --grep 'upload paper'
```

Expected: FAIL because the upload controls and request do not exist.

- [ ] **Step 3: Implement the compact paper task bar**

Add paper selector, upload icon button, current filename, fast-mode selector and start action above the PDF without shrinking the main reading area.

- [ ] **Step 4: Build, verify and commit**

```bash
cd web/peerassist-workspace && npm test -- --grep 'upload paper' && npm run build
cd ../..
git add web/peerassist-workspace
git commit -m "feat: upload and select review papers"
```

### Task 11: Add Recoverable Task Timeline And Paper Profile

**Files:**

- Modify: `web/peerassist-workspace/src/main.tsx`
- Modify: `web/peerassist-workspace/src/styles.css`
- Modify: `web/peerassist-workspace/src/review-workspace.spec.ts`
- Create: `web/peerassist-workspace/src/review-workspace.integration.spec.ts`
- Modify: `web/peerassist-workspace/playwright.config.ts`

- [ ] **Step 1: Add failing UI contract assertions**

Use stubbed durable events and page reload. Cover stage labels, Last-Event-ID reconnect, polling fallback, cancel/retry/finalize, fast-only status, profile/claims/experiments views, evidence navigation, unresolved-core warning, `approval_required`, grant/deny external-service consent, and visible local-parser fallback state.

- [ ] **Step 2: Verify RED**

```bash
cd web/peerassist-workspace && npm test -- --grep 'recover review task'
```

Expected: FAIL on missing timeline/profile controls.

- [ ] **Step 2A: Add a failing real-server browser flow**

Start the real frontend and PeerAssist HTTP/SSE server under Playwright with deterministic fake parser/model adapters injected at the production adapter boundaries. The browser must upload a fixture, observe local parse/profile progress before any consent request, pause at model approval, grant and resume the exact persisted stage, exercise a separate denied/degraded run, reload during progress and recover through SSE/Last-Event-ID, confirm findings, finalize a report, and run at desktop 1440x960 and mobile 390x844.

```bash
cd web/peerassist-workspace && npm test -- --grep 'real review workflow'
```

Expected: FAIL because the real-server workflow and controls are not wired.

- [ ] **Step 3: Implement task state integration**

Reconnect SSE with the last event ID, fall back to polling, and restore selected paper/job from the URL. Show queued/running/awaiting/failed/cancelled/completed states without replacing the PDF reader.

- [ ] **Step 4: Implement progressive paper understanding UI**

When profiling commits, show research question, contributions, core claims, experiment inventory and reading route. Core claims link to PDF evidence locators.

Add a consent panel driven by job requirements. It must identify the service and data category, explain the local fallback, require an explicit grant or denial, and display an audit-confirmed decision. The UI must never auto-grant MinerU, model, retrieval or OCR access.

- [ ] **Step 5: Build, verify and commit**

```bash
cd web/peerassist-workspace && npm test -- --grep 'recover review task|real review workflow' && npm run build
cd ../..
git add web/peerassist-workspace
git commit -m "feat: show review task progress and paper profile"
```

---

## Chunk 5: Focused End-To-End Verification And Release

### Task 12: Run A Real PDF Through Milestone A

**Files:**

- Modify documentation only after behavior is verified.
- Add a small fixture under `tests/fixtures/peerassist/` only if targeted tests require it.

- [ ] **Step 1: Run focused automated verification**

```bash
.venv/bin/pytest -q \
  tests/peerassist/test_job_repository.py \
  tests/peerassist/test_upload.py \
  tests/peerassist/test_local_pdf_parser.py \
  tests/peerassist/test_paper_profile.py \
  tests/peerassist/test_review_job_runner.py \
  tests/peerassist/test_review_job_api.py \
  tests/peerassist/test_confirmation_server.py \
  tests/peerassist/test_stage_runner.py \
  tests/peerassist/test_confirmation_workflow.py \
  tests/peerassist/test_citation_pipeline.py
.venv/bin/ruff check src/peerassist src/schemas/peerassist_jobs.py tests/peerassist
cd web/peerassist-workspace && npm test && npm run build
```

The backend suite must include startup recovery/idempotency, fixed-length and chunked JSON overflow, temporary-artifact cleanup, consent grant/deny/resume, and the assertion that no parser/search/model adapter can issue an external request before its committed consent decision. The Playwright suite must include the consent panel, local-parser-first progress, approval pause, granted resume, denied degradation, refresh recovery and retry controls.

- [ ] **Step 2: Run a real arXiv paper**

Download this fixed manuscript when it is not already available:

```text
https://arxiv.org/pdf/2607.08522v1
SHA-256 cd7ff5e55067466f2238c9b9e28a4631cdf692cf55de1428d315cc422c009e73
```

```bash
curl -L --fail --output /tmp/peerassist-2607.08522v1.pdf https://arxiv.org/pdf/2607.08522v1
printf '%s  %s\n' cd7ff5e55067466f2238c9b9e28a4631cdf692cf55de1428d315cc422c009e73 /tmp/peerassist-2607.08522v1.pdf | sha256sum --check
```

Upload through `/api/papers` and start fast review with no external consent. Assert local PyMuPDF parsing and deterministic paper profiling complete first, and prove from both the durable event log and an outbound-request guard that no external request occurred. The task should then persist `approval_required`, `required_consents=[model]`, and the exact `resume_stage`; explicitly grant model consent, verify the grant atomically requeues that same stage, and observe progress through `awaiting_human_confirmation`. Record exact commands and returned paper/job IDs in the verification log. Separately deny model consent and verify the job preserves the completed local parse/profile/claim/experiment artifacts, commits an auditable local/no-model degraded state, and makes no external call before or after denial.

- [ ] **Step 3: Exercise recovery controls**

Cancel one task at a stage boundary and inject one stage failure. Restart the service and verify cancel history, retry attempt isolation, event replay and committed outputs.

- [ ] **Step 4: Exercise confirmation/finalize concurrency**

Attempt finalize with an older confirmation revision and confirm `revision_conflict`. Then use a barrier to race one confirmation CAS against finalize for the same current revision. Verify exactly one mutation commits: a finalize winner publishes a manifest for the frozen revision and forces confirmation conflict, while a confirmation winner forces finalize `revision_conflict` and publishes no new report pointer. Finally finalize the resulting current revision and verify versioned Chinese/English reports.

- [ ] **Step 5: Browser verification**

Use Playwright desktop 1440x960 and mobile 390x844. Verify upload, task progress, refresh recovery, profile visibility, PDF rendering, text selection, cancel/retry and no horizontal overflow.

- [ ] **Step 6: Run the final milestone audit**

```bash
git diff --check
git status --short
```

Audit each Milestone A requirement against job files, events, committed manifests, HTTP responses, final report manifest and browser results. Keep the larger P0/P1 goal active after Milestone A.

### Task 13: Publish In The Current PeerAssist Environment

This task is project delivery work, not a condition for the portable Milestone A implementation.

- [ ] **Step 1: Update Chinese documentation**

Update README and the operation manual with verified upload, task recovery, consent, confirmation and finalize commands. Do not document behavior that was not exercised.

- [ ] **Step 2: Append the Feishu development log**

Append a new numbered section, fetch it back, and confirm the previous section remains. Never use overwrite and never include manuscript contents or credentials.

- [ ] **Step 3: Scan secrets and commit**

```bash
rg -l --hidden --glob '!runs/**' --glob '!.git/**' --glob '!web/**/node_modules/**' \
  'ghp_[A-Za-z0-9]{30,}|sk-[A-Za-z0-9_-]{40,}' . || true
git diff --check
git status --short
```

- [ ] **Step 4: Restart and verify the configured service**

Preserve runtime-only model/auth configuration, restart the service, then verify local and configured public endpoints, authorized upload, Range PDF response and task recovery.

- [ ] **Step 5: Push the current branch**

Push only after verifying the configured remote and branch. Use transient authentication; do not write tokens to the remote or git config. Report the pushed commit and Feishu revision.

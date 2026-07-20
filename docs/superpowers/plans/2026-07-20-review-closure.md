# Review Closure Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a platform ReviewJob produce a review result that teachers can inspect, confirm, edit, persist, and export without losing the latest human changes.

**Architecture:** Keep the current FastAPI/PostgreSQL/MinIO platform boundary. Extend the Worker report stage with one immutable `review_result.json` artifact containing normalized concerns and PDF evidence references; the frontend hydrates its existing confirmation state from that artifact. Add a small server-owned draft endpoint only after the result artifact is working, and make finalize publish a report snapshot that includes the latest saved draft.

**Tech Stack:** Python 3.12, FastAPI, PostgreSQL repositories, MinIO object store, React/TypeScript/Vite, PDF.js, pytest, Playwright.

---

## Chunk 1: Structured Review Result

**Files:**
- Modify: `services/worker/main.py`
- Modify: `tests/platform/test_worker_service.py`
- Modify: `tests/platform/system/test_m1_workflow.py`
- Modify: `tests/platform/system/test_m1_dependency_failures.py`
- Modify: `web/peerassist-workspace/src/main.tsx`
- Test: `tests/repository/test_frontend_review_result.py`

- [x] **Step 1: Write failing tests** for a third `review_result.json` artifact containing a version, at least one pending concern, and evidence with a page number; add a frontend contract test for hydrating `queue.items` from the artifact.
- [x] **Step 2: Run the focused tests** and verify they fail because the artifact and hydration do not exist.
- [x] **Step 3: Implement the smallest deterministic result builder** in the Worker. Preserve the existing Markdown artifacts, derive stable concern IDs from the job-independent category, and bind each concern to a short extracted PDF text anchor and page number.
- [x] **Step 4: Publish and read `review_result.json`** through the existing immutable artifact API; map its concerns, evidence, agent runs, and citation availability into `ConfirmationState`.
- [x] **Step 5: Run focused worker/platform/frontend tests** and update system artifact expectations.
- [x] **Step 6: Commit** `feat: hydrate platform review evidence` (`a48ed71`).

## Chunk 2: Server-Owned Draft And Final Report

**Files:**
- Modify: `src/peerassist/platform/models.py`
- Modify: `src/peerassist/platform/ports.py`
- Modify: `src/peerassist/platform/adapters/postgres_schema.py`
- Modify: `src/peerassist/platform/adapters/postgres_review.py`
- Modify: `src/peerassist/platform/adapters/memory.py`
- Modify: `src/peerassist/platform/services/reviews.py`
- Modify: `services/api/routes/review_jobs.py`
- Modify: `services/worker/main.py`
- Modify: `web/peerassist-workspace/src/main.tsx`
- Test: `tests/platform/test_review_draft_api.py`

- [ ] **Step 1: Write failing API tests** for saving/loading a draft with expected job version and rejecting stale writes.
- [ ] **Step 2: Implement a project-scoped draft snapshot** using the existing command/event and object-storage patterns; keep the draft text server-side and never expose API keys or private paths.
- [ ] **Step 3: Add `GET/PATCH` draft routes** and replace frontend-only persistence with debounced server saves plus local fallback only while offline.
- [ ] **Step 4: Make finalize generate an immutable `final_report.md` snapshot** from the latest draft and confirmed result, then expose it through existing artifacts.
- [ ] **Step 5: Run API, worker, and frontend regression tests.**
- [ ] **Step 6: Commit** `feat: persist review drafts and final reports`.

## Chunk 3: First-Use Reliability

**Files:**
- Modify: `web/peerassist-workspace/src/main.tsx`
- Modify: `web/peerassist-workspace/src/styles.css`
- Modify: `services/api/errors.py`
- Test: `tests/repository/test_frontend_project_selection.py`
- Test: `tests/repository/test_frontend_error_messages.py`

- [ ] **Step 1: Add project selection and an empty-project onboarding state.**
- [ ] **Step 2: Replace generic upload/request errors with Chinese cause plus next action.**
- [ ] **Step 3: Add a browser smoke covering first login, project selection, upload, and returning to the same paper after refresh.**
- [ ] **Step 4: Commit** `fix: improve first review onboarding`.

## Chunk 4: Multi-Project Worker And Production Boundary

**Files:**
- Modify: `services/worker/main.py`
- Modify: `infrastructure/compose/compose.m1.yml`
- Modify: `scripts/m1_reference_env.sh`
- Modify: deployment/security documentation
- Test: `tests/platform/test_worker_queue_scope.py`

- [ ] **Step 1: Write a queue-scope test** proving a worker can claim jobs across authorized projects without trusting a client-provided tenant header.
- [ ] **Step 2: Replace fixed scope bootstrap with durable worker lease/claim selection.**
- [ ] **Step 3: Add TLS, secure-cookie, upload limits, rate-limit and recovery prerequisites to the deployment profile.**
- [ ] **Step 4: Run the full M1 verification and real Chromium workflow.**
- [ ] **Step 5: Commit** `feat: process review jobs across projects`.

## Verification Gates

- Focused pytest and frontend build pass after every chunk.
- `python scripts/verify_repository.py fast` is green, including Ruff import ordering.
- Real Chromium validates upload → summary → evidence concern → human decision → final report download.
- The Feishu project document records each completed chunk and the remaining risks.

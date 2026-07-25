# PeerAssist First-Use Repair Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing teacher workflow report model availability honestly, preserve manual evidence capture for platform jobs, show terminal job states accurately, and return new registrations to the paper workspace.

**Architecture:** Keep the current FastAPI and React/Vite boundaries. Expose only a non-secret model availability projection to authenticated teachers; keep discovery and mutation admin-only. Reuse existing platform decision and retry routes rather than inventing a second review command.

**Tech Stack:** FastAPI, pytest, React 18, TypeScript, Vite.

---

### Task 1: Safe teacher model availability

**Files:**
- Modify: `tests/platform/test_model_settings_api.py`
- Modify: `services/api/routes/model_settings.py`

- [x] Add a test proving a non-admin can read provider, model, enabled state, and credential presence without base URL, key hint, revision, policy, or configuration ID.
- [x] Run the focused test and confirm it fails because model and provider are absent.
- [x] Implement the minimal safe projection while leaving save and discovery admin-only.
- [x] Re-run the focused model-settings tests.

### Task 2: Teacher-facing state truthfulness

**Files:**
- Modify: `tests/repository/test_frontend_model_settings.py`
- Modify: `tests/peerassist/test_confirmation_server.py`
- Modify: `web/peerassist-workspace/src/main.tsx`

- [x] Add source-contract tests for authenticated model availability loading, terminal summary messages, and manual annotation remaining available for platform jobs.
- [x] Run the focused tests and confirm they fail for the missing behavior.
- [x] Replace the admin discovery probe in `App` with the safe model availability GET.
- [x] Add status-aware summary fallbacks and remove the platform-job restriction from the real manual decision action only.
- [x] Re-run the focused tests and the frontend build.

### Task 3: Registration return path

**Files:**
- Modify: `tests/platform/test_auth_dependencies.py`
- Modify: `services/api/routes/auth.py`

- [x] Extend the registration route test to complete the default flow and require a `/paper` callback.
- [x] Run the focused test and confirm it fails with `/admin`.
- [x] Change only the route default; preserve validated explicit return paths.
- [x] Re-run authentication tests.

### Task 4: Verification and deployment

**Files:**
- Generated: `web/peerassist-workspace/dist/*`

- [x] Run focused backend and repository tests, Ruff on changed Python files, and the frontend production build.
- [x] Perform an adversarial review for secret leakage, role compatibility, terminal statuses, and false affordances.
- [x] Restart the 8766/API services only if required by the changed deployment path, then verify login, identity, API readiness, and the public 8766 entry.
- [x] Commit only scoped source, tests, plan, and intended build output; leave unrelated untracked files untouched.

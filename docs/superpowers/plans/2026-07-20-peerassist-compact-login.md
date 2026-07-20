# PeerAssist Compact Login Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the promotional split login with a compact, task-focused academic login card.

**Architecture:** Keep Keycloak's existing form actions and conditional fields intact. Change only the PeerAssist login template structure and its self-contained stylesheet, protected by the repository theme contract and browser screenshots.

**Tech Stack:** Keycloak FreeMarker, CSS, Pytest, Playwright Chromium, Docker Compose.

---

## Chunk 1: Compact login theme

### Task 1: Protect the single-task layout contract

**Files:**
- Modify: `tests/repository/test_keycloak_theme_contract.py`

- [x] Add assertions for the compact brand context, removal of the marketing list, single-column CSS, and 460px maximum width.
- [x] Run the focused test and observe the expected failure.

### Task 2: Implement the compact template and stylesheet

**Files:**
- Modify: `infrastructure/keycloak/theme/peerassist/login/login.ftl`
- Modify: `infrastructure/keycloak/theme/peerassist/login/resources/css/login.css`

- [ ] Replace the promotional header content with a compact brand row and privacy note.
- [ ] Rebuild the stylesheet around one 460px card without column grid rules.
- [ ] Preserve Keycloak form actions, conditional blocks, localization, errors, and registration.
- [ ] Run `.venv/bin/python -m pytest -q tests/repository/test_keycloak_theme_contract.py` and expect all tests to pass.

### Task 3: Deploy and visually verify

**Files:**
- Rebuild: Keycloak service image only.

- [ ] Run `docker compose --project-name peerassist-m1-ref-59985de10e09f4f8 --env-file /tmp/peerassist-m1-final/environment -f infrastructure/compose/compose.m1.yml build keycloak`.
- [ ] Run the matching `docker compose ... up -d --no-deps keycloak` command and wait for a healthy container.
- [ ] Capture 1440x900 and 390x844 Chromium screenshots after `networkidle`.
- [ ] Verify no console errors, overlap, clipping, or horizontal overflow.
- [ ] Verify password visibility state and label, invalid-credential error rendering, registration navigation, English locale, and the conditional absence of remember-me for the current realm.
- [ ] Verify successful login redirects to the workspace.
- [ ] Review `git diff --check` and commit the change. The full repository gate is intentionally deferred because this is a Keycloak theme-only change and the user requested narrow testing; record that explicitly in the handoff.
- [ ] Under the user's standing synchronization instruction, update only the existing P0 checkbox in Feishu chapter ten after the repository change is approved; do not add or replace chapters.

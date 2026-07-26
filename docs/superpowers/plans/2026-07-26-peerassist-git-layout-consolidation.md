# PeerAssist Git Layout Consolidation Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/root/PeerAssist` the single current Git repository, preserve all legacy and runtime data, publish the current branch and releases to GitHub, and synchronize one authoritative current/history document to Feishu.

**Architecture:** Build and verify an independent candidate repository outside the live path, protect both existing worktrees and non-Git data with manifests, then switch paths under stopped writers with a durable state marker. Git is authoritative; GitHub publishes explicit current refs; Feishu remains a ten-chapter synchronized view.

**Tech Stack:** Git worktrees/refs/bundle, Bash/systemd, Python verification scripts, GitHub CLI, lark-cli Docx v2.

---

## Chunk 1: Preserve and Consolidate

### Task 1: Capture Recoverable State

**Files:**
- Create outside Git: `/root/.peerassist-migration/protected/*`
- Preserve: `/root/.worktrees/peerassist-m0`
- Preserve: `/root/PeerAssist/archive/peerassist-mvp`
- Preserve: `/root/PeerAssist/runtime`

- [ ] Create `/root/.peerassist-migration/{protected,state}` with mode `0700`; abort if candidate, protected, rollback, or state paths already exist unexpectedly. Use `stat -c %d` on their parents and abort unless candidate, current root, and rollback path share one filesystem; record `df -B1` free-space evidence.
- [ ] Record filesystem checks, Git porcelain v2, refs/OIDs, remotes/upstreams, and per-file tracked/untracked/ignored inventories from both worktrees. For every untracked/ignored file and `/root/PeerAssist/README.md`, record relative path, type, size, SHA-256, and exactly one disposition: protection commit, restricted backup, regenerable, or explicitly excluded.
- [ ] Copy every excluded/private/non-Git file to `/root/.peerassist-migration/protected` with its relative layout and verify mode `0700` plus manifest hashes. Explicitly preserve the currently untracked plan and `docs/versions/feishu-baseline-correction.xml`; do not assume a Git bundle contains them.
- [ ] Record runtime file counts, total bytes, latest modification time, job/paper IDs, immutable artifact hashes, Docker volume identities, service unit/launcher hashes, and current HTTP probes.
- [ ] Run repository secret checks against both worktrees; abort archive commit if a secret/private manuscript is detected and preserve it only in the restricted backup.
- [ ] Commit safe archive changes on local `archive/peerassist-mvp-local`; create local annotated protection tag `archive/peerassist-mvp-local-2026-07-26`.
- [ ] Verify every pre-existing untracked and ignored file from both worktrees and the outer README has an executed, checksum-verified disposition.

### Task 2: Build an Independent Candidate Repository

**Files:**
- Create outside live path: `/root/.peerassist-migration/candidate`
- Modify candidate: `.gitignore`
- Add candidate: `reference-materials/*.md`

- [ ] Export a full Git bundle/mirror and create the candidate without alternates or dependencies on the old common directory. Do not copy either existing `.venv`.
- [ ] Check out `peerassist-m0`; restore `moyuan` and FactReview `origin`, disable pushes to FactReview origin, and record explicit remote semantics.
- [ ] Compare all required branch/tag OIDs and run `git fsck --full`.
- [ ] Add runtime and migration-backup exclusions to `.gitignore`; import safe Markdown reference materials.
- [ ] Create an isolated temporary candidate environment, run docs, secret, lint, Python, frontend, and Git diff checks that do not depend on the final absolute path, then remove that environment. Record any pre-existing failure separately; do not silently fix unrelated user files.

## Chunk 2: Cut Over and Document

### Task 3: Switch the Live Repository and Services

**Files:**
- Modify: `deploy/systemd/peerassist-ui.service`
- Modify: `deploy/systemd/peerassist-review-api.service`
- Modify: `deploy/systemd/peerassist-ui-start.sh`
- Create outside Git: `/root/.peerassist-migration/recover-cutover.sh`
- Modify: `/etc/systemd/system/peerassist-*.service`
- Modify: `/usr/local/sbin/peerassist-ui-start`

- [ ] Search repository, active systemd units, launchers, and Compose files for `/root/PeerAssist/current` and `/root/.worktrees/peerassist-m0`; prepare final-path replacements.
- [ ] Back up active units and `/usr/local/sbin/peerassist-ui-start` with modes and hashes. Write explicit forward and inverse install commands to the protected manifest before stopping services.
- [ ] Stop every PeerAssist writer that can mutate runtime data; verify no writer remains.
- [ ] Generate the final runtime manifest and sync runtime/reference material into candidate without changing database, object-store, or identity volumes.
- [ ] Create `/root/.peerassist-migration/recover-cutover.sh` and a state record containing stage, old/candidate OIDs, expected paths, and manifest hash. The script must implement and log this table: `initial + old root OID + candidate OID` = no cutover/continue normally; `initial + root absent + rollback old OID + candidate OID` = record `old-moved`; `old-moved + root absent + rollback old OID + candidate OID` = activate candidate or restore rollback if candidate invalid; `old-moved + root candidate OID + candidate absent` = record `candidate-active`; `candidate-active + root candidate OID + rollback old OID` = run verification, then record `verified`, or move failed candidate aside and restore rollback; `verified + root candidate OID` = never infrastructure-rollback for later documentation failures. Any unexpected OID/path combination aborts without rename.
- [ ] Test the recovery script in a disposable directory for crashes before/after both renames and before/after every state write. Persist `initial -> old-moved -> candidate-active -> verified`, fsync the state directory around writes/renames, and use OIDs/hashes rather than names alone.
- [ ] Rename live root to `/root/PeerAssist.pre-git-layout`, record/fsync `old-moved`, then rename candidate to `/root/PeerAssist`, record/fsync `candidate-active`. Do not start any service while the state is `old-moved`.
- [ ] At the final path, rebuild `.venv`, install the package, and verify shebang, `.pth`, imports, and entrypoints reference only `/root/PeerAssist`.
- [ ] Install updated units/launcher, run `systemctl daemon-reload`, start services, and verify 8766, 8767, PDF Range, readiness, runtime manifests, and Docker volume identity.
- [ ] If verification fails before `verified`, stop new services, restore old units/launcher, reverse the renames, run `systemctl daemon-reload`, and restore old services. After service/data/Git checks pass, record/fsync `verified`; later GitHub/Feishu failures do not roll back validated infrastructure.

### Task 4: Create One Authoritative Project Document

**Files:**
- Create: `docs/PROJECT_OVERVIEW.md`
- Modify: `README.md`
- Modify: `docs/README.md`
- Modify: `docs/versions/README.md`
- Modify: `docs/peerassist_lark_sync.md`
- Modify: `docs/system_review_2026-07-17.md`
- Modify: `docs/system_review_2026-07-20.md`
- Modify: `docs/superpowers/specs/2026-07-17-peerassist-platform-canvas-agent-design.md`
- Modify: `docs/product/peerassist-teacher-first-roadmap.md`
- Modify: `docs/versions/v0.1.0-p0.md`
- Modify: `docs/versions/v0.1.1-p0.md`
- Modify: `scripts/check_docs.py`

- [ ] Write `docs/PROJECT_OVERVIEW.md` with a literal `<!-- current-authority -->` marker and exactly eight top-level sections: current conclusion/product position; current code/version/layout; current capabilities; current runtime architecture/entries; current limitations/risks; historical versions/migration; still-valid versus deferred historical plans; documentation authority/update rules.
- [ ] Explain the old `/root/.worktrees/peerassist-m0` and `/root/PeerAssist/current` topology only in the history section; all current instructions use `/root/PeerAssist`.
- [ ] Reduce `README.md`, `docs/README.md`, and `docs/versions/README.md` to scoped navigation and add literal `<!-- authority: docs/PROJECT_OVERVIEW.md -->` links.
- [ ] Add literal status markers without rewriting historical content: `historical-review` to both system reviews, `historical-plan` to the 2026-07-17 platform/canvas spec, `current-reference` to the teacher roadmap, and `version-record` to both release notes.
- [ ] Update `scripts/check_docs.py` to require exactly one `current-authority`, all three authority links, the six status markers, and no old worktree path in current instruction sections.
- [ ] Run `.venv/bin/python scripts/check_docs.py`, `.venv/bin/python scripts/verify_repository.py secrets`, `git diff --check`, focused repository tests, `.venv/bin/python -m ruff check`, and `npm --prefix web/peerassist-workspace run build`; record expected exit `0` and commit the layout/documentation consolidation.

## Chunk 3: Publish and Synchronize

### Task 5: Publish the Current Version to GitHub

**External target:** `https://github.com/moyuan10086/peerassist`

- [ ] Verify `gh api user --jq .login` equals `moyuan10086`, repository is `moyuan10086/peerassist`, default branch is `peerassist-mvp`, remote baseline is `peerassist-mvp @ 1e73c93`, and `peerassist-m0` plus both release tags are absent. Record remote OIDs/default branch/timestamp in the migration manifest.
- [ ] Abort if any target remote ref exists with a different OID. Run only `git push moyuan refs/heads/peerassist-m0:refs/heads/peerassist-m0`, `git push moyuan refs/tags/v0.1.0-p0:refs/tags/v0.1.0-p0`, and `git push moyuan refs/tags/v0.1.1-p0:refs/tags/v0.1.1-p0`; no force.
- [ ] Verify all three `git ls-remote` OIDs, then run `gh repo edit moyuan10086/peerassist --default-branch peerassist-m0`; verify repository JSON and homepage README. Record post-publish OIDs/default branch/timestamp.
- [ ] Do not push the local archive protection branch/tag, and do not use `--all`, broad `--tags`, or force.

### Task 6: Synchronize the Existing Feishu Ten-Chapter Document

**External target:** `https://my.feishu.cn/docx/XuVIdkaGgoykehxox9Kc3Qnhnw2`

- [ ] Fetch and preserve full revision `298` with block IDs and verify exactly these ten chapter headings: 项目结论、当前能力与状态、当前工程基线、目标架构、无限证据画布、自治 Agent 与人工门禁、安全部署与恢复、迁移路线与验收、历史里程碑与事实来源、当前行动项.
- [ ] Map Git overview sections to the ten existing chapter block IDs. Preserve all human-authored content outside those managed chapter sections and do not create a second document.
- [ ] Generate XML previews for bounded section replacements with content summaries. Use `--revision-id <observed>` on every `docs +update` call as the CAS precondition; if the tool/API cannot enforce that precondition, stop at preview unless an explicit exclusive synchronization window exists.
- [ ] Record the user's explicit approval in this conversation to reorganize, modify, and synchronize the existing Feishu total document. Confirm each generated preview stays within the ten approved managed chapters; any change outside that scope requires a new approval.
- [ ] Apply one chapter at a time and immediately re-read it; record chapter, content summary, input revision, output revision, and block IDs. Stop on revision mismatch or partial success.
- [ ] Re-read the final outline and all changed chapters; update `docs/peerassist_lark_sync.md` with the final revision and commit that synchronization record.
- [ ] Before the synchronization-record push, compare remote/local OIDs and abort unless the remote is an ancestor/equal. Run only `git push moyuan refs/heads/peerassist-m0:refs/heads/peerassist-m0`, verify remote OID, record it, and run a final porcelain check preserving only pre-existing user files.

### Task 7: Final Verification and Handoff

- [ ] Run `git -C /root/PeerAssist fsck --full`, ref/OID checks, repository verification commands, and service/runtime probes fresh.
- [ ] Verify `/root/PeerAssist/.git` is independent and neither services nor current docs depend on the old worktree paths.
- [ ] Verify GitHub default branch and tags, Feishu final revision/ten chapters, and local/remote HEAD equality.
- [ ] After all Git/Feishu checks, remove the old linked-worktree registration with Git worktree bookkeeping only; do not delete the rollback tree's files.
- [ ] Keep `/root/PeerAssist.pre-git-layout` as the explicit rollback copy until the user confirms removal; do not delete it in this task.

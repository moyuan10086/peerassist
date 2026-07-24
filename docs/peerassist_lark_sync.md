# PeerAssist Lark Sync

This file pins the Feishu/Lark document used for implementation-progress sync.
The document now has exactly ten stable second-level sections; update the
existing relevant sections with a revision guard instead of appending dated
sections.

- Document: PeerAssist 论文审核辅助系统：现状、架构与迁移路线
- URL: https://my.feishu.cn/docx/XuVIdkaGgoykehxox9Kc3Qnhnw2
- Document token: XuVIdkaGgoykehxox9Kc3Qnhnw2
- Owner identity used to create it: user 陈昊
- Created on: 2026-07-10
- Last verified revision: 292
- Current product source: `docs/superpowers/specs/2026-07-24-peerassist-evidence-review-workspace-positioning-design.md`
- Current structure: exactly ten stable h2 chapters; historical progress is retained under chapter 九, while chapter 十 contains only current actions.

Use sections 三、八、十 for verified engineering progress, milestone status,
and current actions. Fetch and verify the ten-section outline before and after
each write, and use the current `revision_id` as an optimistic concurrency
guard. Do not store API keys, GitHub tokens, model keys, private paths, paper
secrets, provider responses, or reviewer-private material here.

The evidence-review-workspace positioning is synchronized in place across the
stable chapters: product conclusion, current capability gaps, engineering
baseline, four-entry information architecture, canvas/Agent deferral,
task-scoped external-model consent, recovery semantics, phased acceptance,
fact sources, and current actions. Do not append a dated h2 progress section for
this positioning; update the stable chapter that owns the information.

The DESIGN.md contract and its DTCG validation workflow are recorded in the
repository at `docs/design-system.md` and synchronized in chapter 十. The
Feishu document is a progress mirror only; the repository files remain the
normative design and engineering source.

M1 synchronization must record the delivered login/admin/member/model configuration surfaces,
PostgreSQL/Keycloak/MinIO authority, real PDF-to-summary workflow, 35-operation OpenAPI contract,
317 focused platform tests and the 9-check real-provider smoke. Keep the existing ten headings and
update in place; do not append another dated progress log.

## 2026-07-17 M0 baseline sync contract

- The user authorized revision 137 as the new baseline. Task 6 was synchronized
  by precise edits to sections 三、八、十 and verified at revision 140.
- Task 6 evidence records commits `ac7f65a` and `83885a6`, with the then-current
  unified verification result of 912 passed, 1 skipped, and 3 deselected.
- Final M0 evidence is `941 passed, 1 skipped, 3 deselected`, plus successful
  clean-checkout bootstrap, Compose health and PDF Range verification, actual
  systemd smoke, and exact source-worktree integrity comparison.
- Tasks 7 through 11 may be synchronized only after a fresh revision fetch and
  successful ten-section outline check. No overwrite or dated-section append is
  allowed.

## 2026-07-10 23:xx CST - Agent console UI and deterministic stream update

- Local service URL: http://127.0.0.1:8766/
- GitHub branch: https://github.com/moyuan10086/peerassist/tree/peerassist-mvp
- Commit: 4661d8a feat: add deterministic significance stream UI
- Added deterministic significance-star consistency leads when an explicit legend and exact p-value disagree.
- Renamed builtin percentage capability surface to deterministic_consistency_checks.
- Upgraded confirmation UI with stream state chip, runtime strip, agent metadata, status-colored tool trace, and SSE snapshot endpoint /api/events with /api/state fallback.
- Verification: .venv/bin/python -m pytest -q -> 365 passed, 1 skipped, 3 deselected.
- Note: browser screenshot validation was attempted, but Playwright/browser dependencies are not installed in this runtime.

## 2026-07-10 23:39 CST - Eval target detail ledger

- GitHub branch: https://github.com/moyuan10086/peerassist/tree/peerassist-mvp
- Commit: 9e35841 feat: add eval target detail ledger
- Added machine-readable target_details to PeerAssist-Eval-v1 aggregate reports, including passed, actual, threshold, direction, and margin for every objective metric.
- Added failed_target_names so CI, Feishu summaries, and reviewer-facing status can explain exactly which frozen-set targets remain below threshold.
- Verification: .venv/bin/python -m pytest -q -> 366 passed, 1 skipped, 3 deselected.

## 2026-07-10 23:49 CST - Unevidenced fact rate accounting fix

- GitHub branch: https://github.com/moyuan10086/peerassist/tree/peerassist-mvp
- Commit: 9ff4f79 fix: count unsupported facts in eval records
- Eval record building now counts active concerns with bound evidence IDs but unsupported audited facts as unevidenced_new_facts.
- This prevents the no-evidence-new-fact rate from being under-reported when a concern cites evidence but introduces unsupported numeric content.
- Verification: .venv/bin/python -m pytest -q -> 367 passed, 1 skipped, 3 deselected.

## 2026-07-10 23:53 CST - Paired crossover recall regression diagnostics

- GitHub branch: https://github.com/moyuan10086/peerassist/tree/peerassist-mvp
- Commit: 4ed68ef feat: flag paired crossover recall regressions
- Reviewer crossover analysis now emits paired_reviewer_records by sample_id and reviewer_id with baseline_core_recall, assisted_core_recall, delta, and regression flag.
- Added recall_regression_warnings so the real reviewer experiment can catch reviewer/sample-level core recall drops instead of relying only on aggregate mean delta.
- Verification: .venv/bin/python -m pytest -q -> 368 passed, 1 skipped, 3 deselected.

## 2026-07-10 23:57 CST - Crossover CLI recall regression gate

- GitHub branch: https://github.com/moyuan10086/peerassist/tree/peerassist-mvp
- Commit: ab3e569 fix: fail crossover CLI on recall regression
- The peerassist-eval crossover command now returns a non-zero exit code when recall_regression_warnings are present, and includes those warnings in the CLI summary.
- This turns the "do not reduce core problem recall" crossover constraint into an executable gate instead of a report-only warning.
- Verification: .venv/bin/python -m pytest -q -> 369 passed, 1 skipped, 3 deselected.

## 2026-07-11 00:05 CST - Chinese UI and Feishu log repair

- The confirmation console UI was localized into Chinese: page title, panels, metrics, stream state, action buttons, status pills, common agent names, common deterministic concern copy, and empty states.
- Evidence text remains in its source language to avoid mistranslating paper evidence.
- The Feishu document had append-order drift because earlier updates anchored on heading blocks; revision 24 adds a complete Chinese repair section named "十、2026-07-10 中文增量日志修复版（完整）".
- Future Feishu updates should fetch structure first and anchor to a real tail content block, not a heading-only block.

## 2026-07-11 00:10 CST - Eval hard-gate failure summary

- Eval reports now expose failed_gate_names for freeze_policy_ok, manifest_integrity_ok, and record_coverage_ok.
- The peerassist-eval aggregate CLI summary now prints failed_target_names and failed_gate_names so PeerAssist-Eval-v1 failures identify both metric thresholds and frozen-set integrity gates.
- Feishu document revision 29 adds section "十一、2026-07-11 Eval 硬门禁失败摘要" after the verified tail content block.
- Verification: .venv/bin/python -m pytest -q -> 369 passed, 1 skipped, 3 deselected.

## 2026-07-11 00:22 CST - Feishu document integrity repair

- User reported the Feishu document looked incomplete and possibly overwritten.
- Investigation found no current full-document wipe, but did find structure drift: empty headings and several late-night logs grouped under an older heading/list because earlier syncs anchored on heading blocks.
- Feishu document revision 33 adds section "十二、2026-07-11 文档完整性修复版（以此为准）" after verified tail content block doxcnQBmBBK452sH3G6usanTiXe.
- Repair method: block_insert_after only; no overwrite, no delete, no history revert.
- Future Feishu sync rule: fetch outline, anchor to a real tail content block, write with block_insert_after, then fetch outline/section to verify title and body.

## 2026-07-11 00:35 CST - Feishu outline cleanup

- User reported the middle Feishu outline looked strange.
- Four English timestamp h2 blocks were demoted to normal paragraph blocks so they no longer appear in the left outline:
  Agent console UI and deterministic stream update; Eval target detail ledger; Unevidenced fact rate accounting fix; Paired crossover recall regression diagnostics.
- Feishu document revision 40 outline now shows only the numbered Chinese sections 一 through 十二.
- Repair method: block_replace only on the four heading blocks; no overwrite, no delete, no history revert.

## 2026-07-11 01:19 CST - Public PeerAssist confirmation service

- PeerAssist confirmation console was started on 0.0.0.0:8766 with PID 11454.
- Public URL verified: http://101.47.158.17:8766/
- The page returns the modern Chinese agent UI with "运行态指挥条", "会话导览", "证据焦点", and "证据审稿队列".
- Feishu document revision 41 adds section "十三、2026-07-11 PeerAssist 公网服务启动记录".
- Verification: .venv/bin/python -m pytest -q -> 369 passed, 1 skipped, 3 deselected.

## 2026-07-11 01:25 CST - PeerAssist SSE stream lifecycle

- The confirmation console /api/events endpoint now emits retry, state, heartbeat, and done SSE frames instead of a single state-only snapshot.
- Frontend EventSource logic now listens for heartbeat and done while preserving /api/state polling fallback.
- Public URL verified: http://101.47.158.17:8766/api/events returns retry: 15000, event: state, event: heartbeat, and event: done.
- Feishu document revision 42 adds section "十四、2026-07-11 PeerAssist SSE 数据流增强".
- Verification: .venv/bin/python -m pytest -q -> 369 passed, 1 skipped, 3 deselected.

## 2026-07-11 01:31 CST - PeerAssist artifact workspace and next actions

- The confirmation console now includes "产物工作区" and "下一步动作" panels.
- Artifact workspace is derived from /api/state paths and shows review queue, human confirmations, agent results, capability invocations, and tool trace artifacts.
- Next actions summarize pending confirmations, failed tool events, and recorded human actions for reviewer workflow guidance.
- Public URL verified: http://101.47.158.17:8766/ returns the new panels and artifact paths.
- Feishu document revision 43 adds section "十五、2026-07-11 PeerAssist 产物工作区与下一步动作".
- Verification: .venv/bin/python -m pytest -q -> 369 passed, 1 skipped, 3 deselected.

## 2026-07-11 02:05 CST - PeerAssist workflow ribbon and live stream log

- The confirmation console replaces the left directory-like navigator with a horizontal "审稿流程" workflow ribbon.
- The workflow ribbon shows evidence queue, call trace, and human gate stages plus a human confirmation progress meter.
- The human confirmation panel now renders a "实时事件" stream log for state, heartbeat, done, and error lifecycle updates.
- The review queue includes an inline progress strip derived from recorded actions and pending concerns.
- Public URL verified: http://101.47.158.17:8766/ returns "审稿流程", "人工确认完成度", "实时事件", data-agent-workflow, data-stream-log, and data-review-progress.
- Browser verification: Playwright screenshots for desktop 1440x1000 and mobile 390x844 reported overflow=0 and no missing key selectors.
- Verification: .venv/bin/python -m pytest -q -> 369 passed, 1 skipped, 3 deselected.
- Static check: .venv/bin/python -m ruff check src/peerassist/confirmation_server.py tests/peerassist/test_confirmation_server.py -> all checks passed.

## 2026-07-11 02:28 CST - PeerAssist trace filters and artifact copy controls

- The tool trace panel now includes segmented filters for all, queued, completed, and failed events.
- Artifact workspace rows now include "复制路径" controls with clipboard fallback and a lightweight in-page toast.
- The interactions stay inside the existing confirmation console and preserve /api/state, /api/events, and /api/decision contracts.
- Public URL verified: http://101.47.158.17:8766/ returns data-trace-filter, data-copy-path, and agent-toast.
- Browser verification: Playwright clicked the completed trace filter, copied an artifact path, observed "产物路径已复制", and reported overflow=0.
- Verification: .venv/bin/python -m pytest -q -> 369 passed, 1 skipped, 3 deselected.
- Static check: .venv/bin/python -m ruff check src/peerassist/confirmation_server.py tests/peerassist/test_confirmation_server.py -> all checks passed.

## 2026-07-11 02:42 CST - PeerAssist paper annotation surface

- Added a central "论文原文预览" panel to make the review flow resemble PDF/Word annotation.
- The paper surface renders an A4-like manuscript page, line numbers, evidence-highlighted text, and margin comments mapped to queue concerns.
- The existing evidence review queue remains below the paper surface, so reviewers can move from page context to human confirmation without losing workflow state.
- Public URL verified: http://101.47.158.17:8766/ returns "论文原文预览", "PDF/Word 批注式阅读面", paper-highlight, and data-margin-comment.
- Browser verification: Playwright desktop 1440x1100 and mobile 390x900 checks found the paper viewer, highlight, margin comment, trace filter, and artifact copy controls with overflow=0.
- Verification: .venv/bin/python -m pytest -q -> 369 passed, 1 skipped, 3 deselected.
- Static check: .venv/bin/python -m ruff check src/peerassist/confirmation_server.py tests/peerassist/test_confirmation_server.py -> all checks passed.

## 2026-07-11 03:02 CST - PeerAssist annotation-to-queue linking

- Paper margin comments now include "定位队列" controls that activate and scroll to the linked human-confirmation queue item.
- Review queue items now include "查看原文高亮" controls that activate and scroll back to the paper highlight.
- The selected paper highlight, margin comment, and queue item share the same active state so reviewers can keep context while moving between PDF/Word-style reading and structured confirmation.
- Public URL verified: http://101.47.158.17:8766/ returns "查看原文高亮", "定位队列", focusAnnotation, and data-paper-concern.
- Browser verification: Playwright clicked both directions, observed "已定位到审稿队列" and "已定位到论文高亮", and reported overflow=0.
- Verification: .venv/bin/python -m pytest -q -> 369 passed, 1 skipped, 3 deselected.
- Static check: .venv/bin/python -m ruff check src/peerassist/confirmation_server.py tests/peerassist/test_confirmation_server.py -> all checks passed.

## 2026-07-11 09:28 CST - Documentation and operation manual update

- Added `docs/peerassist_operation_manual.md` as the Chinese operation manual for service startup, frontend development, PDF-first review workflow, manual confirmation, API endpoints, Feishu sync rules, GitHub submission, troubleshooting, and security boundaries.
- Replaced the repository README with a Chinese PeerAssist-first homepage, covering public service URL, frontend source path, operation manual link, quick-start commands, GitHub author explanation, and the FactReview base note.
- Added `web/peerassist-workspace/README.md` so the frontend directory clearly shows how to run and build the React/Vite workspace.
- Confirmed frontend source is tracked under `web/peerassist-workspace`; GitHub language statistics may still show Python-dominant because of file size and Linguist timing.
- Updated local git author config for future commits to `moyuan10086 <moyuan10086@users.noreply.github.com>`; historical pushed commits remain unchanged unless history is explicitly rewritten.
- Security rule retained: no real API keys, GitHub tokens, model keys, or reviewer-private material are written to docs, code, Feishu, or git config.

## 2026-07-11 - Traceable citation audit v1

- Appended Feishu section `六十一、2026-07-11 可追溯证据定位与引用核查第一版`; no overwrite operation was used.
- Feishu revision advanced from `93` to `94`; keyword fetch confirmed both sections `六十` and `六十一` remain present.
- Implemented numeric citation extraction, reference linking, RefCopilot artifact adaptation, immutable verification attempts, semantic audit integrity, conservative citation concerns, confirmation reconciliation, pipeline integration, and bilingual report provenance.
- Final PeerAssist smoke suite: `445 passed`; citation Ruff checks passed; public service returned HTTP `200`.
- GitHub branch: `moyuan10086/peerassist@peerassist-mvp`; merge commit `a477fc3`.
- Remaining scope is explicit: author-year citations, retraction/PubPeer checks, semantic support relation, and frozen `CitationBench-200` evaluation are not yet complete.

## 2026-07-13 - PDF-first workspace performance and UI optimization

- Replaced the independent `/legacy` user interface with a `308` compatibility redirect to the canonical `/paper` React workspace.
- Added HTTP Range streaming for `/paper.pdf`, plus `Accept-Ranges`, ETag, Last-Modified and private caching. A `bytes=0-1023` request now returns `206` with exactly 1024 bytes instead of the full PDF.
- Changed hashed frontend assets to one-year immutable caching and switched to the minified PDF.js worker, reducing the worker from about 2.2 MB to about 1.25 MB.
- Rebuilt the paper window as a PDF-first split workspace: compact Chinese navigation rail, large selectable PDF, resizable/collapsible review inspector, fit-width mode, page input, progress, retry, download/open controls and adjacent-page prefetch.
- PDF text selection now automatically fills the review inspector with the page number; selection review and manual evidence queue actions remain adjacent to the source text.
- Browser verification on the current 11-page demo paper: desktop 1440x960 reached rendered PDF plus text layer in about 3.27 s; mobile 390x844 in about 4.09 s; both had zero horizontal overflow. Selection propagation and inspector expansion were also verified.
- Focused verification: frontend production build passed; three confirmation server tests passed; public `/legacy` redirect, PDF `206`, immutable asset caching and HTTP `200` workspace access were verified.
- Appended Feishu section `六十二、2026-07-13 PeerAssist PDF 首屏性能与审稿工作台重构`; revision advanced from `94` to `95`. Keyword verification confirmed both sections `六十一` and `六十二` remain present. No overwrite operation was used, and no model key, GitHub token or private review material was written.

## 2026-07-13 - End-to-end review Milestone A foundation

- Approved and committed the real-manuscript Milestone A design and implementation plan, including full-SHA Paper identity, multiple ReviewJobs per Paper, candidate/final report separation, local-first parsing and per-task external-service consent.
- Implemented strict durable job contracts and the cross-process file repository: revision CAS, monotonic durable events, damaged-tail recovery warnings, worker leases, deletion tombstones, committed checkpoints/manifests and one authoritative current-stage pointer.
- Focused verification reached 24 passing contract tests and 58 passing repository tests; the concurrency/recovery subset passed five consecutive runs with 34 tests per run, and Ruff passed.
- Appended Feishu section `六十三、2026-07-13 真实论文端到端审稿里程碑 A 启动`; revision advanced from `95` to `96`. Keyword verification confirmed both sections `六十二` and `六十三` remain present.
- Sync used `block_insert_after` only. No overwrite, credential, manuscript-private content or reviewer-private material was used.

## 2026-07-13 - Bounded streaming PDF upload

- Added direct-stream and multipart PDF persistence with incremental SHA-256, actual-byte limits, MIME/filename/PDF-header validation, unique mode-0600 temporary files and complete interruption/parse cleanup.
- Added a separate 64 KiB multipart overhead budget, directory-fd/O_NOFOLLOW publication, atomic source-and-record locking, immutable source-field checks and concurrent duplicate convergence.
- Focused upload verification reached 28 passing tests. The contract, repository and upload suite reached 86 passing tests with Ruff clean.
- Appended Feishu section `六十四、2026-07-13 有界 PDF 流式上传完成`; revision advanced from `96` to `97`. Keyword verification confirmed both sections `六十三` and `六十四` remain present.
- Sync used `block_insert_after` only and stored no credentials or private manuscript content.

## 2026-07-13 - Local parsing and evidence-linked paper understanding

- Added exact preallocated run-directory support so durable ReviewJobs and the legacy pipeline share one authoritative run path.
- Added a local-first PyMuPDF subprocess parser for selectable PDFs, with isolated invocation artifacts, timeout/resource limits, encrypted/corrupted/OCR-required states, and line-level page/locator/bbox preservation into the EvidenceLedger.
- Added four deterministic paper-understanding artifacts: `paper_profile.json`, `claim_graph.json`, `experiment_inventory.json`, and `review_plan.json`.
- The review plan now prioritizes parser uncertainty, central claims and important experiments before supporting claims; minor evidence is filtered from the primary route and agent context while remaining explicitly traceable.
- Claim support excludes self-support and uses directional comparison semantics for positive, negative and reversed comparisons. Multiple experiments keep datasets, figures and tables separated, including multiword dataset names with connectors.
- Task 5 passed specification review and independent code-quality review. The focused Milestone A regression suite reached 171 passing tests; the independent reviewer also ran the complete `tests/peerassist` suite with 555 passing tests. Ruff and `git diff --check` were clean.
- Non-blocking follow-ups remain explicit: split multiple datasets listed in one sentence, and introduce an `unknown` relation distinct from `supported_by` for semantically indeterminate double-negation cases.
- Appended Feishu section `六十五、2026-07-13 本地解析与证据化论文画像完成`; revision advanced from `97` to `98`. Append-only keyword verification confirmed sections `六十四` and `六十五` remain present. No overwrite, credentials, manuscript-private content or reviewer-private material was used.

## 2026-07-14 - Candidate review and versioned final report separation

- Candidate execution now stops at the human-confirmation queue and does not create a final report. Finalization is a separate revision-checked operation.
- Unified deterministic, agent, citation and manual concerns under stable finding lineage, finding revision, supersedes/reconciliation and display revision semantics.
- Confirmation actions bind to `finding_lineage_id + finding_id + revision`; both the legacy console and React workspace submit the current triple, preventing stale-page confirmation after candidate regeneration.
- Final reports are immutable bilingual JSON/Markdown versions under `reports/<version>/`; the manifest freezes confirmation actions and every finding revision, including rewritten and deleted concerns.
- Finalization blocks unresolved core findings without an override, detects stale confirmation/finding snapshots, is idempotent only for an identical snapshot, and preserves the previous pointer on failure.
- Focused verification: 29 Task 6 tests and 63 related producer/confirmation tests passed during implementation; final stale-binding/reference-lineage checks passed 4 focused tests, Ruff passed, and the frontend production build passed.
- Appended Feishu section `六十六、2026-07-14 候选审稿与版本化最终报告分离`; revision advanced from `98` to `99`. Append-only verification confirmed sections `六十五` and `六十六` remain present.

## 2026-07-14 - Recoverable review DAG core

- Added a repository-backed review runner that executes durable stages under an exclusive Worker lease and commits attempt-isolated stage outputs before advancing state.
- The core runner now supports cancellation before the next stage, model-consent blocking and persisted resume stage, explicit consent grant, model-denied local fallback, and retry with a new attempt ID while retaining prior artifacts.
- Added durable degradation fields for denied services and a claim-prioritized review context containing paper profile, claim graph, experiment inventory, review plan, deterministic checks and selected evidence IDs.
- Replaced the sequential first-80 evidence model context with core-claim/reading-route/experiment/check prioritization, so high-centrality evidence at the end of a large ledger is retained.
- Focused verification passed 8 relevant runner/context/agent-review tests and Ruff checks.
- Appended Feishu section `六十七、2026-07-14 可恢复审稿 DAG 核心`; revision advanced from `99` to `100`. Append-only verification confirmed sections `六十六` and `六十七` remain present.

## 2026-07-14 - Background scheduling and orphan recovery

- Added a bounded ThreadPoolExecutor scheduler while keeping repository leases and CAS as the correctness boundary.
- Jobs can be submitted in the background, deduplicated in-process, recovered after an interrupted service process, and waited on without losing durable stage state.
- Cancellation requests are persisted before the Worker observes them. Finalization now records `exporting_report` and transitions to completed, awaiting confirmation, or failed based on the versioned finalizer result.
- Focused review-runner verification reached 10 passing tests with Ruff clean.
- Appended Feishu section `六十八、2026-07-14 后台调度与孤儿任务恢复`; revision advanced from `100` to `101`. Append-only verification confirmed sections `六十七` and `六十八` remain present.

## 2026-07-14 - Real local PDF candidate-review chain

- Connected real local adapters for validation, PyMuPDF parsing, EvidenceLedger construction, paper profile/claim graph/experiment inventory/review plan, deterministic checks, citation audit, local agents and candidate integration.
- A generated selectable-text PDF now runs through all local stages, blocks at persisted model consent only after local artifacts are committed, resumes after grant, writes the human confirmation queue, and stops at `awaiting_human_confirmation` without creating a final report.
- Focused recoverable-runner verification reached 11 passing tests with Ruff clean.
- Appended Feishu section `六十九、2026-07-14 真实 PDF 本地候选审稿链路跑通`; revision advanced from `101` to `102`. Append-only verification confirmed sections `六十八` and `六十九` remain present.

## 2026-07-14 - Public Review Job HTTP and SSE API

- Added durable HTTP endpoints for review creation, job snapshots, SSE event replay, cancellation, retry, model consent and finalization.
- A real PDF lifecycle test creates a job through HTTP, observes the persisted model-consent block, replays durable stage events, grants consent and reaches human confirmation.
- Started the backend worker service on local port 8767 and proxied `/api/health`, `/api/reviews`, and `/api/jobs/*` through the existing public Chinese workspace on port 8766.
- Public health endpoint verified: `http://101.47.158.17:8766/api/health`; the main workspace continues to return HTTP 200.
- Appended Feishu section `七十、2026-07-14 Review Job HTTP 与 SSE 公网接口`; revision advanced from `102` to `103`. Append-only verification confirmed sections `六十九` and `七十` remain present.

## 2026-07-14 - Persistent Review Job timeline UI

- Added `GET /api/jobs` and a Chinese background-review task panel inside the modern agent window.
- Task cards show durable status, current stage, revision, degradation/error state and a stable twelve-stage progress track, with model-consent, cancel, retry and final-report controls.
- Registered arXiv `2607.08522v1` under its full SHA-256 and created a real public ReviewJob. It completed validate, parse, evidence, profile, plan, deterministic and citation stages and is durably parked at `agents / approval_required` without sending the manuscript to a model.
- Browser screenshot verification confirmed the real task card, progress track and authorization controls render correctly in the public `/agent` workspace.
- Appended Feishu section `七十一、2026-07-14 持久化审稿任务时间线 UI`; revision advanced from `103` to `104`. Append-only verification confirmed sections `七十` and `七十一` remain present.

## 2026-07-14 - Real PDF upload to durable review job

- Added `POST /api/papers/upload` with bounded multipart streaming, existing PDF validation and persistence, stable SHA-256 Paper identity, and automatic fast-mode ReviewJob creation.
- Public port `8766` now proxies the upload route to the durable worker API on `8767`; invalid PDF content returns a structured `400`, oversized requests return `413`, and missing request length returns `411`.
- Added a Chinese upload bar to the intelligent-review window. Reviewers can select a PDF, see the selected filename, upload it, and immediately observe the new durable task card without leaving the page.
- Focused verification passed both Review Job API tests and Ruff checks; the production frontend build passed. Desktop and mobile Playwright checks found no horizontal overflow or console errors.
- Replaced ad-hoc background shells with restart-on-failure transient systemd services for ports `8766` and `8767`; the public health endpoint returned `ok` after restart.
- Local commit: `74db628 feat: upload papers into durable review jobs`.
- Appended Feishu section `七十二、2026-07-14 真实论文上传与持久化审稿任务联动`; revision advanced from `104` to `105`. Append-only keyword verification confirmed sections `七十一` and `七十二` remain intact, with no credentials or private manuscript content stored.

## 2026-07-14 - Durable Paper PDF-first reading connection

- Added `GET/HEAD /api/papers/<sha256>/source` with repository-confined path resolution, ETag/Last-Modified metadata, byte ranges, suffix ranges, `416` handling and bounded file streaming.
- Updated the public `8766` proxy to stream request and response bodies and forward Range, conditional-cache and PDF response headers instead of buffering complete uploads or papers in memory.
- Upload completion now stores the active Paper identity locally and navigates directly to the PDF-first reading workspace. Every durable ReviewJob card also has a Chinese `阅读论文` action.
- The reader distinguishes durable task papers from the legacy demo run. It keeps text selection available while disabling legacy-run review writes, preventing concerns from being attached to the wrong manuscript.
- Fixed PDF.js compatibility for current Chromium by installing missing Map/WeakMap methods in the page and using the official legacy PDF worker for the separate worker realm.
- Focused verification: 3 Review Job API tests passed, Ruff passed, and the production frontend build passed. Public Range returned `206` with 1024 bytes and a valid `%PDF-1.7` header.
- Browser verification opened the persisted arXiv Paper from its task card, rendered page 1 of 11 at 151%, produced 111 selectable text nodes, matched the real paper title, and had no horizontal overflow or console errors.
- Local commits: `eab7fdc feat: open durable papers in the PDF workspace` and `b56a7ca fix: support PDF.js range loading in current browsers`.
- Appended Feishu section `七十三、2026-07-14 持久化论文与 PDF 首屏阅读打通`; revision advanced from `105` to `106`. Append-only keyword verification confirmed sections `七十二` and `七十三` remain intact.

## 2026-07-14 - ReviewJob-scoped human confirmation workspace

- Added `GET /api/jobs/<id>/workspace`, which reconstructs the effective concern queue from the job's candidate findings plus immutable confirmation actions and returns sanitized job-scoped evidence, agent and trace state.
- Added `POST /api/jobs/<id>/decisions` with confirmation revision CAS and stable finding lineage/revision binding. Successful decisions synchronize the ReviewJob `confirmation_revision` used by finalization.
- Integration now writes job-compatible `evidence_ledger.json`, `agent_results.json` and an empty capability invocation ledger when needed, so task state survives refresh and service restart without falling back to the legacy demo run.
- The frontend persists both active Paper and active ReviewJob identities. Paper, queue, confirmation and navigation counters now read the selected job workspace and refresh every three seconds.
- PDF concern cards include page-aware evidence links. Clicking evidence navigates the reader to the corresponding page; page-local concerns flow back into the side panel, and pending findings expose confirm/rewrite/downgrade/delete actions.
- Confirmed findings display Chinese status and no longer show mutation buttons.
- Focused verification passed 4 Review Job API tests, Ruff and the production frontend build.
- Browser E2E used a temporary awaiting-confirmation job: opened its PDF, displayed the task concern, jumped from evidence to page 2, confirmed the item, changed pending count from 1 to 0 and confirmation revision from 0 to 1, with no console errors. The temporary job was then archived and removed from the public list.
- Local commit: `5ccbb2e feat: connect review jobs to human confirmation`.
- Appended Feishu section `七十四、2026-07-14 ReviewJob 任务级人工确认工作区`; revision advanced from `106` to `107`. Append-only keyword verification confirmed sections `七十三` and `七十四` remain intact.

## 2026-07-14 - Immutable final report discovery and download

- Added a job-scoped final report artifact index derived only from `current_final_report.json` and its immutable manifest.
- Report manifest and artifact paths must resolve inside the job's `reports/` directory; downloads verify the manifest SHA-256 before serving data.
- Added `GET/HEAD /api/jobs/<id>/artifacts` and `/api/jobs/<id>/artifacts/<name>` for the four allowlisted English/Chinese Markdown/JSON reports.
- Artifact downloads use attachment disposition; `?disposition=inline` provides a real browser preview without weakening path or hash validation.
- The selected ReviewJob workspace now exposes report version, confirmation revision, file size, SHA-256 and safe download URLs.
- The Chinese artifact window renders four report cards with online-view and download actions. Successful finalization automatically selects the job and opens the artifact window.
- Focused verification passed 4 Review Job API tests, Ruff and the production frontend build.
- End-to-end verification confirmed one task at confirmation revision 1, finalized it as report version `r000001`, displayed four artifacts, downloaded the Chinese Markdown with HTTP 200 and verified it contained PeerAssist report content. The page had no horizontal overflow or console errors.
- Public HEAD verification returned `Content-Disposition: inline; filename="report.zh.md"` for the preview URL. The temporary task was archived and removed from the public list.
- Local commit: `06e96e0 feat: expose immutable final report artifacts`.
- Appended Feishu section `七十五、2026-07-14 不可变最终报告在线查看与下载`; revision advanced from `107` to `108`. Append-only keyword verification confirmed sections `七十四` and `七十五` remain intact.

## 2026-07-14 - Citation audit workspace and public firewall recovery

- Added the job-scoped citation audit view to the PDF inspector: coverage metrics, manuscript mention, reference metadata, external verification source, field differences, findings and linked human decision status.
- Citation mention and reference evidence now navigate to their PDF pages and render bbox/text highlights. Linked citation concerns expose task-scoped confirm/rewrite/downgrade/delete actions under the existing confirmation-revision contract.
- The server response is sanitized and excludes verification queries, raw response paths and internal cache paths. Unsupported citation markers render an explicit Chinese degradation warning instead of appearing as a successful zero-result audit.
- Focused verification passed 4 Review Job API tests, Ruff and the production frontend build. Browser E2E verified citation/reference page jumps, highlights, source and mismatch display, human confirmation and a nonblank PDF canvas with no console errors. The temporary task was archived afterward; the real task remains blocked at model consent.
- Diagnosed the public outage as a firewalld public-zone omission. Port `8766/tcp` is now allowed in runtime and permanent rules; external probes returned HTTP 200. Port `8767` remains behind the public workspace proxy.
- Replaced the transient units with enabled persistent systemd services under `/etc/systemd/system`, with repository templates under `deploy/systemd/`. External probes from Poland, Portugal and Turkey returned HTTP 200 after the persistent-service restart.
- Appended Feishu section `七十六、2026-07-14 引用核查前端闭环与公网防火墙修复`; revision advanced from `108` to `109`. Append-only keyword verification confirmed sections `七十五` and `七十六` remain intact.

## 2026-07-14 - Selectable single-page and spread PDF review

- Refactored the React PDF reader into one document/navigation controller plus independent `PdfPageView` renderers, so each visible page owns its canvas, selectable text layer and evidence overlay.
- Added icon controls for single-page and spread reading. Spread mode follows a cover-first layout and then renders `2–3`, `4–5`, and later pairs; page input and evidence links still focus the exact target page.
- Page selection is now derived from the actual text layer that received the mouse gesture. Selecting text on the right page of a spread writes that page number into the review inspector.
- Concern and citation evidence can target either visible page. Only the matching page renders the bbox/text highlight, while the inspector updates to the exact evidence page.
- Viewports narrower than 860 px automatically use one page while retaining the desktop preference. Browser verification at 1600x1000 rendered two nonblank selectable pages, selected text from page 3, and highlighted a page-3 concern; 390x844 rendered one page with no horizontal overflow or console errors.
- Appended Feishu section `七十七、2026-07-14 PDF 单页与双页审稿模式`; revision advanced from `109` to `110`. Append-only keyword verification confirmed sections `七十六` and `七十七` remain intact.

## 2026-07-14 - PDF-to-concern reverse navigation

- Added one page-edge annotation marker per concern and PDF page, preferring evidence with bbox coordinates and applying vertical collision spacing for nearby findings.
- Clicking a marker opens the inspector, selects `本页关注`, moves the linked concern to the first position, applies a selected-card state and highlights the source evidence.
- Existing concern evidence links continue to navigate from the card back to the PDF, completing a bidirectional path over the same concern ID, evidence ID, page and bbox.
- Desktop browser verification confirmed a page-3 marker on the right page of a `2–3` spread, no marker on page 2, exact concern focus and bbox highlight. Mobile verification confirmed the marker remains reachable without horizontal overflow.
- Appended Feishu section `七十八、2026-07-14 PDF 原文与 concern 双向定位`; revision advanced from `110` to `111`. Append-only keyword verification confirmed sections `七十七` and `七十八` remain intact.

## 2026-07-14 - Durable ReviewJob timeline and cancel/retry closure

- Added a sanitized timeline to ReviewJob list and detail responses: event type, normalized stage, status, timestamp, attempt ordinal, duration and allowlisted service name. Raw payloads, actors and internal paths are excluded.
- Corrected future `stage_completed` events to record the stage that actually finished. The API also reconstructs legacy completion stages by matching the corresponding stage-start event in the same attempt.
- The Chinese task card shows the latest durable event and an expandable eight-row timeline while retaining 24 recent events in the API view. Refreshing the page reconstructs the same timeline from JSONL.
- Fixed cancellation for blocked jobs: after persisting `cancel_requested`, the API now schedules a worker to commit `job_cancelled`. Retrying a cancelled job creates attempt 2 and retains attempt 1 events.
- Focused HTTP verification covered stage durations, payload omission, blocked cancellation, terminal cancellation and retry attempts. Browser verification covered the real task timeline, refresh recovery, and a temporary blocked job's cancel/retry lifecycle; the temporary job was archived.
- The real arXiv ReviewJob is now at `awaiting_human_confirmation` with 20 durable events. Existing agent artifacts still show skeletal/local agent output and remain a P1 quality gap rather than proof of professional multi-agent review quality.
- Appended Feishu section `七十九、2026-07-14 ReviewJob 持久时间线与取消重试闭环`; revision advanced from `111` to `112`. Append-only keyword verification confirmed sections `七十八` and `七十九` remain intact.

## 2026-07-14 - Section-aware professional agents and token-bounded UI

- Fixed the real local-PDF data boundary that left all 1,108 evidence rows without sections. The evidence builder now recovers title, abstract, numbered sections, limitations, references and appendices while preserving page, line and bbox locators.
- Reconstructed same-block PDF line fragments and soft hyphenation into complete analysis units. The verified public arXiv paper now yields its complete title, 24 section labels, 8 candidate claims and 3 experiment groups instead of an empty paper profile.
- Replaced the four-result local agent skeleton with seven professional roles (structure, methodology, experiment, statistics, citation, ethics and reproducibility) plus figure/table, defense and integration roles. Every result records responsibility, evidence count, draft count, review engine and model status.
- Added one batched OpenAI-compatible enhancement boundary for fast mode. It selects at most 40 evidence blocks and 12,000 evidence-text characters, enforces a 24,000-character cap on the complete serialized context, defaults to 900 output tokens, rejects every concern whose evidence IDs are outside the selected context, and records usage and tool trace when configured.
- Runtime credentials remain absent from the repository and this document. When no runtime key is configured, the ReviewJob proceeds through an explicit local fallback and the Chinese UI displays `gpt-5.4（未启用）`; no external model request is made.
- Real ReviewJob `ddec0625-80b8-441b-8cac-7e7216741603` reached `awaiting_human_confirmation` with 20 durable events, 10 completed agent views, 2 evidence-grounded candidate concerns, zero model calls and zero model tokens.
- The React agent window now uses Chinese names and responsibilities, evidence/finding/model metrics, localized warnings and responsive three/two/one-column layouts. Playwright verified 10 cards at desktop 1440x960 and mobile 390x844 with zero horizontal overflow and zero console errors.
- Focused verification: 62 backend tests passed, scoped Ruff passed, the Vite production build passed, and both local and public health endpoints returned success.
- Appended Feishu section `八十、2026-07-14 章节感知专业代理与 token 受控界面`; revision advanced from `112` to `113`. Append-only keyword and outline fetches confirmed both sections `七十九` and `八十` remain intact; no overwrite operation was used.

## 2026-07-14 - Complete model-context budget enforcement

- Release auditing found that limiting selected evidence text alone did not bound the complete serialized request because full profile, claim, experiment and review-plan structures were still included.
- The fast-mode boundary now separately caps evidence text at 12,000 characters and the complete serialized context at 24,000 characters. Compact grounded summaries and priority-aware tail trimming preserve the most important claims, experiments, checks and traceable evidence first.
- The real ReviewJob context measured 23,750 serialized characters and retained 6 core claims, 3 experiment groups, 4 deterministic checks and 52 traceable evidence IDs.
- Focused release verification passed 597 PeerAssist tests, scoped Ruff, the Vite production build and the credential scan.
- Appended Feishu section `八十一、2026-07-14 模型上下文总预算发布修订`; revision advanced from `113` to `114`. The write used append only and did not overwrite existing sections.

## 2026-07-19 - 教师优先目标与路线重写

- 产品目标从“先完成平台权限与基础设施”调整为“让高校老师从上传 PDF 到编辑、导出审稿意见形成可靠闭环”。
- 新增 `docs/product/peerassist-teacher-first-roadmap.md`，路线改为 P0 真实可用闭环、P1 审稿结果可用、P2 持续使用、P3 平台化；复杂 RBAC、无限画布和完整 Next.js 迁移后置。
- 重写 `docs/product/peerassist-platform-prd.md` 的用户、信息架构、最小权限、产品指标和非目标；旧 M1 权限计划保留为技术参考。
- 飞书文档 `XuVIdkaGgoykehxox9Kc3Qnhnw2` 通过精准 block_replace 更新第 1、2、4、8、10 章，revision 从 `185` 提升到 `201`；随后补记 P0 失效上下文修复，revision 为 `202`；outline 校验仍保持正好 10 个二级章节。
- 本次同步未写入账号密码、API Key、稿件原文或其他私密材料。

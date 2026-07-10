# PeerAssist Lark Sync

This file pins the Feishu/Lark document used for implementation-progress sync.

- Document: PeerAssist 论文审核辅助系统搭建过程同步
- URL: https://my.feishu.cn/docx/XuVIdkaGgoykehxox9Kc3Qnhnw2
- Document token: XuVIdkaGgoykehxox9Kc3Qnhnw2
- Owner identity used to create it: user 陈昊
- Created on: 2026-07-10

Use this document to append major build milestones, test results, UI changes,
GitHub upload status, and known risks. Do not store API keys, GitHub tokens,
model keys, paper secrets, or reviewer-private material here.

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

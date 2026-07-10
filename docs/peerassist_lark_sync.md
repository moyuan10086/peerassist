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

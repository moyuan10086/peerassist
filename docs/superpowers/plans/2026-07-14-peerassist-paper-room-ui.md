# PeerAssist Paper-Room UI Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the React review workspace into the approved A2 paper-room layout with a near-full-width PDF and a durable, collapsible review drawer.

**Architecture:** Keep the existing PDF.js document controller and job data flow. Add a small drawer state boundary around the inspector, snapshot manual text selections before layout changes, and express the new visual system entirely through the existing React/CSS application without remote assets or a new UI framework.

**Tech Stack:** React 19, TypeScript, Vite, PDF.js, Lucide icons, Playwright.

**Design:** `docs/superpowers/specs/2026-07-14-peerassist-paper-room-ui-design.md`

---

## Chunk 1: Drawer State And Selection Safety

### Task 1: Add Review Drawer State

**Files:**
- Modify: `web/peerassist-workspace/src/main.tsx`

- [ ] **Step 1: Map the current inspector state**

Identify `inspectorOpen`, `inspectorTab`, selected concern, citation highlight, selected text/page and local-storage helpers. Replace `inspectorOpen/inspectorTab` directly with the drawer state in this task; do not leave parallel inspector and drawer sources of truth.

- [ ] **Step 2: Add drawer preferences**

Implement or reuse a small `useStoredBoolean` helper that reads once, catches storage errors and persists changes. Create one local UI state:

```ts
type ReviewDrawerTab = "concerns" | "citations" | "agents" | "annotation";

const [drawerOpen, setDrawerOpen] = useStoredBoolean("peerassist.drawer.open", false);
const [drawerTab, setDrawerTab] = useState<ReviewDrawerTab>("concerns");
```

Opening from a concern/citation/agent/selection sets the corresponding tab. Migrate every existing inspector-open path in this task. The drawer remains open across page and view-mode changes until explicitly closed.

- [ ] **Step 3: Snapshot text selection before opening**

At `mouseup`, synchronously capture selected text, page, stable page/layer ID, start span index/offset and end span index/offset before setting drawer state. Preserve the existing draft `note` independently. Only after the snapshot is committed may the drawer open. Do not promise restoration of the browser-native blue selection after PDF page rerender.

- [ ] **Step 4: Build and verify TypeScript**

Run: `npm run build`

Working directory: `web/peerassist-workspace`

Expected: build passes.

- [ ] **Step 5: Commit**

```bash
git add web/peerassist-workspace/src/main.tsx web/peerassist-workspace/dist
git commit -m "feat: add durable review drawer state"
```

### Task 2: Restructure The Review Workspace

**Files:**
- Modify: `web/peerassist-workspace/src/main.tsx`
- Modify: `web/peerassist-workspace/src/styles.css`

- [ ] **Step 1: Replace the always-wide inspector shell**

Render a 52-72px bookmark rail when closed and a constrained drawer when open. Keep the PDF reader mounted in both states. Change the existing “专业审阅/agent” navigation so it opens the drawer `agents` tab and passes `agentRuns`; it must not switch `activeWindow` in a way that unmounts `PaperWindow` or `PdfReviewReader`.

- [ ] **Step 2: Add four drawer tabs**

Implement `待确认`, `引用核查`, `专业审阅`, and `人工批注`. Move existing content; do not duplicate business logic.

- [ ] **Step 3: Wire every open path**

Concern page marker -> concerns tab; citation highlight -> citations tab; agent navigation -> agents tab; text selection -> annotation tab.

- [ ] **Step 4: Build**

Run: `npm run build` in `web/peerassist-workspace`.

- [ ] **Step 5: Commit**

```bash
git add web/peerassist-workspace/src web/peerassist-workspace/dist
git commit -m "feat: make PDF the primary review surface"
```

## Chunk 2: Paper Visual System, Copy, And Browser Verification

### Task 3: Apply The Paper-Room Visual System

**Files:**
- Modify: `web/peerassist-workspace/src/styles.css`
- Modify: `web/peerassist-workspace/src/main.tsx`

- [ ] **Step 1: Define scoped color and spacing tokens**

Add warm paper, gray-green background, ink green, annotation red, evidence yellow and citation blue. Keep colors scoped to the workspace root and preserve readable contrast.

- [ ] **Step 2: Restyle navigation, reader and drawer**

Use the PDF as the only paper-shadow surface. Use borders/color strips for the rest. Keep radii at 0-6px and avoid nested cards.

- [ ] **Step 3: Add reduced-motion support**

Drawer and selection transitions use 120-220ms and are disabled under `prefers-reduced-motion`.

- [ ] **Step 4: Inspect CSS for prohibited one-note palette and overflow risks**

Run: `rg -n '#[0-9a-fA-F]{6}|border-radius|font-size' web/peerassist-workspace/src/styles.css`

Review the output manually against the design tokens.

- [ ] **Step 5: Build and commit**

Run: `npm run build` in `web/peerassist-workspace`.

```bash
git add web/peerassist-workspace/src web/peerassist-workspace/dist
git commit -m "style: apply PeerAssist paper-room workspace"
```

### Task 4: Rewrite Reviewer-Facing Chinese Copy

**Files:**
- Modify: `web/peerassist-workspace/src/main.tsx`
- Modify: `README.md`
- Modify: `docs/peerassist_operation_manual.md`

- [ ] **Step 1: Replace internal terms**

Use `专业审阅`, `待确认意见`, `审稿意见`, `当前使用本地证据规则`, `已切换为本地核查`, `已完成模型辅助审阅`, `已定位证据`, `发现关注点`, `等待逐条确认`, and `生成审稿报告`.

- [ ] **Step 2: Rewrite stage progress**

Use active Chinese phrases from design section 6. Preserve exact technical details in tooltips or operation manual, not in primary labels.

- [ ] **Step 3: Scan for stale copy**

Run:

```bash
rg -n "candidate concern|agent run|local fallback|model enhanced|evidence count|finding count|awaiting human confirmation|多代理结果|模型未启用|finalize|Level|Lv\.|经验|通关|奖励" web/peerassist-workspace/src README.md docs/peerassist_operation_manual.md
```

Expected: no stale user-facing strings; technical documentation references may remain when explicitly explained.

- [ ] **Step 4: Build and commit**

Run: `npm run build` in `web/peerassist-workspace`.

```bash
git add web/peerassist-workspace/src web/peerassist-workspace/dist README.md docs/peerassist_operation_manual.md
git commit -m "copy: clarify PeerAssist reviewer language"
```

### Task 5: Implement Responsive Drawer Behavior

**Files:**
- Modify: `web/peerassist-workspace/src/styles.css`
- Modify: `web/peerassist-workspace/src/main.tsx`

- [ ] **Step 1: Add desktop constraints**

Closed rail 52-72px; open drawer 360px with `min-width: 320px; max-width: 480px`; reader uses the remaining track with `minmax(0, 1fr)`.

- [ ] **Step 2: Add tablet overlay**

At widths from 701px through 1100px, render the drawer as a right overlay and add an accessible dismiss backdrop. Collapse the left navigation to icons. Do not remount or resize the PDF reader while the overlay is open.

- [ ] **Step 3: Add mobile bottom sheet**

At widths up to 700px, force single-page PDF, convert the drawer to a bottom sheet, include safe-area padding and cap height at 85dvh.

- [ ] **Step 4: Build and commit**

Run: `npm run build`.

```bash
git add web/peerassist-workspace/src web/peerassist-workspace/dist
git commit -m "feat: make the review drawer responsive"
```

### Task 6: Verify The Complete Browser Workflow

**Files:**
- Modify: `web/peerassist-workspace/package.json`
- Modify: `web/peerassist-workspace/package-lock.json`
- Create: `web/peerassist-workspace/playwright.config.ts`
- Create: `web/peerassist-workspace/e2e/paper-room.spec.ts`
- Modify: `docs/peerassist_lark_sync.md`
- Modify: `docs/论文审核辅助系统与方法论.md`

- [ ] **Step 1: Start/restart the production-like local services**

Install `@playwright/test` as a development dependency and add `test:e2e` script. Configure tests against `PEERASSIST_E2E_BASE_URL` with default `http://127.0.0.1:8766`. Use the existing systemd services or non-conflicting development ports and confirm `/api/health` before browser checks.

- [ ] **Step 2: Desktop Playwright verification**

At 1440x960: measure PDF width closed vs open; open from a page concern; confirm a concern; close/reopen; verify selected concern and highlight.

- [ ] **Step 3: Spread-mode verification**

At 1600x1000: render pages 2-3, click a right-page marker, verify exact right-page bbox and drawer concern.

- [ ] **Step 4: Selection snapshot verification**

Select PDF text, trigger annotation drawer, verify captured text/page survives ResizeObserver/text-layer rerender and draft input remains editable.

- [ ] **Step 5: Mobile verification**

At 390x844: verify one PDF page, bottom sheet, all text/buttons fit, no horizontal overflow, and no incoherent overlap.

Add a 900x1024 tablet case: opening the right overlay must not change the PDF canvas dimensions; backdrop closes the drawer; navigation is icon-only; no horizontal overflow.

- [ ] **Step 6: Canvas and console checks**

In `paper-room.spec.ts`, sample canvas pixels with `getImageData` and require at least 1% of sampled pixels to differ from the dominant background color. Create a real DOM `Range` across text-layer spans and assert `window.getSelection()?.toString().trim()` is non-empty. Assert browser console contains no errors.

- [ ] **Step 7: Release checks**

Run: `npm run build` in `web/peerassist-workspace`.

Run: `npm run test:e2e` in `web/peerassist-workspace`.

Run focused backend workspace tests only if API contracts changed.

- [ ] **Step 8: Update docs and Feishu**

Append the chosen A2 direction, implemented interactions, viewport evidence and remaining limitations. Use append-only Feishu update and never include credentials.

- [ ] **Step 9: Commit**

```bash
git add docs web/peerassist-workspace/dist
git commit -m "docs: record paper-room UI rollout"
```

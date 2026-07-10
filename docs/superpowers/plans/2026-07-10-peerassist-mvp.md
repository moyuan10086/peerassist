# PeerAssist MVP Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first runnable PeerAssist backend slice on top of FactReview: schemas, evidence ledger generation, deterministic checks, confirmation-aware report export, tool tracing, and CLI integration.

**Architecture:** Add a focused `peerassist` package under `src/peerassist/` and keep it independent from FactReview's existing review agent internals. The first slice consumes parse-stage artifacts and emits PeerAssist artifacts under `stages/peerassist/`, while preserving existing FactReview behavior when `--peerassist-mode off`.

**Tech Stack:** Python 3.11, Pydantic v2, pytest, existing FactReview pipeline helpers, Markdown/JSON artifacts.

---

## Chunk 1: Schemas And Package Wiring

### Task 1: Add PeerAssist schema models

**Files:**
- Create: `src/schemas/peerassist.py`
- Modify: `src/schemas/__init__.py`
- Modify: `pyproject.toml`
- Test: `tests/test_peerassist_schemas.py`

- [ ] **Step 1: Write failing schema tests**

Create tests that validate:

```python
from schemas.peerassist import (
    Concern,
    ConcernLevel,
    ConcernStatus,
    DeterministicCheck,
    DeterministicCheckApplicability,
    DeterministicCheckStatus,
    EvidenceItem,
    EvidenceLedger,
    EvidenceType,
    HumanConfirmationAction,
    ToolTraceEvent,
    ToolTraceStatus,
)


def test_evidence_ledger_requires_stable_item_ids():
    ledger = EvidenceLedger(
        paper_id="demo",
        source_sha256="abc",
        items=[
            EvidenceItem(
                id="P01-L001",
                type=EvidenceType.TEXT_SPAN,
                page=1,
                locator="page 1, line 1",
                text="The method uses a fixed seed.",
                source_path="mineru_full.md",
            )
        ],
    )
    assert ledger.items[0].id == "P01-L001"
    assert ledger.coverage["text_span"] == 1


def test_concern_without_evidence_cannot_be_confirmed():
    concern = Concern(
        id="concern_001",
        level=ConcernLevel.CLARIFICATION_NEEDED,
        category="method",
        title="Missing parameter details",
        impact="The method cannot be reproduced.",
        author_action="Please provide the parameter values.",
        status=ConcernStatus.PENDING_HUMAN_CONFIRMATION,
    )
    assert concern.evidence_ids == []
    assert concern.status == ConcernStatus.PENDING_HUMAN_CONFIRMATION


def test_tool_trace_event_has_required_lifecycle_fields():
    event = ToolTraceEvent(
        task_id="task",
        call_id="call",
        agent_id="statistics_agent",
        source="builtin",
        tool="percentage_check",
        status=ToolTraceStatus.STARTED,
        ts="2026-07-10T00:00:00Z",
    )
    assert event.status == ToolTraceStatus.STARTED
```

- [ ] **Step 2: Run tests and verify failure**

Run: `pytest tests/test_peerassist_schemas.py -q`

Expected: FAIL because `schemas.peerassist` does not exist.

- [ ] **Step 3: Implement minimal schema models**

Create enums and Pydantic models for:

- `EvidenceType`, `EvidenceItem`, `EvidenceLedger`
- `DeterministicCheckApplicability`, `DeterministicCheckStatus`, `DeterministicCheck`
- `ConcernLevel`, `ConcernStatus`, `Concern`
- `HumanConfirmationAction`
- `ToolTraceStatus`, `ToolTraceEvent`

`EvidenceLedger` should derive a `coverage` dict when not supplied.

- [ ] **Step 4: Run tests and verify pass**

Run: `pytest tests/test_peerassist_schemas.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add pyproject.toml src/schemas/__init__.py src/schemas/peerassist.py tests/test_peerassist_schemas.py
git commit -m "feat: add PeerAssist schemas"
```

## Chunk 2: Evidence Ledger

### Task 2: Build ledger from parse artifacts

**Files:**
- Create: `src/peerassist/__init__.py`
- Create: `src/peerassist/evidence_ledger.py`
- Create: `src/peerassist/stage_runner.py`
- Modify: `src/common/pipeline_context.py`
- Test: `tests/peerassist/test_evidence_ledger.py`

- [ ] **Step 1: Write failing ledger tests**

Create a test fixture with a small MinerU markdown string and optional
`content_list` JSON. Assert:

- line evidence IDs are stable: `P01-L001`
- figure labels are detected as `figure` or `figure_caption`
- table markdown rows create table evidence
- coverage records missing parser structures explicitly

- [ ] **Step 2: Run test and verify failure**

Run: `pytest tests/peerassist/test_evidence_ledger.py -q`

Expected: FAIL because `peerassist.evidence_ledger` does not exist.

- [ ] **Step 3: Implement ledger builder**

Implement:

```python
def build_evidence_ledger(
    *,
    paper_id: str,
    source_pdf: Path,
    mineru_markdown_path: Path | None,
    mineru_content_list_path: Path | None = None,
) -> EvidenceLedger:
    ...
```

Behavior:

- hash `source_pdf` when present
- convert non-empty markdown lines into `text_span` items
- assign stable line IDs by detected page and line number
- detect simple headings as `section`
- detect `Figure/Fig./Table/图/表` labels as figure/table/caption candidates
- record parser warnings in ledger metadata

- [ ] **Step 4: Run tests and verify pass**

Run: `pytest tests/peerassist/test_evidence_ledger.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/peerassist src/common/pipeline_context.py tests/peerassist/test_evidence_ledger.py
git commit -m "feat: build PeerAssist evidence ledger"
```

## Chunk 3: Deterministic Checks And Tool Trace

### Task 3: Add deterministic checks over ledger evidence

**Files:**
- Create: `src/peerassist/deterministic_checks.py`
- Create: `src/peerassist/tool_trace.py`
- Test: `tests/peerassist/test_deterministic_checks.py`
- Test: `tests/peerassist/test_tool_trace.py`

- [ ] **Step 1: Write failing deterministic check tests**

Tests:

- percentage/count consistency emits `lead` when `30/100` is paired with `40%`
- small or ambiguous samples emit `insufficient_evidence`
- Benford-like digit checks are not run on years/pages
- tool trace writes JSONL events with `started` and `completed`

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
pytest tests/peerassist/test_deterministic_checks.py tests/peerassist/test_tool_trace.py -q
```

Expected: FAIL because modules do not exist.

- [ ] **Step 3: Implement minimal deterministic checks**

Implement a deterministic runner:

```python
def run_deterministic_checks(ledger: EvidenceLedger) -> list[DeterministicCheck]:
    ...
```

Initial checks:

- percentage/count consistency from nearby text or table-cell items
- repeated exact table rows when table row metadata exists
- figure/table reference consistency from label counts
- reproducibility keyword absence as `inconclusive` unless relevant sections exist

Implement `ToolTraceRecorder` with append-only JSONL writes.

- [ ] **Step 4: Run tests and verify pass**

Run:

```bash
pytest tests/peerassist/test_deterministic_checks.py tests/peerassist/test_tool_trace.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/peerassist/deterministic_checks.py src/peerassist/tool_trace.py tests/peerassist
git commit -m "feat: add PeerAssist deterministic checks"
```

## Chunk 4: Concern Integration, Confirmation, And Export

### Task 4: Generate concerns and export confirmation-aware report

**Files:**
- Create: `src/peerassist/concerns.py`
- Create: `src/peerassist/confirmations.py`
- Create: `src/peerassist/report_export.py`
- Test: `tests/peerassist/test_concerns.py`
- Test: `tests/peerassist/test_report_export.py`

- [ ] **Step 1: Write failing tests**

Tests:

- deterministic `lead` becomes a pending concern with evidence IDs
- a concern with no evidence remains pending and is not exported as a major item
- `confirm` and `rewrite` actions affect exported Markdown
- `delete` action removes the concern from main report
- accusatory language guard rejects prohibited phrasing

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
pytest tests/peerassist/test_concerns.py tests/peerassist/test_report_export.py -q
```

Expected: FAIL because modules do not exist.

- [ ] **Step 3: Implement minimal concern and report logic**

Implement:

```python
def concerns_from_checks(checks: list[DeterministicCheck]) -> list[Concern]:
    ...

def apply_confirmations(concerns: list[Concern], actions: list[HumanConfirmationAction]) -> list[Concern]:
    ...

def export_peerassist_report(...)-> tuple[str, dict]:
    ...
```

Report sections:

- Paper summary placeholder from existing FactReview metadata when available
- Major concerns
- Minor concerns
- Clarifications requested
- Pending manual checks
- System limitations
- Provenance appendix

- [ ] **Step 4: Run tests and verify pass**

Run:

```bash
pytest tests/peerassist/test_concerns.py tests/peerassist/test_report_export.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/peerassist/concerns.py src/peerassist/confirmations.py src/peerassist/report_export.py tests/peerassist
git commit -m "feat: export PeerAssist review concerns"
```

## Chunk 5: Pipeline And CLI Integration

### Task 5: Wire PeerAssist into the existing pipeline

**Files:**
- Modify: `src/pipeline_full.py`
- Modify: `scripts/execute_review_pipeline.py` if needed
- Modify: `src/common/pipeline_context.py`
- Test: `tests/test_pipeline_config.py`
- Test: `tests/peerassist/test_stage_runner.py`

- [ ] **Step 1: Write failing integration tests**

Tests:

- default mode is `off`, preserving existing pipeline behavior
- `--peerassist-mode fast` creates `stages/peerassist/evidence_ledger.json`
- fast mode creates checks, concerns, confirmations, tool trace, and report
- invalid mode fails argument parsing

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
pytest tests/test_pipeline_config.py tests/peerassist/test_stage_runner.py -q
```

Expected: FAIL because CLI mode and stage runner are not wired.

- [ ] **Step 3: Implement pipeline wiring**

Add CLI argument:

```text
--peerassist-mode {off,fast,standard,deep}
```

For `fast`, run the local non-LLM PeerAssist backbone after parse. For
`standard` and `deep`, create the same artifacts and record warnings for
not-yet-implemented optional checks rather than silently pretending completion.

- [ ] **Step 4: Run focused tests**

Run:

```bash
pytest tests/test_pipeline_config.py tests/peerassist -q
```

Expected: PASS.

- [ ] **Step 5: Run existing fast test suite**

Run:

```bash
pytest -q
```

Expected: PASS or known external-marker skips only.

- [ ] **Step 6: Commit**

Run:

```bash
git add src/pipeline_full.py scripts/execute_review_pipeline.py src/common/pipeline_context.py src/peerassist tests
git commit -m "feat: wire PeerAssist fast mode into pipeline"
```

## Chunk 6: Evaluation Harness Skeleton

### Task 6: Add PeerAssist-Eval-v1 metric harness skeleton

**Files:**
- Create: `eval/PeerAssist-Eval-v1/manifest.example.json`
- Create: `src/peerassist/eval_metrics.py`
- Test: `tests/peerassist/test_eval_metrics.py`

- [ ] **Step 1: Write failing metric tests**

Tests:

- precision/recall calculation
- evidence binding rate
- unevidenced new-fact rate from concerns/report metadata
- stratified aggregation by domain and PDF type

- [ ] **Step 2: Run tests and verify failure**

Run: `pytest tests/peerassist/test_eval_metrics.py -q`

Expected: FAIL because `peerassist.eval_metrics` does not exist.

- [ ] **Step 3: Implement metric helpers**

Implement pure functions only. Do not create the full dataset.

- [ ] **Step 4: Run tests and verify pass**

Run: `pytest tests/peerassist/test_eval_metrics.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add eval/PeerAssist-Eval-v1/manifest.example.json src/peerassist/eval_metrics.py tests/peerassist/test_eval_metrics.py
git commit -m "feat: add PeerAssist evaluation metrics skeleton"
```

## Completion Checks For This MVP Plan

- [ ] `pytest tests/peerassist -q` passes.
- [ ] `pytest -q` passes or reports only externally marked skips.
- [ ] `python scripts/execute_review_pipeline.py demos/Graph/compgcn/paper.pdf --paper-key peerassist_smoke --peerassist-mode fast --teaser-mode prompt` produces PeerAssist artifacts.
- [ ] Existing FactReview default mode still works without PeerAssist artifacts unless explicitly enabled.
- [ ] `peerassist_report.md` contains no automatic accept/reject decision and no accusatory system language.

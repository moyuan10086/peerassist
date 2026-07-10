# PeerAssist Agents And Confirmation Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a typed multi-agent review backbone and a human confirmation review bundle so PeerAssist concerns flow through agent-result artifacts before report export.

**Architecture:** Keep the first agent implementation deterministic and local. Agents receive typed input packets, produce typed results, and can later be replaced by LLM/MCP-backed runners without changing concern integration or report export.

**Tech Stack:** Python 3.11, Pydantic v2, pytest, existing PeerAssist schemas and artifacts.

---

## Chunk 1: Agent Result Contracts

### Task 1: Add agent packet/result schemas

**Files:**
- Modify: `src/schemas/peerassist.py`
- Test: `tests/test_peerassist_schemas.py`

- [ ] **Step 1: Write failing tests**

Tests:

- `AgentInputPacket` stores agent id, mode, evidence ids, check ids, and capability names.
- `AgentConcernDraft` stores a candidate concern with evidence/check provenance.
- `AgentReviewResult` records status, warnings, and draft concerns.

- [ ] **Step 2: Run red tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_peerassist_schemas.py -q
```

Expected: FAIL because agent schemas do not exist.

- [ ] **Step 3: Implement schemas**

Add:

- `AgentRunStatus`
- `AgentInputPacket`
- `AgentConcernDraft`
- `AgentReviewResult`

- [ ] **Step 4: Run green tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_peerassist_schemas.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/schemas/peerassist.py tests/test_peerassist_schemas.py
git commit -m "feat: add PeerAssist agent result schemas"
```

## Chunk 2: Deterministic Agent Runner

### Task 2: Add local agent runner and result artifacts

**Files:**
- Create: `src/peerassist/agents.py`
- Modify: `src/peerassist/concerns.py`
- Test: `tests/peerassist/test_agents.py`
- Test: `tests/peerassist/test_concerns.py`

- [ ] **Step 1: Write failing tests**

Tests:

- `run_peerassist_agents` emits `statistics_agent` result for deterministic leads.
- `defense_agent` adds benign-explanation pressure-test text.
- `integrate_agent_results` returns pending concerns with `source_agent_ids`.
- failed agent result becomes pending manual-check concern instead of disappearing.

- [ ] **Step 2: Run red tests**

Run:

```bash
.venv/bin/python -m pytest tests/peerassist/test_agents.py tests/peerassist/test_concerns.py -q
```

Expected: FAIL because `peerassist.agents` does not exist.

- [ ] **Step 3: Implement local runner**

Implement:

- `build_agent_input_packet(...)`
- `run_peerassist_agents(...)`
- `integrate_agent_results(...)`

Fast mode should run:

- `statistics_agent`
- `defense_agent`
- `integrator_agent`

Standard/deep can include placeholder result statuses for other agents, but must
mark unsupported agents as incomplete rather than silently omitting them.

- [ ] **Step 4: Run green tests**

Run:

```bash
.venv/bin/python -m pytest tests/peerassist/test_agents.py tests/peerassist/test_concerns.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/peerassist/agents.py src/peerassist/concerns.py tests/peerassist/test_agents.py tests/peerassist/test_concerns.py
git commit -m "feat: add PeerAssist local agent runner"
```

## Chunk 3: Confirmation Review Bundle

### Task 3: Export human confirmation bundle

**Files:**
- Modify: `src/peerassist/confirmations.py`
- Modify: `src/peerassist/stage_runner.py`
- Test: `tests/peerassist/test_confirmations.py`
- Test: `tests/peerassist/test_stage_runner.py`

- [ ] **Step 1: Write failing tests**

Tests:

- `build_confirmation_bundle` groups concerns by status and includes evidence locators.
- stage runner writes `confirmation_bundle.json`.
- report JSON references the bundle path.

- [ ] **Step 2: Run red tests**

Run:

```bash
.venv/bin/python -m pytest tests/peerassist/test_confirmations.py tests/peerassist/test_stage_runner.py -q
```

Expected: FAIL because bundle helpers and stage artifact do not exist.

- [ ] **Step 3: Implement bundle export**

Add helper:

```python
def build_confirmation_bundle(*, concerns, evidence_lookup) -> dict: ...
```

Write the bundle in `run_peerassist_stage` after concerns are generated and
before final report export.

- [ ] **Step 4: Run green tests and focused suite**

Run:

```bash
.venv/bin/python -m pytest tests/peerassist -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/peerassist/confirmations.py src/peerassist/stage_runner.py tests/peerassist/test_confirmations.py tests/peerassist/test_stage_runner.py
git commit -m "feat: export PeerAssist confirmation bundle"
```

## Completion Checks

- [ ] `.venv/bin/python -m pytest tests/peerassist -q` passes.
- [ ] `.venv/bin/python -m pytest -q` passes.
- [ ] `peerassist_report.json` contains agent result provenance and confirmation bundle path.

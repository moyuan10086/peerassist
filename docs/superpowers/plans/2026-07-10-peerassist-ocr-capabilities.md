# PeerAssist OCR And Capabilities Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the provider and capability boundaries needed for OCR alternatives, Baidu/PaddleOCR adapters, and traceable MCP/Skills-style tool exposure.

**Architecture:** Keep OCR and capability management in `src/peerassist/` as typed, testable modules. The first implementation introduces provider metadata, a MinerU artifact adapter, explicit external-provider approval requirements, and a capability registry that filters schemas by mode and permission class before agents can see them.

**Tech Stack:** Python 3.11, Pydantic v2, pytest, existing PeerAssist schemas, append-only tool trace.

---

## Chunk 1: OCR Provider Boundary

### Task 1: Add parse provider contracts and MinerU adapter

**Files:**
- Create: `src/peerassist/ocr_providers.py`
- Modify: `src/peerassist/evidence_ledger.py`
- Test: `tests/peerassist/test_ocr_providers.py`
- Test: `tests/peerassist/test_evidence_ledger.py`

- [ ] **Step 1: Write failing provider tests**

Tests:

- `MinerUParseProvider` exposes `provider_name = "mineru"` and `external_upload_required = False`.
- `MinerUParseProvider.from_parse_payload(...)` returns Markdown/content paths and metadata.
- `BaiduDocumentParseProvider` and `PaddleOCRStructureProvider` exist as disabled adapters whose metadata says whether external upload is required.
- External providers require explicit enabled config before `resolve_parse_result` can return usable paths.

- [ ] **Step 2: Run red tests**

Run:

```bash
.venv/bin/python -m pytest tests/peerassist/test_ocr_providers.py -q
```

Expected: FAIL because `peerassist.ocr_providers` does not exist.

- [ ] **Step 3: Implement provider contracts**

Implement:

```python
class ParseProviderKind(StrEnum): ...
class ParseProviderMetadata(BaseModel): ...
class ParseProviderResult(BaseModel): ...
class MinerUParseProvider: ...
class PaddleOCRStructureProvider: ...
class BaiduDocumentParseProvider: ...
```

Provider result fields:

- `provider_name`
- `markdown_path`
- `content_list_path`
- `structured_json_path`
- `metadata`
- `warnings`
- `external_upload_required`
- `enabled`

The Baidu adapters are explicit stubs for now. They must fail closed with a clear
warning unless enabled by config and supplied credentials.

- [ ] **Step 4: Refactor ledger builder to accept provider metadata**

Add optional `provider_name`, `provider_metadata`, and `warnings` parameters to
`build_evidence_ledger`, preserving current callers.

- [ ] **Step 5: Run green tests**

Run:

```bash
.venv/bin/python -m pytest tests/peerassist/test_ocr_providers.py tests/peerassist/test_evidence_ledger.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add src/peerassist/ocr_providers.py src/peerassist/evidence_ledger.py tests/peerassist/test_ocr_providers.py tests/peerassist/test_evidence_ledger.py
git commit -m "feat: add PeerAssist OCR provider boundary"
```

## Chunk 2: Capability Registry

### Task 2: Add capability metadata and permission filtering

**Files:**
- Create: `src/peerassist/capabilities.py`
- Test: `tests/peerassist/test_capabilities.py`

- [ ] **Step 1: Write failing capability tests**

Tests:

- Built-in deterministic checks are visible in `fast` mode.
- External-upload OCR capabilities are hidden unless external tools are allowed.
- `approval_required` is true for file write, external request, manuscript upload, and code execution permission classes.
- Capability schemas expose only summary metadata, not full skill instructions.

- [ ] **Step 2: Run red tests**

Run:

```bash
.venv/bin/python -m pytest tests/peerassist/test_capabilities.py -q
```

Expected: FAIL because `peerassist.capabilities` does not exist.

- [ ] **Step 3: Implement registry**

Implement:

```python
class PermissionClass(StrEnum): ...
class CapabilitySource(StrEnum): ...
class CapabilitySpec(BaseModel): ...
class CapabilityRegistry: ...
```

Filtering inputs:

- `mode`: `fast`, `standard`, or `deep`
- `allow_external`: bool
- `allow_manuscript_upload`: bool
- `allow_code_execution`: bool

Default registered capabilities:

- `build_evidence_ledger`
- `percentage_consistency_check`
- `mineru_parse_artifacts`
- `paddleocr_structure_v3`
- `baidu_doc_parser`
- `baidu_paddleocr_vl`
- `baidu_unlimited_ocr`

- [ ] **Step 4: Run green tests**

Run:

```bash
.venv/bin/python -m pytest tests/peerassist/test_capabilities.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/peerassist/capabilities.py tests/peerassist/test_capabilities.py
git commit -m "feat: add PeerAssist capability registry"
```

## Chunk 3: Stage Runner Integration

### Task 3: Route PeerAssist stage through provider and registry

**Files:**
- Modify: `src/peerassist/stage_runner.py`
- Test: `tests/peerassist/test_stage_runner.py`

- [ ] **Step 1: Write failing integration tests**

Tests:

- stage runner output `peerassist_report.json` includes parse provider metadata.
- tool trace records provider resolution.
- `standard` mode records a warning when external OCR capabilities are not enabled.

- [ ] **Step 2: Run red tests**

Run:

```bash
.venv/bin/python -m pytest tests/peerassist/test_stage_runner.py -q
```

Expected: FAIL because stage runner does not include provider/registry metadata.

- [ ] **Step 3: Implement integration**

Use `MinerUParseProvider.from_parse_payload` to resolve paths in the current
stage runner. Add registry metadata to report payload warnings for disabled
external providers in `standard` and `deep` modes.

- [ ] **Step 4: Run green tests and focused suite**

Run:

```bash
.venv/bin/python -m pytest tests/peerassist -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/peerassist/stage_runner.py tests/peerassist/test_stage_runner.py
git commit -m "feat: trace PeerAssist parse provider resolution"
```

## Completion Checks

- [ ] `.venv/bin/python -m pytest tests/peerassist -q` passes.
- [ ] `.venv/bin/python -m pytest -q` passes.
- [ ] `--peerassist-mode fast` still produces all Phase 1 artifacts.
- [ ] External OCR providers fail closed unless explicitly enabled.

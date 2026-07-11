# PeerAssist Traceable Citation Audit Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first complete PeerAssist citation-audit loop from manuscript evidence extraction through deterministic verification, traceable findings, human-confirmable concerns, pipeline artifacts, bilingual reports, tests, and Chinese documentation.

**Architecture:** Add focused citation schema models and four small modules: extraction/linking, metadata normalization, verification adaptation, and audit/concern generation. The existing PeerAssist stage orchestrates these units and publishes `citation_audit.json` plus immutable verification-attempt artifacts; existing confirmation and report paths consume citation concerns without inventing facts.

**Tech Stack:** Python 3.11+, Pydantic v2, pytest, existing PeerAssist pipeline/storage/tool-trace APIs, Markdown/JSON artifacts.

**Design source:** `docs/superpowers/specs/2026-07-11-peerassist-citation-audit-design.md`

---

## Chunk 1: Citation Contracts And Manuscript Evidence

### Task 1: Add Strict Citation Audit Schemas

**Files:**
- Create: `src/schemas/citation.py`
- Modify: `src/schemas/__init__.py`
- Create: `tests/peerassist/test_citation_schemas.py`

- [ ] **Step 1: Write failing schema tests**

Add tests proving:

```python
def test_citation_audit_rejects_unknown_fields() -> None:
    payload = valid_citation_audit_payload()
    payload["unexpected"] = True
    with pytest.raises(ValidationError) as exc:
        CitationAudit.model_validate(payload)
    assert any(error["type"] == "extra_forbidden" and error["loc"] == ("unexpected",)
               for error in exc.value.errors())

def test_metadata_mismatch_requires_complete_trace() -> None:
    with pytest.raises(ValidationError, match="metadata_mismatch"):
        CitationAuditFinding(
            id="F-metadata_mismatch-deadbeef",
            status="metadata_mismatch",
            citation_link_ids=[],
            reference_record_ids=[],
            mention_evidence_ids=[],
            reference_evidence_ids=[],
            verification_ids=[],
        )
```

Use complete valid fixtures, then mutate one field per test. Cover nested `extra_forbidden`, exact schema version, all five verification statuses, and every finding trace invariant: `verified`, `metadata_mismatch`, `not_found`, verification-derived `ambiguous`, `insufficient_evidence`, `verification_failed`, `missing_reference`, and reference-only findings. Verify `missing_reference` permits no reference evidence but requires mention evidence and a link. `EvidenceType.CITATION` already exists in `schemas.peerassist`; add a regression assertion so it is not removed.

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest -q tests/peerassist/test_citation_schemas.py`

Expected: collection/import failure because citation models do not exist.

- [ ] **Step 3: Implement strict models and validators**

Add focused Pydantic models/enums in `schemas/citation.py` with `ConfigDict(extra="forbid")`; keep the existing shared PeerAssist schema file small. The exact owned fields are:

| Model | Required fields | Defaults / validation |
|---|---|---|
| `ReferenceRecord` | id, reference_number, source_evidence_ids, raw_text | title/doi empty, year null, parse_confidence 0; sources non-empty |
| `CitationLink` | id, mention_evidence_id, reference_number (positive int), status | reference IDs empty only for missing; linked requires exactly one; ambiguous requires at least two |
| `CitationFieldDifference` | field, manuscript/external raw and normalized values, comparison, rule | comparison strict enum |
| `CitationVerification` | id, reference_record_id, source, status, adapter, query, attempt_id/number, checked_at, tool_call_id | conditional match/source/response/error fields follow design §4.3 |
| `CitationAuditFinding` | id, status, severity, trace ID lists, message, requires_human_review | status-specific trace validator |
| `CitationAudit` | schema_version, paper_id, parse_version | records/links/verifications/findings/warnings default empty; `coverage: dict[str, int]` defaults empty; schema version exact |
| `UnsupportedCitationMarker` | id, source_evidence_id, raw, start, end, status, error_code | status fixed to unsupported_syntax; offsets non-negative and ordered |
| `CitationEvidenceResult` | mentions, references, links | `unsupported_markers: list[UnsupportedCitationMarker]` and warnings default empty |
| `CitationAdapterInfo` | name, version | both non-empty |
| `CitationSourceRecord` | id, url | required only for completed unique match |
| `CitationMatch` | method, candidate_count, selected_candidate_id, selection_reason, candidate_ids | selected candidate required only for completed; absent for not_found/ambiguous |
| `RawResponseArtifact` | path, sha256 | SHA-256 is 64 lowercase hex characters |

```python
class CitationLinkStatus(StrEnum):
    LINKED = "linked"
    MISSING_REFERENCE = "missing_reference"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED_SYNTAX = "unsupported_syntax"

class VerificationStatus(StrEnum):
    COMPLETED = "completed"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"

class CitationFindingStatus(StrEnum):
    VERIFIED = "verified"
    METADATA_MISMATCH = "metadata_mismatch"
    MISSING_REFERENCE = "missing_reference"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    VERIFICATION_FAILED = "verification_failed"
    UNCITED_REFERENCE = "uncited_reference"
    MALFORMED_REFERENCE = "malformed_reference"
    DUPLICATE_REFERENCE_METADATA = "duplicate_reference_metadata"
```

Implement the listed models plus strict enums for comparison and severity. `CitationVerification.query` and `observed_metadata` are JSON dictionaries; `match`, `source_record`, `raw_response_artifact`, and `error_code` use the nested contracts above and obey design §4.3. Validators enforce type-specific traceability. Re-export citation models from `schemas/__init__.py`; citation modules import from `schemas.citation` directly.

- [ ] **Step 4: Run schema tests and verify GREEN**

Run: `pytest -q tests/peerassist/test_citation_schemas.py`

Expected: all tests pass.

- [ ] **Step 5: Run existing schema consumers**

Run: `pytest -q tests/peerassist/test_evidence_ledger.py tests/peerassist/test_confirmations.py`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/schemas/citation.py src/schemas/__init__.py tests/peerassist/test_citation_schemas.py
git commit -m "feat: add strict citation audit contracts"
```

### Task 2: Extract Citation Mentions And Reference Records

**Files:**
- Create: `src/peerassist/citation_evidence.py`
- Create: `tests/peerassist/test_citation_evidence.py`
- Modify: `src/peerassist/evidence_ledger.py`

- [ ] **Step 1: Write failing extraction tests**

Cover:

```python
def test_extracts_and_expands_numeric_citations() -> None:
    ledger = ledger_with_text("Prior work [1, 3-5] established this.")
    result = extract_citation_evidence(ledger)
    assert [link.reference_number for link in result.links] == [1, 3, 4, 5]
    assert all(item.page == 2 and item.section == "Related Work" for item in result.mentions)

def test_mention_inherits_bbox_and_uses_stable_offset_id() -> None:
    result = extract_citation_evidence(ledger_with_bbox("See [7].", [1, 2, 3, 4]))
    assert result.mentions[0].id == "C-P02-L004-4-7-7"
    assert result.mentions[0].bbox == [1, 2, 3, 4]
```

Add exact cases: `[5-3]`, `[1-]`, and `[1-101]` each yield one `UnsupportedCitationMarker` and no links; `95% CI [1, 3]` yields no citation; two *different* reference texts numbered `[1]` yield an `ambiguous` link with two record IDs; missing `[9]` yields `missing_reference`; two identical `[1]` entries aggregate one record with both source IDs; caption/table contexts retain source type and lower confidence; multiline continuation is included in `raw_text`; duplicate normalized DOI values stay separate and add a warning. For raw text `[1] Alpha Study. 2020. doi:10.1/a`, normalized to `[1] alpha study. 2020. doi:10.1/a`, assert reference ID `R-1-61339f76`; for `See [1].` in `P02-L004`, assert mention ID `C-P02-L004-4-7-1` and link ID `citation-link-C-P02-L004-4-7-1`. Also assert sentence, source path, page, section, bbox, offsets, and citation coverage.

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest -q tests/peerassist/test_citation_evidence.py`

Expected: import failure because `citation_evidence` does not exist.

- [ ] **Step 3: Implement extraction and deterministic IDs**

Implement pure functions with these concrete interfaces:

```python
def extract_citation_evidence(ledger: EvidenceLedger) -> CitationEvidenceResult:
    """Return extracted mentions, records, links, warnings, and unsupported markers."""

def parse_numeric_citation(raw: str, *, max_range: int = 100) -> NumericCitationParse:
    """Return numbers plus status and error_code; never partially expand invalid ranges."""

def build_reference_records(items: list[EvidenceItem]) -> list[ReferenceRecord]:
    """Build canonical records and aggregate identical source occurrences."""

def build_citation_links(
    mentions: list[EvidenceItem], references: list[ReferenceRecord]
) -> list[CitationLink]:
    """Link only by explicit numeric reference number."""
```

Define `NumericCitationParse` in `citation_evidence.py` with `numbers`, `status`, `raw`, and `error_code`. Generate citation evidence by inheriting source location and storing source ID, offsets, marker, source type, confidence, and number. Unsupported markers become `UnsupportedCitationMarker` records plus warnings and are not links because no valid number exists. Group multiline references conservatively. Apply exact design ID formulas; do not call an LLM or network service.

- [ ] **Step 4: Merge citation items into the ledger**

Add a small helper in `evidence_ledger.py` that returns a copied ledger with extracted citation items and recomputed coverage. Do not mutate caller-owned models.

- [ ] **Step 5: Run extraction tests and verify GREEN**

Run: `pytest -q tests/peerassist/test_citation_evidence.py tests/peerassist/test_evidence_ledger.py`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/peerassist/citation_evidence.py src/peerassist/evidence_ledger.py tests/peerassist/test_citation_evidence.py
git commit -m "feat: extract traceable manuscript citations"
```

## Chunk 2: Metadata Verification And Citation Findings

### Task 3: Normalize Metadata And Adapt Existing Refcheck Results

**Files:**
- Create: `src/peerassist/citation_metadata.py`
- Create: `src/peerassist/citation_verification.py`
- Create: `tests/peerassist/test_citation_verification.py`

- [ ] **Step 1: Write failing normalization and adapter tests**

Cover exact DOI normalization, Unicode/title normalization, title similarity boundaries (`0.85`, `0.9499`, and `0.95`), online/print year aliases, no candidate, multiple candidates, unavailable input, malformed adapter schema, and immutable response hashing. Add deterministic retry cases: timeout then success uses attempts 1 and 2; three timeouts stop at 3; `not_found` and schema error do not retry; a valid completed attempt is reused; all failed files remain; a later failure does not override an earlier valid terminal; among multiple valid terminals separated by failures, the valid record with the greatest `attempt_number` is authoritative regardless of input ordering.

Representative assertion:

```python
def test_exact_doi_does_not_hide_year_mismatch(tmp_path: Path) -> None:
    verification = verify_reference(
        reference_record(doi="https://doi.org/10.X/A", year=2023),
        OfflineMetadataVerifier([{ "doi": "10.x/a", "year": 2024, "title": "A Study" }]),
        artifact_dir=tmp_path,
    )
    assert any(diff.field == "year" and diff.comparison == "mismatch"
               for diff in verification.field_differences)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest -q tests/peerassist/test_citation_verification.py`

Expected: import failure because verification functions do not exist.

- [ ] **Step 3: Implement pure normalization and comparison functions**

Implement `normalize_doi`, `normalize_title`, `title_similarity`, and `compare_reference_metadata` in `citation_metadata.py`. Keep normalization rules explicit and deterministic so adapter execution and persistence remain separate.

- [ ] **Step 4: Implement adapters and immutable attempts**

Define a small adapter protocol and implement:

```python
class CitationMetadataAdapter(Protocol):
    name: str
    version: str
    def lookup(self, record: ReferenceRecord) -> MetadataLookupResult:
        """Return candidates and raw response, or raise a classified adapter error."""

class ExistingRefcheckAdapter:
    """Map an existing reference_check.json payload without network calls."""

class OfflineMetadataVerifier:
    """Deterministic offline adapter used by tests and frozen fixtures."""
```

Implement `verify_reference_with_retries` and `select_authoritative_verification` here. Default maximum attempts is three; retry only timeout, rate-limit, and transient 5xx classifications. Write every received response atomically to `citation_verifications/attempt-<attempt_id>.json`, calculate SHA-256, increment attempt number, and retain all attempts. Stable verification IDs do not contain attempt IDs. Reuse valid completed attempts by idempotency key. Valid terminal statuses (`completed`, `not_found`, `ambiguous`, `unavailable`) outrank failures; only all-failed attempts produce authoritative `failed`. Missing adapter/input yields `unavailable`; malformed payload yields `failed` with `adapter_schema_error` and no retry.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `pytest -q tests/peerassist/test_citation_verification.py`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/peerassist/citation_metadata.py src/peerassist/citation_verification.py tests/peerassist/test_citation_verification.py
git commit -m "feat: add auditable citation metadata verification"
```

### Task 4: Produce Findings With Referential Integrity

**Files:**
- Create: `src/peerassist/citation_audit.py`
- Create: `tests/peerassist/test_citation_audit.py`

- [ ] **Step 1: Write failing audit-decision tests**

Create parameterized tests for every decision outcome and precedence rule:

```python
@pytest.mark.parametrize(("verification_status", "comparable", "mismatch", "expected"), [
    ("completed", 2, False, "verified"),
    ("completed", 1, False, "insufficient_evidence"),
    ("completed", 3, True, "metadata_mismatch"),
    ("not_found", 0, False, "not_found"),
    ("ambiguous", 0, False, "ambiguous"),
    ("unavailable", 0, False, "insufficient_evidence"),
    ("failed", 0, False, "verification_failed"),
])
def test_finding_decision_precedence(
    verification_status: str, comparable: int, mismatch: bool, expected: str
) -> None:
    verification = verification_fixture(
        status=verification_status,
        comparable_fields=comparable,
        has_mismatch=mismatch,
    )
    assert finding_status_for(linked_link(), verification).value == expected
```

Add named tests `test_link_status_precedes_verification`, `test_reference_only_findings`, `test_finding_id_is_stable_for_canonical_trace`, `test_rejects_response_hash_mismatch`, and a parameterized `test_rejects_each_dangling_trace_id` over link/reference/evidence/verification IDs. Authoritative attempt selection is owned and tested by Task 3; Task 4 only consumes the selected verification.

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest -q tests/peerassist/test_citation_audit.py`

Expected: import failure because audit engine does not exist.

- [ ] **Step 3: Implement audit decisions and ID generation**

Implement pure functions that consume evidence results and verification records, apply the exact ordered decision table, and produce `CitationAuditFinding` models with type-specific trace fields.

- [ ] **Step 4: Implement integrity validation and atomic index writing**

Implement `validate_citation_audit(audit, ledger, artifact_root) -> None` and `write_citation_audit_atomic(path, audit, ledger) -> Path`. The first raises `CitationAuditIntegrityError` with a stable error code; the second validates before atomically replacing the canonical index.

Validation checks every evidence/link/reference/verification ID and every immutable response hash before replacing the canonical `citation_audit.json`.

- [ ] **Step 5: Run audit tests and verify GREEN**

Run: `pytest -q tests/peerassist/test_citation_audit.py tests/peerassist/test_citation_schemas.py`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/peerassist/citation_audit.py tests/peerassist/test_citation_audit.py
git commit -m "feat: build traceable citation audit findings"
```

### Task 5: Convert Findings Into Conservative Review Concerns

**Files:**
- Create: `src/peerassist/citation_concerns.py`
- Create: `tests/peerassist/test_citation_concerns.py`
- Modify: `src/peerassist/agents.py`
- Modify: `tests/peerassist/test_agents.py`

- [ ] **Step 1: Write failing concern-generation tests**

Verify that `metadata_mismatch`, `missing_reference`, `not_found`, verification-derived `ambiguous`, `insufficient_evidence`, and `verification_failed` produce Chinese-neutral, evidence-bound pending concerns; `verified` produces no concern; reference-only findings default to audit-only or minor concerns according to policy. Assert concern metadata includes `citation_finding_ids`, `citation_link_ids`, `reference_record_ids`, `verification_ids`, response artifact path/hash when present, error code when failed, and audit schema/parse version.

Add negative assertions that titles/body never contain “虚假引用”, “造假”, “实锤”, automatic accept/reject language, or invented DOI/title/year.

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest -q tests/peerassist/test_citation_concerns.py`

Expected: import failure because concern conversion does not exist.

- [ ] **Step 3: Implement deterministic conversion**

Implement `concerns_from_citation_audit(audit, *, source_agent_id) -> list[Concern]`. This is the single authoritative citation-concern creation path. Derive evidence IDs and provenance only from findings/verification records and preserve finding IDs in metadata. Use explicit templates per status; no LLM calls.

- [ ] **Step 4: Enforce uniqueness and agent attribution**

Assert returned concern IDs are unique and stable. Remove `_citation_agent` draft generation from `run_peerassist_agents`; standard/deep retain a completed citation-agent run record with metadata `source="citation_audit"` and zero drafts for observability. The stage calls this converter exactly once: fast mode passes `source_agent_id="citation_audit"`; standard/deep passes `source_agent_id="citation_agent"`. Agent tests assert no citation draft and exactly one authoritative citation-concern path in every mode.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `pytest -q tests/peerassist/test_citation_concerns.py tests/peerassist/test_agents.py tests/peerassist/test_report_export.py`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/peerassist/citation_concerns.py src/peerassist/agents.py tests/peerassist/test_citation_concerns.py tests/peerassist/test_agents.py
git commit -m "feat: generate conservative citation concerns"
```

## Chunk 3: Pipeline, Tool Trace, Confirmation, And Reports

### Task 6: Reconcile Citation Confirmations Across Audit Versions

**Files:**
- Modify: `src/schemas/peerassist.py`
- Modify: `src/peerassist/confirmations.py`
- Create: `tests/peerassist/test_citation_confirmation_reconciliation.py`

- [ ] **Step 1: Write failing reconciliation tests**

Create tests proving a `HumanConfirmationAction` records `citation_finding_ids`, `audit_version`, previous/new text, reviewer, UTC timestamp, reason, and metadata. Test that an unchanged finding ID replays the prior action; a vanished finding or changed evidence/parse version produces `needs_reconciliation`; historical action rows remain byte-for-byte present and are never overwritten.

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest -q tests/peerassist/test_citation_confirmation_reconciliation.py`

Expected: failure because citation action fields and reconciliation do not exist.

- [ ] **Step 3: Extend the action contract compatibly**

Add optional `citation_finding_ids: list[str]`, `audit_version: str`, and `reconciliation_status` fields with defaults so existing confirmation files still validate. Enforce a UTC-aware timestamp when citation finding IDs are present.

- [ ] **Step 4: Implement reconciliation outside the stage runner**

Add `reconcile_citation_confirmations(concerns, actions, audit) -> CitationConfirmationReconciliation`. It returns replayable actions, unresolved historical actions, and concern IDs requiring reconciliation; it never mutates or deletes input history.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `pytest -q tests/peerassist/test_citation_confirmation_reconciliation.py tests/peerassist/test_confirmations.py tests/peerassist/test_confirmation_workflow.py`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/schemas/peerassist.py src/peerassist/confirmations.py tests/peerassist/test_citation_confirmation_reconciliation.py
git commit -m "feat: reconcile citation confirmation history"
```

### Task 7: Integrate Citation Audit Into PeerAssist Stage

**Files:**
- Create: `src/peerassist/citation_pipeline.py`
- Modify: `src/peerassist/stage_runner.py`
- Modify: `tests/peerassist/test_stage_runner.py`

- [ ] **Step 1: Write failing pipeline integration test**

Create a paper fixture containing a body citation and numbered reference plus an optional existing `reference_check.json`. Assert the stage writes:

- `citation_audit.json`
- immutable attempt JSON when an external payload was actually adapted
- ledger citation evidence with inherited location
- citation findings and concerns with valid IDs
- `tool_trace.jsonl` events for extraction, linking, verification, audit validation, and artifact creation; each event asserts task ID, call ID, source/tool, status, artifact IDs, evidence IDs, duration, and error code
- output mappings and report payload paths
- no duplicate concern IDs, and citation concerns were added exactly once

Add a failure-path fixture where the adapter schema is invalid. Assert a `failed` trace event has `adapter_schema_error`, the audit finding is `verification_failed`, and the overall PeerAssist stage still succeeds with a pending manual item.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `pytest -q tests/peerassist/test_stage_runner.py::test_run_peerassist_stage_writes_traceable_citation_audit`

Expected: failure because output/artifact does not exist.

- [ ] **Step 3: Add orchestration without embedding domain logic**

Implement a focused `run_citation_pipeline(repo_root, run_dir, paper_key, ledger, trace, mode) -> CitationPipelineResult` coordinator in `citation_pipeline.py`; its result contains the augmented ledger, validated audit, audit path, verification artifact paths, concerns, and warnings. Keep extraction, comparison, audit, and concern logic in their owned modules. Call it after base ledger creation and before deterministic checks/agents. Resolve the existing refcheck artifact from `refcheck_stage_dir(run_dir) / "reference_check.json"`; missing file becomes an `unavailable` verification rather than stage failure. Record start/completed/failed/artifact-created events with all required fields.

- [ ] **Step 4: Preserve human-confirmation behavior**

Merge the coordinator's citation concerns exactly once with integrated non-citation agent concerns before creating `confirmation_bundle.json`. Call Task 6 reconciliation before applying confirmations; write unresolved reconciliation state into the confirmation queue without altering `human_confirmations.json` history.

- [ ] **Step 5: Run stage tests and verify GREEN**

Run: `pytest -q tests/peerassist/test_stage_runner.py tests/peerassist/test_tool_trace.py tests/peerassist/test_confirmation_workflow.py`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/peerassist/citation_pipeline.py src/peerassist/stage_runner.py tests/peerassist/test_stage_runner.py
git commit -m "feat: integrate citation audit pipeline artifacts"
```

### Task 8: Expose Citation Provenance In Bilingual Reports

**Files:**
- Modify: `src/peerassist/report_export.py`
- Modify: `tests/peerassist/test_report_export.py`
- Modify: `tests/peerassist/test_stage_runner.py`

- [ ] **Step 1: Write failing report tests**

Assert English and Chinese reports include a concise citation-audit provenance section with totals by status, audit artifact path, and neutral wording for `not_found`, `unavailable`, and `verification_failed`. Assert report JSON includes `citation_audit_path`, `citation_audit_summary`, and every citation concern has `metadata.citation_finding_ids`; raw external responses are never embedded. Validate the audit input through `CitationAudit.model_validate` before export.

- [ ] **Step 2: Run report tests and verify RED**

Run: `pytest -q tests/peerassist/test_report_export.py -k citation`

Expected: failure because citation provenance fields/section do not exist.

- [ ] **Step 3: Extend report export API**

Add optional citation summary/path parameters with backward-compatible defaults. Render status counts and limitations, not unconfirmed accusations. Keep raw response artifacts referenced by path/hash only.

- [ ] **Step 4: Run report and stage tests and verify GREEN**

Run: `pytest -q tests/peerassist/test_report_export.py tests/peerassist/test_stage_runner.py`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/peerassist/report_export.py tests/peerassist/test_report_export.py tests/peerassist/test_stage_runner.py
git commit -m "feat: report citation audit provenance"
```

## Chunk 4: Documentation, Regression Verification, And Append-Only Sync

### Task 9: Update Chinese Technical And Operation Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/peerassist_operation_manual.md`
- Create: `docs/peerassist_citation_audit.md`
- Modify: `docs/peerassist_lark_sync.md`
- Modify: `web/peerassist-workspace/README.md`

- [ ] **Step 1: Inspect current docs and preserve unrelated edits**

Run: `git diff -- README.md docs/peerassist_lark_sync.md docs/peerassist_operation_manual.md web/peerassist-workspace/README.md`

Expected: existing uncommitted documentation changes are visible and retained.

- [ ] **Step 2: Document the implemented workflow in Chinese**

Assign distinct responsibilities: root README gives capability/status overview; operation manual gives reviewer workflow and human actions; `docs/peerassist_citation_audit.md` is the normative Chinese technical guide for schemas, stable IDs, normalization rules, verification adapters, immutable attempts, integrity validation, retries and degradation; frontend README explains artifact fields consumed by the workspace; lark sync file remains append-only chronology. Describe supported syntax, artifact paths, privacy boundary, and remaining limits (author-year, retraction/PubPeer, semantic support, frozen evaluation data).

- [ ] **Step 3: Add an operator verification recipe**

Include commands for running citation unit tests, a PeerAssist stage, inspecting `citation_audit.json`, checking immutable response hashes, and locating citation concerns in the confirmation queue.

- [ ] **Step 4: Validate documentation and secret hygiene**

Run:

```bash
git diff --check
rg -n 'ghp_[A-Za-z0-9]+|sk-[A-Za-z0-9]+' README.md docs web/peerassist-workspace/README.md
```

Expected: `git diff --check` succeeds; secret scan returns no matches.

- [ ] **Step 5: Commit documentation**

```bash
git add README.md docs/peerassist_operation_manual.md docs/peerassist_citation_audit.md docs/peerassist_lark_sync.md web/peerassist-workspace/README.md
git commit -m "docs: document peerassist citation audit workflow"
```

### Task 10: Run Full Verification And Append Feishu Record

**Files:**
- Modify: `docs/peerassist_lark_sync.md`

- [ ] **Step 1: Run focused PeerAssist tests**

Run: `pytest -q tests/peerassist`

Expected: all PeerAssist tests pass.

- [ ] **Step 2: Run the broader regression suite**

Run: `pytest -q`

Expected: all repository tests pass. Any failure blocks completion unless it is independently reproduced on the pre-implementation commit `47e6d1c`; record the failing test, both commands and outputs in `docs/peerassist_lark_sync.md`, while focused citation tests must still pass.

For a failure only, reproduce non-destructively in a detached temporary worktree:

```bash
git worktree add --detach /tmp/peerassist-baseline-47e6d1c 47e6d1c
pytest -q 2>&1 | tee /tmp/peerassist-current-pytest.log
cd /tmp/peerassist-baseline-47e6d1c
pytest -q 2>&1 | tee /tmp/peerassist-baseline-pytest.log
cd /root/peerassist-review-system-20260710/peerassist
git worktree remove /tmp/peerassist-baseline-47e6d1c
```

Expected: only failures present with the same test ID and failure signature in both logs qualify as pre-existing. Different/new failures remain blocking. Do not use checkout/reset on the working branch.

- [ ] **Step 3: Validate generated artifacts on a fixed local fixture**

Run:

```bash
pytest -q \
  tests/peerassist/test_stage_runner.py::test_run_peerassist_stage_writes_traceable_citation_audit \
  tests/peerassist/test_citation_audit.py::test_rejects_dangling_ids_and_response_hash_mismatch \
  tests/peerassist/test_report_export.py::test_reports_citation_provenance_in_both_languages \
  tests/peerassist/test_citation_confirmation_reconciliation.py
```

Expected: all tests pass. These tests validate JSON through Pydantic, resolve every finding ID, verify response hashes, assert bilingual report sections, and assert the confirmation queue/reconciliation behavior on fixed temporary fixtures.

- [ ] **Step 4: Verify the public service remains healthy**

Run: `curl -fsS -o /dev/null -w '%{http_code}\n' http://101.47.158.17:8766/`

Expected: `200`. This verifies availability only; it does not claim the running process has reloaded new backend code unless deployment/restart evidence proves that separately.

- [ ] **Step 5: Confirm the Feishu section is not already present**

Run:

```bash
lark-cli docs +fetch \
  --as user \
  --doc 'https://my.feishu.cn/docx/XuVIdkaGgoykehxox9Kc3Qnhnw2' \
  --scope keyword \
  --keyword '六十一、2026-07-11 可追溯证据定位与引用核查第一版' \
  --detail simple
```

Expected: zero matching blocks. If one exact section already exists, skip append and proceed to full fetch; if multiple exist, stop and report the duplicate rather than appending again.

- [ ] **Step 6: Append to the fixed Feishu document**

After reading the required lark-doc update references, run exactly one append command (never overwrite):

```bash
lark-cli docs +update \
  --as user \
  --doc 'https://my.feishu.cn/docx/XuVIdkaGgoykehxox9Kc3Qnhnw2' \
  --command append \
  --content "<h2>六十一、2026-07-11 可追溯证据定位与引用核查第一版</h2><p>目标：完成正文引用、参考文献、外部核验、人工确认和报告之间的可追溯闭环。</p><p>实现：新增严格引用契约、数字型引用提取、元数据规范化、不可变核验尝试、保守审计结论、确认历史协调及中英文报告溯源。</p><p>验证：PeerAssist 聚焦测试与仓库回归测试已按本地记录执行；当前提交 $(git rev-parse --short HEAD)。</p><p>服务：http://101.47.158.17:8766/</p><p>限制：作者年份制、撤稿/PubPeer、语义支持关系与冻结测试集指标仍待后续实现和实测。</p>"
```

Expected: command returns success and a revision greater than 92. The content contains no credentials and section title `六十一` appears once.

- [ ] **Step 7: Re-fetch the appended section**

Run:

```bash
lark-cli docs +fetch \
  --as user \
  --doc 'https://my.feishu.cn/docx/XuVIdkaGgoykehxox9Kc3Qnhnw2' \
  --scope full \
  --detail simple
```

Expected: output contains both the prior section `六十、2026-07-11 PeerAssist 操作手册与中文 README 更新` and the new `六十一、2026-07-11 可追溯证据定位与引用核查第一版`; new section occurs once. Append the returned revision and timestamp to `docs/peerassist_lark_sync.md` without changing prior entries.

- [ ] **Step 8: Commit the final local Feishu revision record**

Run:

```bash
git add docs/peerassist_lark_sync.md
git commit -m "docs: record citation audit feishu sync"
```

Expected: commit succeeds if the revision entry changed; if Task 9 already contained the exact returned revision, verify `git diff --exit-code -- docs/peerassist_lark_sync.md` and do not create an empty commit.

- [ ] **Step 9: Final repository audit**

Run:

```bash
git status --short --branch
git log --oneline --decorate -12
git diff --check
rg -n 'ghp_[A-Za-z0-9]+|sk-[A-Za-z0-9]+' README.md docs src tests web/peerassist-workspace/README.md
```

Expected: implementation commits are present; `git diff --check` succeeds; secret scan has no matches; no generated dependency churn; any intentionally retained user changes are named explicitly.

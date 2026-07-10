# PeerAssist Design

Date: 2026-07-10

## Source Of Truth

This design implements the product and methodology described in
`/root/peerassist-review-system-20260710/论文审核辅助系统与方法论.md`.

PeerAssist is not a misconduct detector, an automatic accept/reject system, or
a replacement for peer reviewers. Its purpose is to turn the mechanical parts
of review into structured, evidenced, traceable work: parsing papers, locating
evidence, running deterministic checks, generating review concerns, preserving
tool provenance, and letting a human reviewer confirm each item.

The long-term goal remains the full objective:

- Use DEFENSE-SEU/FactReview as the code base.
- Add evidence-grounded review, deterministic checks, multi-agent review,
  traceable MCP/Skills calls, and per-concern human confirmation.
- Evaluate on `PeerAssist-Eval-v1` with the target metrics from the methodology
  document.
- Validate real-world benefit with reviewer crossover experiments.

## Reference Projects And License Boundaries

FactReview is the implementation base and is licensed AGPL-3.0. PeerAssist
changes stay inside this AGPL-3.0 repository.

Reference projects are used for architecture cues, not copied code:

- DEFENSE-SEU/FactReview, AGPL-3.0: current pipeline, claim evidence, reference
  checking, execution comparison, report generation.
- cylqwe7855-alt/research-integrity-auditor, MIT: evidence ledger shape,
  deterministic numeric leads, MinerU artifact use, cautious risk language.
- scienceverse/metacheck, AGPL-3.0: modular research-output checks, Crossref,
  PubPeer, RetractionWatch, replication and power-analysis style checks.
- allenai/marg-reviewer, Apache-2.0: specialized multi-agent review generation,
  comment evaluation, reviewer-study framing.
- openai/codex, Apache-2.0: task/event protocol, tool lifecycle, approvals,
  dynamic tool exposure, skill loading.
- Dimillian/CodexMonitor, MIT: run monitoring concepts.
- Haleclipse/CodexDesktop-Rebuild: interaction concepts only unless a compatible
  license is confirmed.
- PaddleOCR / PP-StructureV3: local document parsing reference for layout,
  table, formula, chart, reading-order, Markdown, and JSON outputs.
- Baidu AI Cloud OCR document parsing APIs: optional cloud OCR provider for
  document parsing, PaddleOCR-VL, and Unlimited-OCR style asynchronous document
  parsing when users explicitly enable external upload.

## Approved Direction

PeerAssist will follow a progressive FactReview extension rather than a rewrite.
The current FactReview flow remains the spine:

```text
parse -> refcheck -> positioning -> execution -> report -> teaser
```

PeerAssist adds a review-governance layer around that spine:

```text
input materials
  -> FactReview parse / MinerU
  -> evidence ledger
  -> deterministic checks
  -> specialized review agents
  -> defense explanations
  -> concern integration
  -> human confirmation ledger
  -> bilingual Markdown / JSON report
  -> PeerAssist-Eval-v1 metrics harness
```

## OCR And Document Parsing Providers

PeerAssist keeps OCR behind a provider boundary. The default implementation
continues to use the current FactReview parse path and MinerU artifacts. Baidu
and PaddleOCR options are adapters, not hard dependencies.

Provider requirements:

- Return Markdown and structured JSON when available.
- Preserve page numbers, reading order, layout block types, table cells,
  formulas, captions, image references, and coordinates or polygons when the
  provider supplies them.
- Record provider name, provider version if known, request mode, source hash,
  page count, language hints, timing, and warnings in parse metadata.
- Emit coverage metadata so downstream stages know which item types are
  trustworthy and which are missing or parser-uncertain.
- Never silently upload manuscripts to a third-party OCR provider. External OCR
  requires explicit configuration and must produce `approval_required` and
  `completed` or `failed` events in `tool_trace.jsonl`.

Initial provider plan:

- `mineru`: current default path used by FactReview.
- `paddleocr_structure_v3`: local/self-hosted adapter inspired by PP-StructureV3
  for complex PDF and document-image parsing. It should map layout, table,
  formula, chart, and Markdown/JSON outputs into PeerAssist ledger items.
- `baidu_doc_parser`: optional asynchronous Baidu OCR document parsing adapter.
  It should submit a task, poll for completion, ingest Markdown/JSON results,
  and record QPS/rate-limit or timeout failures as explicit parse warnings.
- `baidu_paddleocr_vl`: optional Baidu-hosted PaddleOCR-VL adapter for complex
  multimodal documents, especially scanned, multilingual, table/formula-heavy,
  or irregular-layout papers.
- `baidu_unlimited_ocr`: optional Baidu-hosted Unlimited-OCR adapter when that
  service is enabled by configuration.

The first code plan may implement only the provider interface and the MinerU
adapter. Baidu adapters can be stubbed behind explicit configuration until API
credentials and data-handling policy are available.

## Scope Decomposition

The complete goal includes several independent subsystems. They are sequenced
so each stage produces artifacts that later stages can measure.

### Phase 1: Evidence And Review Backbone

Build the backend artifacts and pipeline stages:

- `evidence_ledger.json`
- `deterministic_checks.json`
- `peerassist_concerns.json`
- `human_confirmations.json`
- `tool_trace.jsonl`
- `peerassist_report.md`
- `peerassist_report.json`

Phase 1 must be runnable from the existing CLI and must not require a new web
frontend.

### Phase 2: Multi-Agent And Capability Layer

Add structured agent execution, capability registry, MCP/Skill metadata,
permission filtering, tool-call events, and artifact validation.

Phase 2 may still run in CLI mode, but all events must be suitable for a future
timeline UI.

### Phase 3: Reviewer Confirmation Interface

Add a review UI or lightweight local interface where each concern can be
confirmed, downgraded, rewritten, deleted, split, merged, or marked pending.

### Phase 4: PeerAssist-Eval-v1

Create the versioned evaluation package, manifests, gold annotations,
metric scripts, and frozen-set protections.

### Phase 5: Human Crossover Experiment

Run the reviewer study described in the methodology document. Code completion
does not prove the final objective until this evidence exists.

## Core Artifacts

### Evidence Ledger

`evidence_ledger.json` is the root evidence source for PeerAssist. Every major
concern must point to one or more evidence IDs from this ledger.

Shape:

```json
{
  "schema_version": "peerassist.evidence_ledger.v1",
  "paper_id": "paper-key",
  "source_sha256": "hex",
  "items": [
    {
      "id": "P03-L012",
      "type": "text_span",
      "page": 3,
      "section": "Method",
      "locator": "page 3, line 12",
      "text": "original sentence",
      "bbox": null,
      "source_path": "mineru_full.md",
      "metadata": {}
    }
  ]
}
```

Required item types:

- `text_span`
- `section`
- `figure`
- `figure_caption`
- `table`
- `table_cell`
- `formula`
- `citation`
- `reference`
- `supplement`
- `code_artifact`

The first implementation may produce a subset when the parser lacks enough
structure, but missing item types must be explicit in `coverage` metadata.

### Deterministic Checks

`deterministic_checks.json` contains reproducible leads, not misconduct
judgments. Each check must first record applicability.

Shape:

```json
{
  "schema_version": "peerassist.deterministic_checks.v1",
  "checks": [
    {
      "id": "check_table_percentage_001",
      "kind": "percentage_consistency",
      "applicability": "applicable",
      "status": "lead",
      "evidence_ids": ["T2-R4-C3", "T2-R4-C4"],
      "message": "Reported percentage does not match count/sample size.",
      "benign_explanations": ["rounding", "different denominator"],
      "requires_human_review": true
    }
  ]
}
```

Applicability values:

- `applicable`
- `not_applicable`
- `insufficient_evidence`
- `parser_uncertain`

Status values:

- `pass`
- `lead`
- `inconclusive`
- `failed_to_run`

Initial checks:

- percentage/count consistency
- mean/range impossibility
- p-value/statistic consistency when all inputs are present
- significance-marker consistency
- repeated table rows or columns
- fixed-difference and fixed-ratio table patterns
- figure/table numbering consistency
- figure/table caption and in-text reference consistency
- missing seed, split, parameter, or environment statements
- reference metadata and retraction warnings via existing RefCopilot path

Benford and terminal-digit checks are weak leads. They must emit
`not_applicable` for small samples, narrow ranges, identifiers, years, pages,
fixed scales, and other unsuitable data.

### PeerAssist Concerns

`peerassist_concerns.json` is the candidate review concern pool. It is the only
input to human confirmation.

Shape:

```json
{
  "schema_version": "peerassist.concerns.v1",
  "concerns": [
    {
      "id": "concern_001",
      "level": "major_concern",
      "category": "statistics",
      "title": "样本比例与报告百分比需要澄清",
      "evidence_ids": ["T2-R4-C3"],
      "impact": "This may affect whether the reported comparison supports the claim.",
      "benign_explanation": "The table may use a filtered denominator not described in the paper.",
      "author_action": "Please clarify the denominator and provide the calculation rule.",
      "status": "pending_human_confirmation",
      "source_agent_ids": ["statistics_agent"],
      "source_check_ids": ["check_table_percentage_001"]
    }
  ]
}
```

Levels:

- `major_concern`
- `minor_concern`
- `clarification_needed`
- `editor_note`

Categories:

- `structure`
- `method`
- `statistics`
- `figure_table`
- `citation`
- `reproducibility`
- `ethics`
- `safety`
- `other`

Concerns without evidence IDs are allowed only with
`status = "pending_human_confirmation"` and must be exported as manual-check
appendix items, not as confirmed findings.

### Human Confirmations

`human_confirmations.json` stores every human decision and text revision.

Shape:

```json
{
  "schema_version": "peerassist.human_confirmations.v1",
  "actions": [
    {
      "concern_id": "concern_001",
      "action": "rewrite",
      "previous_text": "...",
      "new_text": "...",
      "reviewer_id": "local-reviewer",
      "timestamp": "2026-07-10T00:00:00Z",
      "reason": "Make the wording more cautious."
    }
  ]
}
```

Actions:

- `confirm`
- `downgrade`
- `rewrite`
- `delete`
- `mark_pending`
- `split`
- `merge`

The default report exporter includes only `confirm`, `downgrade`, and
`rewrite` outcomes in main review sections. Pending items appear in a manual
verification appendix. Deleted items remain in the confirmation log but are not
exported.

### Tool Trace

`tool_trace.jsonl` records built-in checks, MCP calls, Skill calls, and artifact
creation.

Event statuses:

- `queued`
- `started`
- `progress`
- `approval_required`
- `completed`
- `failed`
- `cancelled`
- `artifact_created`

Each event includes:

- `task_id`
- `call_id`
- `agent_id`
- `source`
- `tool`
- `status`
- `ts`
- `input_summary`
- `output_summary`
- `artifact_ids`
- `duration_ms`
- `error_code`
- `evidence_ids`

The integrator can consume only completed results whose output validates
against the expected schema. Failed or timed-out calls become explicit
`inconclusive` or `pending_human_confirmation` records.

## Pipeline Stages

PeerAssist adds the following stages under `src/peerassist/` and wires them
into `pipeline_full.py` after parse and before final PeerAssist export.

```text
parse
  -> build_evidence_ledger
  -> run_deterministic_checks
  -> run_peerassist_agents
  -> run_defense_explanations
  -> integrate_concerns
  -> apply_human_confirmations
  -> export_peerassist_report
  -> evaluate_peerassist_run
```

The first CLI integration may run PeerAssist as an optional mode:

```bash
python scripts/execute_review_pipeline.py paper.pdf --peerassist-mode fast
```

Modes:

- `off`: current FactReview behavior.
- `fast`: evidence ledger, lightweight deterministic checks, core agents.
- `standard`: full deterministic checks, all review agents, RefCopilot, defense.
- `deep`: standard mode plus execution, supplement/code/image-intensive checks,
  external databases, and additional approval gates.

## Multi-Agent Review

Agents generate structured concerns, not final conclusions.

Agents:

- `structure_agent`: abstract, contribution, section logic, conclusion scope.
- `method_agent`: method completeness, experiment design, controls, ablations,
  parameters.
- `statistics_agent`: deterministic numeric and statistical leads.
- `figure_table_agent`: captions, labels, units, figure/table references, image
  review leads.
- `citation_agent`: citation truth, support relation, retractions, missing
  related work.
- `reproducibility_agent`: code, data, environment, seeds, splits, parameters.
- `ethics_agent`: approvals, privacy, consent, conflicts, data authorization.
- `defense_agent`: strongest benign explanation for each lead.
- `integrator_agent`: deduplicate, merge, rank, and normalize wording.

Every agent input is a typed packet containing the ledger slice, relevant
checks, prior FactReview artifacts, and allowed capabilities. Agents do not get
unbounded access to the whole run directory by default.

## MCP And Skills Capability Layer

Capabilities are exposed through a registry with progressive disclosure:

1. Load capability metadata: name, description, version, input/output summary,
   source, permission class.
2. Filter by mode, stage, task, and privacy policy.
3. Expose only needed schemas to the agent.
4. Request approval for file writes, external requests, code execution, or
   manuscript-leaking operations.
5. Execute with timeout, cancellation, retries, concurrency limits, and tracing.
6. Validate outputs before artifact integration.

The registry supports three sources:

- `builtin`: local deterministic checks and FactReview tools.
- `mcp`: external MCP servers discovered at runtime.
- `skill`: Codex/Claude-style skills loaded progressively.

## Report Export

PeerAssist exports:

- `peerassist_report.json`: structured report and all confirmed concerns.
- `peerassist_report.md`: editable human-facing review.
- `peerassist_report.zh.md` and `peerassist_report.en.md` when bilingual output
  is enabled.

Template sections:

1. Paper summary.
2. Overall assessment in review-aid language.
3. Major concerns.
4. Minor concerns.
5. Clarifications requested.
6. Confidential editor notes.
7. Pending manual checks.
8. System limitations.
9. Provenance appendix.

The exporter must reject accusatory phrasing such as "fraud", "fabricated",
"proven misconduct", "definitely fake", or automatic accept/reject decisions
unless they appear only in quoted source text and are not the system's claim.

## Evaluation Design

`PeerAssist-Eval-v1` is versioned and isolated from development prompts,
retrieval indexes, fine-tuning, and manual prompt tuning.

Directory:

```text
eval/PeerAssist-Eval-v1/
  manifest.json
  ReviewBench-300/
  LayoutBench-100/
  ConsistencyBench-500/
  ImageBench-400/
  CitationBench-200/
  ReproBench-30/
  SafetyBench-100/
  gold/
  scripts/
  reports/
```

Every sample records:

- source
- license
- SHA-256
- annotation version
- allowed use
- split: development or frozen
- domain
- PDF type
- problem categories

Automated metrics:

- document parsing success rate
- section and page localization accuracy
- figure/caption/body-reference F1
- evidence binding rate
- evidence faithfulness
- deterministic error precision and recall
- gold concern coverage
- unevidenced new-fact rate
- accusatory language violation rate
- safety refusal/boundary accuracy
- median and P95 latency per mode
- estimated inference cost per mode

Reports must be stratified by domain, PDF type, and problem category.

Human crossover metrics:

- paper summary time
- mechanical check time
- evidence location time
- complete draft time
- retained concern count
- gold concern recall
- system-generated concern retention rate
- SUS score

The final objective is complete only when both the automated evaluation and the
human crossover experiment satisfy the target thresholds in the methodology
document.

## Error Handling And Degradation

Failures must be explicit:

- Parser failures create ledger coverage warnings and block deterministic checks
  that need missing structures.
- Missing evidence prevents a concern from entering confirmed report sections.
- Failed deterministic checks become `failed_to_run` records.
- Failed agent calls become `pending_human_confirmation` records.
- Failed MCP/Skill calls remain visible in `tool_trace.jsonl`.
- External database outages downgrade citation checks to incomplete, not
  supported.
- Timeouts are mode-specific and recorded in run stats.

No model is allowed to fill missing evidence by inference.

## Testing Strategy

Implementation planning must use test-first development for behavior changes.

Unit tests:

- schema validation for all PeerAssist artifacts
- evidence ID stability
- deterministic check applicability gates
- report export filtering of pending/deleted concerns
- accusatory-language guard
- tool trace event validation
- capability permission filtering

Integration tests:

- minimal parsed-paper fixture to ledger to checks to report
- missing parser structure degrades cleanly
- failed tool call cannot enter integrated concerns
- human confirmation actions change exported report correctly

Evaluation harness tests:

- metric formulas on tiny fixtures
- frozen split isolation checks
- stratified report generation

## Non-Goals For The First Implementation Plan

These remain part of the full objective but are not required for the first code
plan:

- Full web UI.
- Real MCP server management UI.
- Full `PeerAssist-Eval-v1` dataset collection.
- Completed reviewer crossover experiment.
- Pixel-level image forensic model.
- Automatic accept/reject recommendation.

## Open Implementation Decisions

These are implementation choices; the requirements above are already fixed:

- Whether PeerAssist artifacts live under `stages/peerassist/` or under
  substage-specific paths such as `stages/review/peerassist/`.
- Whether the first human confirmation interface is CLI-only, Markdown-driven,
  or a local web page.
- Whether `fast` mode uses the existing FactReview agent or a new small
  PeerAssist concern generator.
- Which deterministic checks are implemented in the first red-green-refactor
  sequence.

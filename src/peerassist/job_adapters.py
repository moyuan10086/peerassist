"""Real local-first stage adapters for the recoverable review runner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from common.pipeline_context import peerassist_stage_dir, write_json_file
from peerassist.agents import integrate_agent_results, run_peerassist_agents
from peerassist.citation_pipeline import run_citation_pipeline
from peerassist.confirmations import build_confirmation_bundle, build_confirmation_review_queue
from peerassist.deterministic_checks import run_deterministic_checks
from peerassist.evidence_ledger import build_evidence_ledger
from peerassist.job_repository import PaperRepository, ReviewJobRepository
from peerassist.local_pdf_parser import LocalParseStatus, parse_pdf_locally
from peerassist.paper_profile import build_paper_understanding
from peerassist.tool_trace import ToolTraceRecorder
from schemas.citation import CitationAudit
from schemas.peerassist import AgentReviewResult, Concern, DeterministicCheck, EvidenceLedger
from schemas.peerassist_jobs import ReviewJobState, ReviewStage


def build_local_stage_adapters(repository: ReviewJobRepository) -> dict[ReviewStage, Any]:
    paper_repository = PaperRepository(repository.data_dir)

    def validate(state: ReviewJobState) -> dict[str, Any]:
        record = paper_repository.get(state.paper_id)
        source_pdf = repository.data_dir / "papers" / state.paper_id / record.source_pdf_path
        if not source_pdf.is_file() or source_pdf.read_bytes()[:5] != b"%PDF-":
            raise ValueError("uploaded paper is not a valid PDF")
        return {
            "schema_version": "peerassist.validation.v1",
            "source_pdf": str(source_pdf.resolve()),
            "size_bytes": source_pdf.stat().st_size,
        }

    def parse(state: ReviewJobState) -> dict[str, Any]:
        source_pdf = Path(_result(repository, state, ReviewStage.VALIDATE)["source_pdf"])
        output_dir = repository.data_dir / state.run_dir / "local_parse" / state.attempt_id
        result = parse_pdf_locally(source_pdf, output_dir)
        if result.status is not LocalParseStatus.OK:
            raise ValueError(result.error_code or result.status.value)
        return result.model_dump(mode="json")

    def evidence(state: ReviewJobState) -> dict[str, Any]:
        parsed = _result(repository, state, ReviewStage.PARSE)
        source_pdf = Path(str(parsed["source_pdf"]))
        ledger = build_evidence_ledger(
            paper_id=state.paper_id,
            source_pdf=source_pdf,
            mineru_markdown_path=_optional_path(parsed.get("markdown_path")),
            mineru_content_list_path=_optional_path(parsed.get("content_list_path")),
            provider_name=str(parsed.get("provider_name") or "local_pymupdf"),
            provider_warnings=[str(row) for row in parsed.get("warnings") or []],
        )
        return ledger.model_dump(mode="json")

    def profile(state: ReviewJobState) -> dict[str, Any]:
        ledger = EvidenceLedger.model_validate(_result(repository, state, ReviewStage.EVIDENCE))
        output_dir = repository.data_dir / state.run_dir / "paper_understanding" / state.attempt_id
        return build_paper_understanding(ledger, output_dir).model_dump(mode="json")

    def plan(state: ReviewJobState) -> dict[str, Any]:
        return dict(_result(repository, state, ReviewStage.PROFILE).get("review_plan") or {})

    def deterministic(state: ReviewJobState) -> dict[str, Any]:
        ledger = EvidenceLedger.model_validate(_result(repository, state, ReviewStage.EVIDENCE))
        checks = run_deterministic_checks(ledger)
        return {
            "schema_version": "peerassist.deterministic_checks.v1",
            "checks": [check.model_dump(mode="json") for check in checks],
        }

    def citation(state: ReviewJobState) -> dict[str, Any]:
        ledger = EvidenceLedger.model_validate(_result(repository, state, ReviewStage.EVIDENCE))
        run_dir = repository.data_dir / state.run_dir
        trace = ToolTraceRecorder(peerassist_stage_dir(run_dir) / "tool_trace.jsonl")
        result = run_citation_pipeline(
            repo_root=repository.data_dir.parent,
            run_dir=run_dir,
            paper_key=state.paper_id,
            ledger=ledger,
            trace=trace,
            mode=state.mode,
        )
        return {
            "schema_version": "peerassist.citation_stage.v1",
            "audit": result.audit.model_dump(mode="json"),
            "augmented_ledger": result.augmented_ledger.model_dump(mode="json"),
            "concerns": [concern.model_dump(mode="json") for concern in result.concerns],
            "warnings": result.warnings,
        }

    def agents(state: ReviewJobState) -> dict[str, Any]:
        ledger = EvidenceLedger.model_validate(_result(repository, state, ReviewStage.EVIDENCE))
        checks = [
            DeterministicCheck.model_validate(row)
            for row in _result(repository, state, ReviewStage.DETERMINISTIC).get("checks", [])
        ]
        results = run_peerassist_agents(mode=state.mode, ledger=ledger, checks=checks)
        return {
            "schema_version": "peerassist.agent_results.v1",
            "results": [result.model_dump(mode="json") for result in results],
        }

    def integrate(state: ReviewJobState) -> dict[str, Any]:
        agent_results = [
            AgentReviewResult.model_validate(row)
            for row in _result(repository, state, ReviewStage.AGENTS).get("results", [])
        ]
        citation_payload = _result(repository, state, ReviewStage.CITATION)
        audit = CitationAudit.model_validate(citation_payload["audit"])
        concerns = integrate_agent_results(agent_results, citation_audit=audit, mode=state.mode)
        _write_confirmation_outputs(repository, state, concerns)
        return {
            "schema_version": "peerassist.candidate_review.v1",
            "concerns": [concern.model_dump(mode="json") for concern in concerns],
            "confirmation_revision": 0,
        }

    return {
        ReviewStage.VALIDATE: validate,
        ReviewStage.PARSE: parse,
        ReviewStage.EVIDENCE: evidence,
        ReviewStage.PROFILE: profile,
        ReviewStage.PLAN: plan,
        ReviewStage.DETERMINISTIC: deterministic,
        ReviewStage.CITATION: citation,
        ReviewStage.AGENTS: agents,
        ReviewStage.INTEGRATE: integrate,
    }


def _result(
    repository: ReviewJobRepository,
    state: ReviewJobState,
    stage: ReviewStage,
) -> dict[str, Any]:
    manifest = repository.current_stage_manifest(state.id, stage)
    if manifest is None:
        raise RuntimeError(f"missing committed dependency: {stage.value}")
    path = (
        repository.jobs_dir
        / str(state.id)
        / manifest.output_dir
        / manifest.artifacts["result"]
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"invalid stage result: {stage.value}")
    return payload


def _optional_path(value: Any) -> Path | None:
    token = str(value or "").strip()
    return Path(token) if token else None


def _write_confirmation_outputs(
    repository: ReviewJobRepository,
    state: ReviewJobState,
    concerns: list[Concern],
) -> None:
    ledger = EvidenceLedger.model_validate(_result(repository, state, ReviewStage.EVIDENCE))
    evidence_lookup = {item.id: item.locator for item in ledger.items}
    bundle = build_confirmation_bundle(concerns=concerns, evidence_lookup=evidence_lookup)
    queue = build_confirmation_review_queue(bundle)
    out_dir = peerassist_stage_dir(repository.data_dir / state.run_dir)
    write_json_file(
        out_dir / "peerassist_concerns.json",
        {
            "schema_version": "peerassist.concerns.v1",
            "mode": state.mode,
            "paper_id": state.paper_id,
            "concerns": [concern.model_dump(mode="json") for concern in concerns],
        },
    )
    write_json_file(out_dir / "confirmation_bundle.json", bundle)
    write_json_file(out_dir / "confirmation_review_queue.json", queue)
    if not (out_dir / "human_confirmations.json").exists():
        write_json_file(
            out_dir / "human_confirmations.json",
            {
                "schema_version": "peerassist.human_confirmations.v2",
                "revision": 0,
                "mutation_revision": 0,
                "actions": [],
            },
        )

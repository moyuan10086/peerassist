"""Real local-first stage adapters for the recoverable review runner."""

from __future__ import annotations

import json
from pathlib import Path
from time import monotonic
from typing import Any

from common.pipeline_context import peerassist_stage_dir, write_json_file
from peerassist.agents import integrate_agent_results, run_peerassist_agents
from peerassist.citation_pipeline import run_citation_pipeline
from peerassist.confirmations import build_confirmation_bundle, build_confirmation_review_queue
from peerassist.deterministic_checks import run_deterministic_checks
from peerassist.evidence_ledger import build_evidence_ledger
from peerassist.job_repository import PaperRepository, ReviewJobRepository
from peerassist.local_pdf_parser import LocalParseStatus, parse_pdf_locally
from peerassist.model_review import resolve_model_review_config, run_batched_model_review
from peerassist.paper_profile import build_paper_understanding
from peerassist.tool_trace import ToolTraceRecorder
from schemas.citation import CitationAudit
from schemas.peerassist import (
    AgentReviewResult,
    Concern,
    DeterministicCheck,
    EvidenceLedger,
    PaperUnderstandingArtifacts,
    ToolTraceStatus,
)
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
        understanding = PaperUnderstandingArtifacts.model_validate(
            _result(repository, state, ReviewStage.PROFILE)
        )
        checks = [
            DeterministicCheck.model_validate(row)
            for row in _result(repository, state, ReviewStage.DETERMINISTIC).get("checks", [])
        ]
        citation_audit = CitationAudit.model_validate(
            _result(repository, state, ReviewStage.CITATION)["audit"]
        )
        config = resolve_model_review_config()
        trace = ToolTraceRecorder(
            peerassist_stage_dir(repository.data_dir / state.run_dir) / "tool_trace.jsonl"
        )
        model_metadata: dict[str, Any] = {
            "configured": config is not None,
            "status": "unavailable" if config is None else "pending",
            "prompt_version": "peerassist.professional_agents.v1",
            "max_output_tokens": config.max_tokens if config else 0,
        }

        def enhance(context: dict[str, Any]) -> dict[str, Any]:
            if config is None:
                return {"concerns": [], "usage": {}}
            call_id = f"professional_agent_model_{state.attempt_id}"
            evidence_ids = [str(value) for value in context.get("selected_evidence_ids", [])]
            started = monotonic()
            trace.record(
                task_id=str(state.id),
                call_id=call_id,
                agent_id="professional_agent_batch",
                source="openai_compatible",
                tool="chat_completions",
                status=ToolTraceStatus.STARTED,
                input_summary=(
                    f"model={config.model}; blocks={context.get('budget', {}).get('selected_blocks', 0)}; "
                    f"max_output_tokens={config.max_tokens}"
                ),
                evidence_ids=evidence_ids,
            )
            try:
                payload = run_batched_model_review(context, config)
            except Exception as exc:
                trace.record(
                    task_id=str(state.id),
                    call_id=call_id,
                    agent_id="professional_agent_batch",
                    source="openai_compatible",
                    tool="chat_completions",
                    status=ToolTraceStatus.FAILED,
                    output_summary=type(exc).__name__,
                    duration_ms=max(0, int((monotonic() - started) * 1000)),
                    error_code="model_review_failed",
                    evidence_ids=evidence_ids,
                )
                model_metadata["status"] = "failed"
                model_metadata["error_code"] = "model_review_failed"
                raise
            usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
            trace.record(
                task_id=str(state.id),
                call_id=call_id,
                agent_id="professional_agent_batch",
                source="openai_compatible",
                tool="chat_completions",
                status=ToolTraceStatus.COMPLETED,
                output_summary=(
                    f"concerns={len(payload.get('concerns') or [])}; "
                    f"tokens={int(usage.get('total_tokens') or 0)}"
                ),
                duration_ms=max(0, int((monotonic() - started) * 1000)),
                evidence_ids=evidence_ids,
            )
            model_metadata.update(
                {
                    "status": "completed",
                    "model": config.model,
                    "usage": usage,
                }
            )
            return payload

        results = run_peerassist_agents(
            mode=state.mode,
            ledger=ledger,
            checks=checks,
            understanding=understanding,
            model_enhancer=enhance if config is not None else None,
            citation_audit=citation_audit,
        )
        if config is None:
            for result in results:
                if result.agent_id.endswith("_agent") and "responsibility" in result.metadata:
                    result.metadata["model_status"] = "unavailable"
                    result.metadata["model_configured"] = False
        return {
            "schema_version": "peerassist.agent_results.v1",
            "results": [result.model_dump(mode="json") for result in results],
            "model_enhancement": model_metadata,
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
    write_json_file(out_dir / "evidence_ledger.json", ledger.model_dump(mode="json"))
    write_json_file(out_dir / "agent_results.json", _result(repository, state, ReviewStage.AGENTS))
    if not (out_dir / "capability_invocations.json").exists():
        write_json_file(out_dir / "capability_invocations.json", {"results": []})
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

"""Coordinate citation extraction, verification, and auditable publication."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from common.pipeline_context import peerassist_stage_dir, refcheck_stage_dir
from peerassist.citation_audit import build_citation_audit, write_citation_audit_atomic
from peerassist.citation_concerns import concerns_from_citation_audit
from peerassist.citation_evidence import extract_citation_evidence
from peerassist.citation_verification import ExistingRefcheckAdapter, verify_reference_with_retries
from peerassist.evidence_ledger import with_citation_evidence
from peerassist.tool_trace import ToolTraceRecorder
from schemas.citation import CitationAudit
from schemas.peerassist import Concern, EvidenceLedger, ToolTraceStatus


@dataclass(frozen=True)
class CitationPipelineResult:
    augmented_ledger: EvidenceLedger
    audit: CitationAudit
    audit_path: Path
    verification_artifact_paths: list[Path]
    concerns: list[Concern]
    warnings: list[str]


def run_citation_pipeline(
    *,
    repo_root: Path,
    run_dir: Path,
    paper_key: str,
    ledger: EvidenceLedger,
    trace: ToolTraceRecorder,
    mode: str,
) -> CitationPipelineResult:
    """Run the local citation pipeline without allowing adapter errors to fail the stage."""
    del repo_root
    out_dir = peerassist_stage_dir(run_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    trace.record(
        task_id=paper_key,
        call_id="citation_extraction",
        agent_id="citation_audit",
        source="builtin",
        tool="extract_citation_evidence",
        status=ToolTraceStatus.STARTED,
        input_summary=f"mode={mode}",
    )
    extracted = extract_citation_evidence(ledger)
    augmented_ledger = with_citation_evidence(ledger)
    trace.record(
        task_id=paper_key,
        call_id="citation_extraction",
        agent_id="citation_audit",
        source="builtin",
        tool="extract_citation_evidence",
        status=ToolTraceStatus.COMPLETED,
        output_summary=f"mentions={len(extracted.mentions)}; references={len(extracted.references)}",
        evidence_ids=[item.id for item in extracted.mentions],
        artifact_ids=["evidence_ledger"],
    )
    trace.record(
        task_id=paper_key,
        call_id="citation_linking",
        agent_id="citation_audit",
        source="builtin",
        tool="build_citation_links",
        status=ToolTraceStatus.STARTED,
        input_summary=f"mentions={len(extracted.mentions)}; references={len(extracted.references)}",
    )
    trace.record(
        task_id=paper_key,
        call_id="citation_linking",
        agent_id="citation_audit",
        source="builtin",
        tool="build_citation_links",
        status=ToolTraceStatus.COMPLETED,
        output_summary=f"links={len(extracted.links)}",
        evidence_ids=[link.mention_evidence_id for link in extracted.links],
    )

    refcheck_path = refcheck_stage_dir(run_dir) / "reference_check.json"
    adapter = ExistingRefcheckAdapter(refcheck_path) if refcheck_path.exists() else None
    trace.record(
        task_id=paper_key,
        call_id="citation_verification",
        agent_id="citation_audit",
        source="builtin",
        tool="verify_reference_with_retries",
        status=ToolTraceStatus.STARTED,
        input_summary=f"records={len(extracted.references)}; refcheck_present={refcheck_path.exists()}",
    )
    verifications = [
        verify_reference_with_retries(record, adapter, artifact_dir=out_dir, max_attempts=1)
        for record in extracted.references
    ]
    artifact_paths = [
        out_dir / verification.raw_response_artifact.path
        for verification in verifications
        if verification.raw_response_artifact is not None
    ]
    failed = next((verification for verification in verifications if verification.status.value == "failed"), None)
    trace.record(
        task_id=paper_key,
        call_id="citation_verification",
        agent_id="citation_audit",
        source="builtin",
        tool="verify_reference_with_retries",
        status=ToolTraceStatus.FAILED if failed is not None else ToolTraceStatus.COMPLETED,
        output_summary=f"verifications={len(verifications)}",
        artifact_ids=[verification.id for verification in verifications],
        evidence_ids=[evidence_id for record in extracted.references for evidence_id in record.source_evidence_ids],
        error_code=failed.error_code if failed is not None else "",
    )
    for artifact_path in artifact_paths:
        trace.record(
            task_id=paper_key,
            call_id="citation_verification_artifact",
            agent_id="citation_audit",
            source="builtin",
            tool="write_response_artifact",
            status=ToolTraceStatus.ARTIFACT_CREATED,
            artifact_ids=[str(artifact_path.relative_to(out_dir))],
        )

    warnings = list(extracted.warnings)
    if adapter is None and extracted.references:
        warnings.append("refcheck_unavailable")
    trace.record(
        task_id=paper_key,
        call_id="citation_audit",
        agent_id="citation_audit",
        source="builtin",
        tool="write_citation_audit_atomic",
        status=ToolTraceStatus.STARTED,
        input_summary=f"records={len(extracted.references)}; links={len(extracted.links)}",
    )
    audit = build_citation_audit(
        paper_id=paper_key,
        parse_version=str(ledger.metadata.get("provider") or "unknown"),
        ledger=augmented_ledger,
        records=extracted.references,
        links=extracted.links,
        selected_verifications=verifications,
        warnings=warnings,
    )
    audit_path = write_citation_audit_atomic(out_dir / "citation_audit.json", audit, augmented_ledger, out_dir)
    trace.record(
        task_id=paper_key,
        call_id="citation_audit",
        agent_id="citation_audit",
        source="builtin",
        tool="write_citation_audit_atomic",
        status=ToolTraceStatus.COMPLETED,
        output_summary=f"findings={len(audit.findings)}",
        artifact_ids=["citation_audit"],
    )
    source_agent_id = "citation_agent" if mode in {"standard", "deep"} else "citation_audit"
    return CitationPipelineResult(
        augmented_ledger=augmented_ledger,
        audit=audit,
        audit_path=audit_path,
        verification_artifact_paths=artifact_paths,
        concerns=concerns_from_citation_audit(audit, source_agent_id=source_agent_id),
        warnings=warnings,
    )

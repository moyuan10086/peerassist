from __future__ import annotations

import pytest

from peerassist.citation_concerns import concerns_from_citation_audit
from schemas.citation import CitationAudit
from schemas.peerassist import ConcernLevel, ConcernStatus


def _audit(status: str) -> CitationAudit:
    finding = {
        "id": f"F-{status}-001",
        "status": status,
        "severity": "clarification_needed",
        "citation_link_ids": ["link-1"],
        "reference_record_ids": ["record-1"],
        "mention_evidence_ids": ["mention-1"],
        "reference_evidence_ids": ["reference-1"],
        "verification_ids": ["verification-1"],
        "message": "Citation audit result.",
        "requires_human_review": status != "verified",
    }
    if status == "missing_reference":
        finding.update(reference_record_ids=[], reference_evidence_ids=[], verification_ids=[])
    if status in {"uncited_reference", "malformed_reference", "duplicate_reference_metadata"}:
        finding.update(citation_link_ids=[], mention_evidence_ids=[], verification_ids=[])
    verification_status = {
        "not_found": "not_found",
        "ambiguous": "ambiguous",
        "verification_failed": "failed",
        "insufficient_evidence": "unavailable",
    }.get(status, "completed")
    verifications = []
    if finding["verification_ids"]:
        verification = {
            "id": "verification-1",
            "reference_record_id": "record-1",
            "source": "offline",
            "status": verification_status,
            "adapter": {"name": "offline", "version": "1"},
            "query": {},
            "attempt_id": "attempt-1",
            "attempt_number": 1,
            "checked_at": "2026-07-11T00:00:00Z",
            "tool_call_id": "verify-1",
            "match": {
                "method": "doi_exact",
                "candidate_count": 1,
                "selected_candidate_id": "external-1",
                "selection_reason": "exact match",
                "candidate_ids": ["external-1"],
            },
            "source_record": {"id": "external-1", "url": "https://example.invalid/1"},
            "raw_response_artifact": {"path": "citation_verifications/attempt-attempt-1.json", "sha256": "a" * 64},
            "error_code": "",
            "observed_metadata": {},
        }
        if verification_status == "not_found":
            verification.update(
                match={"method": "doi_exact", "candidate_count": 0, "selected_candidate_id": None, "selection_reason": "", "candidate_ids": []},
                source_record=None,
            )
        elif verification_status == "ambiguous":
            verification.update(
                match={"method": "multiple_candidates", "candidate_count": 2, "selected_candidate_id": None, "selection_reason": "", "candidate_ids": ["external-1", "external-2"]},
                source_record=None,
            )
        elif verification_status == "unavailable":
            verification.update(match=None, source_record=None, raw_response_artifact=None, error_code="adapter_unavailable")
        elif verification_status == "failed":
            verification.update(match=None, source_record=None, error_code="adapter_schema_error")
        verifications = [verification]
    return CitationAudit.model_validate(
        {
            "schema_version": "peerassist.citation_audit.v1",
            "paper_id": "paper-1",
            "parse_version": "parser-1",
            "records": [{"id": "record-1", "reference_number": 1, "source_evidence_ids": ["reference-1"], "raw_text": "[1] Reference."}],
            "links": [],
            "verifications": verifications,
            "findings": [finding],
        }
    )


@pytest.mark.parametrize(
    "status",
    ["metadata_mismatch", "missing_reference", "not_found", "ambiguous", "insufficient_evidence", "verification_failed"],
)
def test_audit_findings_become_neutral_pending_concerns(status: str) -> None:
    concern = concerns_from_citation_audit(_audit(status), source_agent_id="citation_agent")[0]

    assert concern.id == f"concern_citation_F-{status}-001"
    assert concern.status is ConcernStatus.PENDING_HUMAN_CONFIRMATION
    assert concern.category == "citation"
    assert concern.evidence_ids == (["mention-1", "reference-1"] if status != "missing_reference" else ["mention-1"])
    assert concern.metadata["citation_finding_ids"] == [f"F-{status}-001"]
    assert concern.metadata["audit_schema_version"] == "peerassist.citation_audit.v1"
    assert concern.metadata["audit_parse_version"] == "parser-1"
    text = " ".join([concern.title, concern.impact, concern.benign_explanation, concern.author_action])
    assert not any(word in text for word in ("虚假引用", "造假", "实锤", "自动接受", "自动拒绝"))


def test_verified_finding_creates_no_concern() -> None:
    assert concerns_from_citation_audit(_audit("verified"), source_agent_id="citation_agent") == []


@pytest.mark.parametrize(
    ("status", "level"),
    [("uncited_reference", ConcernLevel.MINOR_CONCERN), ("malformed_reference", ConcernLevel.EDITOR_NOTE), ("duplicate_reference_metadata", ConcernLevel.EDITOR_NOTE)],
)
def test_reference_only_findings_use_conservative_levels(status: str, level: ConcernLevel) -> None:
    concern = concerns_from_citation_audit(_audit(status), source_agent_id="citation_audit")[0]

    assert concern.level is level
    assert concern.evidence_ids == ["reference-1"]
    assert concern.metadata["citation_link_ids"] == []


def test_converter_preserves_verification_provenance_without_inventing_metadata() -> None:
    concern = concerns_from_citation_audit(_audit("verification_failed"), source_agent_id="citation_agent")[0]

    assert concern.metadata["verification_ids"] == ["verification-1"]
    assert concern.metadata["raw_response_artifacts"] == [{"path": "citation_verifications/attempt-attempt-1.json", "sha256": "a" * 64}]
    assert concern.metadata["error_code"] == "adapter_schema_error"
    assert all(key not in concern.metadata for key in ("doi", "title", "year"))

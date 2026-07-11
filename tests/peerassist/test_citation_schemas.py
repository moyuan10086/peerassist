from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from schemas.citation import (
    CitationAudit,
    CitationAuditFinding,
    CitationEvidenceResult,
    CitationFieldComparison,
    CitationFieldDifference,
    CitationFindingSeverity,
    CitationFindingStatus,
    CitationLink,
    CitationLinkStatus,
    CitationVerification,
    ReferenceRecord,
    UnsupportedCitationMarker,
    VerificationStatus,
)
from schemas.peerassist import EvidenceType

SHA256 = "a" * 64


def reference_payload() -> dict[str, Any]:
    return {
        "id": "R-1-deadbeef",
        "reference_number": 1,
        "source_evidence_ids": ["P08-L014"],
        "raw_text": "[1] A Study. 2024.",
        "title": "A Study",
        "year": 2024,
        "doi": "10.1000/example",
        "parse_confidence": 0.9,
    }


def link_payload(status: str = "linked", *, reference_count: int = 1) -> dict[str, Any]:
    record_ids = [f"R-1-record-{index}" for index in range(reference_count)]
    evidence_ids = [f"P08-L{index:03d}" for index in range(reference_count)]
    return {
        "id": "citation-link-C-P02-L004-12-15-1",
        "mention_evidence_id": "C-P02-L004-12-15-1",
        "reference_number": 1,
        "status": status,
        "reference_record_ids": record_ids,
        "reference_evidence_ids": evidence_ids,
    }


def match_payload(candidate_count: int = 1) -> dict[str, Any]:
    candidate_ids = [f"external-{index}" for index in range(candidate_count)]
    return {
        "method": "doi_exact",
        "candidate_count": candidate_count,
        "selected_candidate_id": candidate_ids[0] if candidate_count == 1 else None,
        "selection_reason": "normalized DOI exact match" if candidate_count == 1 else "",
        "candidate_ids": candidate_ids,
    }


def verification_payload(status: str = "completed") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": "V-R-1-deadbeef-offline-1-feedface",
        "reference_record_id": "R-1-deadbeef",
        "source": "offline",
        "status": status,
        "adapter": {"name": "offline", "version": "1"},
        "query": {"doi": "10.1000/example"},
        "attempt_id": "attempt-1",
        "attempt_number": 1,
        "checked_at": "2026-07-11T00:00:00Z",
        "tool_call_id": "verify-reference-1",
        "match": match_payload(),
        "source_record": {
            "id": "external-0",
            "url": "https://example.invalid/record",
        },
        "raw_response_artifact": {
            "path": "citation_verifications/attempt-attempt-1.json",
            "sha256": SHA256,
        },
        "error_code": "",
        "observed_metadata": {"title": "A Study", "year": 2024},
        "field_differences": [
            {
                "field": "title",
                "manuscript_value": "A Study",
                "external_value": "A Study",
                "normalized_manuscript_value": "a study",
                "normalized_external_value": "a study",
                "comparison": "match",
                "rule": "normalized_title_exact",
            }
        ],
    }
    if status == "not_found":
        payload["match"] = match_payload(0)
        payload["source_record"] = None
        payload["observed_metadata"] = {}
    elif status == "ambiguous":
        payload["match"] = match_payload(2)
        payload["source_record"] = None
        payload["observed_metadata"] = {}
    elif status == "unavailable":
        payload["match"] = None
        payload["source_record"] = None
        payload["raw_response_artifact"] = None
        payload["error_code"] = "adapter_unavailable"
        payload["observed_metadata"] = {}
    elif status == "failed":
        payload["match"] = None
        payload["source_record"] = None
        payload["error_code"] = "adapter_schema_error"
        payload["observed_metadata"] = {}
    return payload


def finding_payload(status: str = "metadata_mismatch") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": f"F-{status}-deadbeef",
        "status": status,
        "severity": "clarification_needed",
        "citation_link_ids": ["citation-link-C-P02-L004-12-15-1"],
        "reference_record_ids": ["R-1-deadbeef"],
        "mention_evidence_ids": ["C-P02-L004-12-15-1"],
        "reference_evidence_ids": ["P08-L014"],
        "verification_ids": ["V-R-1-deadbeef-offline-1-feedface"],
        "message": "Citation audit result.",
        "requires_human_review": status != "verified",
        "metadata": {"rule": "test"},
    }
    if status == "missing_reference":
        payload["reference_record_ids"] = []
        payload["reference_evidence_ids"] = []
        payload["verification_ids"] = []
    elif status in {
        "uncited_reference",
        "malformed_reference",
        "duplicate_reference_metadata",
    }:
        payload["citation_link_ids"] = []
        payload["mention_evidence_ids"] = []
        payload["verification_ids"] = []
    return payload


def audit_payload() -> dict[str, Any]:
    return {
        "schema_version": "peerassist.citation_audit.v1",
        "paper_id": "paper-1",
        "parse_version": "parser-1",
        "records": [reference_payload()],
        "links": [link_payload()],
        "verifications": [verification_payload()],
        "findings": [finding_payload()],
        "coverage": {"mentions": 1, "references": 1},
        "warnings": [],
    }


def assert_validation_error(
    model: type[Any], payload: dict[str, Any], error_type: str, location: tuple[Any, ...]
) -> None:
    with pytest.raises(ValidationError) as exc:
        model.model_validate(payload)
    assert any(error["type"] == error_type and error["loc"] == location for error in exc.value.errors()), (
        exc.value.errors()
    )


def test_citation_audit_accepts_complete_payload_and_exact_schema_version() -> None:
    audit = CitationAudit.model_validate(audit_payload())
    assert audit.schema_version == "peerassist.citation_audit.v1"
    assert audit.verifications[0].status is VerificationStatus.COMPLETED


def test_citation_audit_rejects_unknown_top_level_field() -> None:
    payload = audit_payload()
    payload["unexpected"] = True
    assert_validation_error(CitationAudit, payload, "extra_forbidden", ("unexpected",))


def test_citation_audit_rejects_unknown_nested_field() -> None:
    payload = audit_payload()
    payload["verifications"][0]["adapter"]["unexpected"] = True
    assert_validation_error(
        CitationAudit,
        payload,
        "extra_forbidden",
        ("verifications", 0, "adapter", "unexpected"),
    )


def test_citation_audit_rejects_other_schema_versions() -> None:
    payload = audit_payload()
    payload["schema_version"] = "peerassist.citation_audit.v2"
    assert_validation_error(CitationAudit, payload, "literal_error", ("schema_version",))


@pytest.mark.parametrize("status", [status.value for status in VerificationStatus])
def test_each_verification_status_accepts_its_complete_contract(status: str) -> None:
    verification = CitationVerification.model_validate(verification_payload(status))
    assert verification.status.value == status


@pytest.mark.parametrize(
    ("status", "field", "value"),
    [
        ("completed", "match", None),
        ("completed", "source_record", None),
        ("completed", "raw_response_artifact", None),
        ("not_found", "raw_response_artifact", None),
        ("not_found", "source_record", {"id": "fake", "url": "https://x.invalid"}),
        ("ambiguous", "raw_response_artifact", None),
        ("ambiguous", "source_record", {"id": "fake", "url": "https://x.invalid"}),
        ("unavailable", "error_code", ""),
        ("unavailable", "raw_response_artifact", {"path": "x", "sha256": SHA256}),
        ("unavailable", "match", match_payload()),
        ("unavailable", "source_record", {"id": "fake", "url": "https://x.invalid"}),
        ("failed", "error_code", ""),
    ],
)
def test_verification_status_rejects_invalid_field_mutation(status: str, field: str, value: Any) -> None:
    payload = verification_payload(status)
    payload[field] = value
    with pytest.raises(ValidationError, match=status):
        CitationVerification.model_validate(payload)


@pytest.mark.parametrize(
    ("status", "candidate_count", "selected_candidate_id"),
    [
        ("completed", 2, "external-0"),
        ("completed", 1, None),
        ("not_found", 1, None),
        ("not_found", 0, "external-0"),
        ("ambiguous", 0, None),
        ("ambiguous", 2, "external-0"),
    ],
)
def test_verification_status_enforces_match_shape(
    status: str, candidate_count: int, selected_candidate_id: str | None
) -> None:
    payload = verification_payload(status)
    payload["match"]["candidate_count"] = candidate_count
    payload["match"]["selected_candidate_id"] = selected_candidate_id
    with pytest.raises(ValidationError, match=status):
        CitationVerification.model_validate(payload)


def test_ambiguous_verification_permits_one_non_authoritative_candidate() -> None:
    payload = verification_payload("ambiguous")
    payload["match"] = match_payload(1)
    payload["match"]["selected_candidate_id"] = None
    payload["match"]["selection_reason"] = "title similarity below unique threshold"
    verification = CitationVerification.model_validate(payload)
    assert verification.match is not None
    assert verification.match.candidate_count == 1


def test_completed_verification_does_not_require_candidate_summary_ids() -> None:
    payload = verification_payload("completed")
    payload["match"]["candidate_ids"] = []
    assert CitationVerification.model_validate(payload).status is VerificationStatus.COMPLETED


def test_failed_verification_permits_absent_raw_response() -> None:
    payload = verification_payload("failed")
    payload["raw_response_artifact"] = None
    assert CitationVerification.model_validate(payload).raw_response_artifact is None


@pytest.mark.parametrize(
    "sha256",
    ["a" * 63, "a" * 65, "A" * 64, "g" * 64],
)
def test_raw_response_artifact_requires_lowercase_sha256(sha256: str) -> None:
    payload = verification_payload()
    payload["raw_response_artifact"]["sha256"] = sha256
    assert_validation_error(
        CitationVerification,
        payload,
        "string_pattern_mismatch",
        ("raw_response_artifact", "sha256"),
    )


@pytest.mark.parametrize("status", [status.value for status in CitationFindingStatus])
def test_each_finding_status_accepts_its_complete_trace(status: str) -> None:
    finding = CitationAuditFinding.model_validate(finding_payload(status))
    assert finding.status.value == status


@pytest.mark.parametrize(
    "status",
    [
        "verified",
        "metadata_mismatch",
        "not_found",
        "ambiguous",
        "insufficient_evidence",
        "verification_failed",
    ],
)
@pytest.mark.parametrize(
    "field",
    [
        "citation_link_ids",
        "reference_record_ids",
        "mention_evidence_ids",
        "reference_evidence_ids",
        "verification_ids",
    ],
)
def test_verification_derived_findings_require_complete_trace(status: str, field: str) -> None:
    payload = finding_payload(status)
    payload[field] = []
    with pytest.raises(ValidationError, match=status):
        CitationAuditFinding.model_validate(payload)


def test_link_ambiguous_finding_allows_no_verification_for_multiple_records() -> None:
    payload = finding_payload("ambiguous")
    payload["reference_record_ids"] = ["R-1-a", "R-1-b"]
    payload["reference_evidence_ids"] = ["P08-L014", "P08-L015"]
    payload["verification_ids"] = []
    assert CitationAuditFinding.model_validate(payload).verification_ids == []


@pytest.mark.parametrize("field", ["citation_link_ids", "mention_evidence_ids"])
def test_missing_reference_requires_link_and_mention_trace(field: str) -> None:
    payload = finding_payload("missing_reference")
    payload[field] = []
    with pytest.raises(ValidationError, match="missing_reference"):
        CitationAuditFinding.model_validate(payload)


@pytest.mark.parametrize("field", ["reference_record_ids", "reference_evidence_ids"])
def test_missing_reference_forbids_reference_trace(field: str) -> None:
    payload = finding_payload("missing_reference")
    payload[field] = ["unexpected-reference"]
    with pytest.raises(ValidationError, match="missing_reference"):
        CitationAuditFinding.model_validate(payload)


@pytest.mark.parametrize(
    "status",
    ["uncited_reference", "malformed_reference", "duplicate_reference_metadata"],
)
@pytest.mark.parametrize("field", ["reference_record_ids", "reference_evidence_ids"])
def test_reference_only_findings_require_reference_trace(status: str, field: str) -> None:
    payload = finding_payload(status)
    payload[field] = []
    with pytest.raises(ValidationError, match=status):
        CitationAuditFinding.model_validate(payload)


@pytest.mark.parametrize(
    "status",
    ["uncited_reference", "malformed_reference", "duplicate_reference_metadata"],
)
@pytest.mark.parametrize("field", ["citation_link_ids", "mention_evidence_ids", "verification_ids"])
def test_reference_only_findings_forbid_non_reference_trace(status: str, field: str) -> None:
    payload = finding_payload(status)
    payload[field] = ["unexpected-trace"]
    with pytest.raises(ValidationError, match=status):
        CitationAuditFinding.model_validate(payload)


@pytest.mark.parametrize(
    ("status", "requires_human_review"),
    [("verified", True), ("metadata_mismatch", False)],
)
def test_finding_human_review_flag_is_status_specific(status: str, requires_human_review: bool) -> None:
    payload = finding_payload(status)
    payload["requires_human_review"] = requires_human_review
    with pytest.raises(ValidationError, match=status):
        CitationAuditFinding.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value", "error_type"),
    [
        ("reference_number", 0, "greater_than"),
        ("reference_number", -1, "greater_than"),
        ("source_evidence_ids", [], "too_short"),
        ("parse_confidence", -0.01, "greater_than_equal"),
        ("parse_confidence", 1.01, "less_than_equal"),
    ],
)
def test_reference_record_bounds(field: str, value: Any, error_type: str) -> None:
    payload = reference_payload()
    payload[field] = value
    assert_validation_error(ReferenceRecord, payload, error_type, (field,))


def test_reference_record_defaults() -> None:
    record = ReferenceRecord(
        id="R-1-deadbeef",
        reference_number=1,
        source_evidence_ids=["P08-L014"],
        raw_text="[1] A Study.",
    )
    assert (record.title, record.doi, record.year, record.parse_confidence) == (
        "",
        "",
        None,
        0,
    )


@pytest.mark.parametrize(
    ("status", "reference_count"),
    [("linked", 0), ("linked", 2), ("ambiguous", 0), ("ambiguous", 1)],
)
def test_citation_link_enforces_record_cardinality(status: str, reference_count: int) -> None:
    payload = link_payload(status, reference_count=reference_count)
    with pytest.raises(ValidationError, match=status):
        CitationLink.model_validate(payload)


def test_missing_reference_link_permits_no_reference_records() -> None:
    link = CitationLink.model_validate(link_payload("missing_reference", reference_count=0))
    assert link.reference_record_ids == []


def test_unsupported_syntax_is_not_a_citation_link() -> None:
    payload = link_payload("unsupported_syntax", reference_count=0)
    with pytest.raises(ValidationError, match="unsupported_syntax"):
        CitationLink.model_validate(payload)


@pytest.mark.parametrize(
    ("start", "end"),
    [(-1, 2), (1, -1), (4, 3)],
)
def test_unsupported_marker_rejects_invalid_offsets(start: int, end: int) -> None:
    payload = {
        "id": "unsupported-P02-L004-1",
        "source_evidence_id": "P02-L004",
        "raw": "[5-3]",
        "start": start,
        "end": end,
        "status": "unsupported_syntax",
        "error_code": "descending_range",
    }
    with pytest.raises(ValidationError):
        UnsupportedCitationMarker.model_validate(payload)


def test_unsupported_marker_status_is_fixed() -> None:
    payload = {
        "id": "unsupported-P02-L004-1",
        "source_evidence_id": "P02-L004",
        "raw": "[5-3]",
        "start": 1,
        "end": 6,
        "status": "linked",
        "error_code": "descending_range",
    }
    assert_validation_error(UnsupportedCitationMarker, payload, "literal_error", ("status",))


def test_citation_evidence_result_accepts_evidence_items() -> None:
    result = CitationEvidenceResult.model_validate(
        {
            "mentions": [
                {
                    "id": "C-P02-L004-12-15-1",
                    "type": "citation",
                    "text": "[1]",
                }
            ],
            "references": [reference_payload()],
            "links": [link_payload()],
        }
    )
    assert result.mentions[0].type is EvidenceType.CITATION
    assert result.unsupported_markers == []


def test_citation_owned_enums_reject_unknown_values() -> None:
    with pytest.raises(ValidationError):
        CitationFieldDifference(
            field="title",
            manuscript_value="A",
            external_value="B",
            normalized_manuscript_value="a",
            normalized_external_value="b",
            comparison="close_enough",
            rule="title_rule",
        )
    assert CitationFieldComparison.MATCH.value == "match"
    assert CitationFindingSeverity.CLARIFICATION_NEEDED.value == "clarification_needed"
    assert CitationLinkStatus.UNSUPPORTED_SYNTAX.value == "unsupported_syntax"


def test_existing_evidence_type_keeps_citation_member() -> None:
    assert EvidenceType.CITATION.value == "citation"

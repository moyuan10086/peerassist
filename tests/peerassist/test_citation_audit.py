from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from peerassist.citation_artifacts import write_response_artifact
from peerassist.citation_audit import (
    CitationAuditIntegrityError,
    build_citation_audit,
    finding_status_for,
    make_finding_id,
    validate_citation_audit,
    write_citation_audit_atomic,
)
from schemas.citation import (
    CitationAdapterInfo,
    CitationFieldComparison,
    CitationFieldDifference,
    CitationFindingStatus,
    CitationLink,
    CitationLinkStatus,
    CitationMatch,
    CitationSourceRecord,
    CitationVerification,
    ReferenceRecord,
    VerificationStatus,
)
from schemas.peerassist import EvidenceItem, EvidenceLedger, EvidenceType


def evidence(item_id: str, item_type: EvidenceType, text: str) -> EvidenceItem:
    return EvidenceItem(id=item_id, type=item_type, text=text, section="References" if item_type is EvidenceType.REFERENCE else "Body")


def ledger() -> EvidenceLedger:
    return EvidenceLedger(
        paper_id="paper-1",
        items=[
            evidence("M-1", EvidenceType.CITATION, "[1]"),
            evidence("R-E-1", EvidenceType.REFERENCE, "[1] Alpha Study. 2024."),
        ],
    )


def record(
    *,
    record_id: str = "R-1",
    reference_number: int = 1,
    source_evidence_ids: list[str] | None = None,
    raw_text: str = "[1] Alpha Study. 2024.",
    doi: str = "10.1000/alpha",
    title: str = "Alpha Study",
    year: int | None = 2024,
) -> ReferenceRecord:
    return ReferenceRecord(
        id=record_id,
        reference_number=reference_number,
        source_evidence_ids=source_evidence_ids or ["R-E-1"],
        raw_text=raw_text,
        doi=doi,
        title=title,
        year=year,
    )


def link(
    *,
    status: CitationLinkStatus = CitationLinkStatus.LINKED,
    record_ids: list[str] | None = None,
    evidence_ids: list[str] | None = None,
) -> CitationLink:
    if status is CitationLinkStatus.MISSING_REFERENCE:
        record_ids, evidence_ids = [], []
    elif status is CitationLinkStatus.AMBIGUOUS:
        record_ids = record_ids or ["R-1", "R-2"]
        evidence_ids = evidence_ids or ["R-E-1", "R-E-2"]
    return CitationLink(
        id="L-1",
        mention_evidence_id="M-1",
        reference_number=1,
        status=status,
        reference_record_ids=["R-1"] if record_ids is None else record_ids,
        reference_evidence_ids=["R-E-1"] if evidence_ids is None else evidence_ids,
    )


def difference(index: int, *, mismatch: bool) -> CitationFieldDifference:
    return CitationFieldDifference(
        field=("doi", "title", "year")[index],
        manuscript_value="left",
        external_value="right" if mismatch else "left",
        normalized_manuscript_value="left",
        normalized_external_value="right" if mismatch else "left",
        comparison=CitationFieldComparison.MISMATCH if mismatch else CitationFieldComparison.MATCH,
        rule=f"rule-{index}",
    )


def verification(
    root: Path,
    *,
    status: VerificationStatus = VerificationStatus.COMPLETED,
    comparable_fields: int = 2,
    has_mismatch: bool = False,
    attempt_id: str = "attempt-1",
) -> CitationVerification:
    artifact = None
    match = None
    source_record = None
    error_code = ""
    differences: list[CitationFieldDifference] = []
    observed: dict[str, object] = {}
    if status in {VerificationStatus.COMPLETED, VerificationStatus.NOT_FOUND, VerificationStatus.AMBIGUOUS}:
        artifact = write_response_artifact(root, attempt_id, {"status": status.value})
    if status is VerificationStatus.COMPLETED:
        match = CitationMatch(
            method="doi_exact",
            candidate_count=1,
            selected_candidate_id="external-1",
            selection_reason="exact",
            candidate_ids=["external-1"],
        )
        source_record = CitationSourceRecord(id="external-1", url="https://example.invalid/1")
        differences = [difference(index, mismatch=has_mismatch and index == 0) for index in range(comparable_fields)]
        observed = {item.field: item.external_value for item in differences}
    elif status is VerificationStatus.NOT_FOUND:
        match = CitationMatch(method="no_candidates", candidate_count=0, selected_candidate_id=None, selection_reason="", candidate_ids=[])
    elif status is VerificationStatus.AMBIGUOUS:
        match = CitationMatch(
            method="multiple_candidates",
            candidate_count=2,
            selected_candidate_id=None,
            selection_reason="not unique",
            candidate_ids=["external-1", "external-2"],
        )
    else:
        error_code = "adapter_unavailable" if status is VerificationStatus.UNAVAILABLE else "adapter_error"
    return CitationVerification(
        id="V-1",
        reference_record_id="R-1",
        source="offline",
        status=status,
        adapter=CitationAdapterInfo(name="offline", version="1"),
        query={"doi": "10.1000/alpha"},
        attempt_id=attempt_id,
        attempt_number=1,
        checked_at="2026-07-11T00:00:00Z",
        tool_call_id="call-1",
        match=match,
        source_record=source_record,
        raw_response_artifact=artifact,
        error_code=error_code,
        observed_metadata=observed,
        field_differences=differences,
    )


@pytest.mark.parametrize(
    ("verification_status", "comparable", "mismatch", "expected"),
    [
        (VerificationStatus.COMPLETED, 2, False, CitationFindingStatus.VERIFIED),
        (VerificationStatus.COMPLETED, 1, False, CitationFindingStatus.INSUFFICIENT_EVIDENCE),
        (VerificationStatus.COMPLETED, 3, True, CitationFindingStatus.METADATA_MISMATCH),
        (VerificationStatus.NOT_FOUND, 0, False, CitationFindingStatus.NOT_FOUND),
        (VerificationStatus.AMBIGUOUS, 0, False, CitationFindingStatus.AMBIGUOUS),
        (VerificationStatus.UNAVAILABLE, 0, False, CitationFindingStatus.INSUFFICIENT_EVIDENCE),
        (VerificationStatus.FAILED, 0, False, CitationFindingStatus.VERIFICATION_FAILED),
    ],
)
def test_finding_decision_precedence(
    tmp_path: Path,
    verification_status: VerificationStatus,
    comparable: int,
    mismatch: bool,
    expected: CitationFindingStatus,
) -> None:
    assert finding_status_for(link(), verification(tmp_path, status=verification_status, comparable_fields=comparable, has_mismatch=mismatch)) is expected


def test_link_status_precedes_verification(tmp_path: Path) -> None:
    completed = verification(tmp_path, comparable_fields=3, has_mismatch=True)
    assert finding_status_for(link(status=CitationLinkStatus.MISSING_REFERENCE), completed) is CitationFindingStatus.MISSING_REFERENCE
    assert finding_status_for(link(status=CitationLinkStatus.AMBIGUOUS), completed) is CitationFindingStatus.AMBIGUOUS


def test_reference_only_findings(tmp_path: Path) -> None:
    second = record(record_id="R-2", reference_number=2, raw_text="[2] Alpha Study. 2024.")
    audit = build_citation_audit(
        paper_id="paper-1",
        parse_version="parse-1",
        ledger=ledger(),
        records=[record(), second, record(record_id="R-3", reference_number=3, raw_text="[3]", doi="", title="", year=None)],
        links=[],
        selected_verifications=[],
    )
    statuses = {finding.status for finding in audit.findings}
    assert CitationFindingStatus.UNCITED_REFERENCE in statuses
    assert CitationFindingStatus.MALFORMED_REFERENCE in statuses
    assert CitationFindingStatus.DUPLICATE_REFERENCE_METADATA in statuses
    assert all(not finding.citation_link_ids and not finding.verification_ids for finding in audit.findings)


def test_finding_id_is_stable_for_canonical_trace() -> None:
    trace = {
        "citation_link_ids": ["L-2", "L-1"],
        "reference_record_ids": ["R-2", "R-1"],
        "verification_ids": ["V-2", "V-1"],
        "difference_fields": ["year", "doi"],
        "difference_rules": ["year_exact", "doi_exact"],
    }
    reversed_trace = {
        **trace,
        "citation_link_ids": list(reversed(trace["citation_link_ids"])),
        "reference_record_ids": list(reversed(trace["reference_record_ids"])),
        "verification_ids": list(reversed(trace["verification_ids"])),
        "difference_fields": list(reversed(trace["difference_fields"])),
        "difference_rules": list(reversed(trace["difference_rules"])),
    }
    assert make_finding_id(CitationFindingStatus.METADATA_MISMATCH, trace) == make_finding_id(CitationFindingStatus.METADATA_MISMATCH, reversed_trace)
    assert make_finding_id(CitationFindingStatus.METADATA_MISMATCH, trace) != make_finding_id(
        CitationFindingStatus.METADATA_MISMATCH, {**trace, "difference_rules": ["other_rule"]}
    )
    assert make_finding_id(CitationFindingStatus.METADATA_MISMATCH, trace) == make_finding_id(
        CitationFindingStatus.METADATA_MISMATCH,
        {**trace, "message": "Citation metadata differs.", "concern_state": "pending"},
    )


def test_duplicate_reference_metadata_uses_title_year_when_dois_differ() -> None:
    audit = build_citation_audit(
        paper_id="paper-1",
        parse_version="parse-1",
        ledger=ledger(),
        records=[
            record(record_id="R-1", doi="10.1000/one"),
            record(record_id="R-2", reference_number=2, doi="10.1000/two"),
        ],
        links=[],
        selected_verifications=[],
    )
    duplicate = next(
        finding for finding in audit.findings if finding.status is CitationFindingStatus.DUPLICATE_REFERENCE_METADATA
    )
    assert duplicate.reference_record_ids == ["R-1", "R-2"]


def valid_audit(tmp_path: Path):
    selected = verification(tmp_path)
    return build_citation_audit(
        paper_id="paper-1",
        parse_version="parse-1",
        ledger=ledger(),
        records=[record()],
        links=[link()],
        selected_verifications=[selected],
    )


def test_rejects_dangling_ids_and_response_hash_mismatch(tmp_path: Path) -> None:
    audit = valid_audit(tmp_path)
    broken = audit.model_copy(deep=True)
    broken.findings[0].mention_evidence_ids = ["missing-evidence"]
    with pytest.raises(CitationAuditIntegrityError, match="dangling_evidence_id"):
        validate_citation_audit(broken, ledger(), tmp_path)

    verification_record = audit.verifications[0]
    assert verification_record.raw_response_artifact is not None
    (tmp_path / verification_record.raw_response_artifact.path).write_bytes(b"tampered")
    with pytest.raises(CitationAuditIntegrityError, match="response_hash_mismatch"):
        validate_citation_audit(audit, ledger(), tmp_path)


@pytest.mark.parametrize(
    ("attribute", "bad_id", "error_code"),
    [
        ("citation_link_ids", "missing-link", "dangling_link_id"),
        ("reference_record_ids", "missing-record", "dangling_reference_id"),
        ("mention_evidence_ids", "missing-evidence", "dangling_evidence_id"),
        ("verification_ids", "missing-verification", "dangling_verification_id"),
    ],
)
def test_rejects_each_dangling_trace_id(tmp_path: Path, attribute: str, bad_id: str, error_code: str) -> None:
    audit = valid_audit(tmp_path)
    setattr(audit.findings[0], attribute, [bad_id])
    with pytest.raises(CitationAuditIntegrityError, match=error_code):
        validate_citation_audit(audit, ledger(), tmp_path)


def test_rejects_inconsistent_chain_and_duplicate_ids(tmp_path: Path) -> None:
    audit = valid_audit(tmp_path)
    audit.records.append(record(record_id="R-2", reference_number=2))
    audit.findings[0].reference_record_ids = ["R-2"]
    with pytest.raises(CitationAuditIntegrityError, match="inconsistent_finding_chain"):
        validate_citation_audit(audit, ledger(), tmp_path)

    audit = valid_audit(tmp_path / "duplicate")
    audit.links.append(audit.links[0].model_copy(deep=True))
    with pytest.raises(CitationAuditIntegrityError, match="duplicate_link_id"):
        validate_citation_audit(audit, ledger(), tmp_path / "duplicate")


def test_rejects_finding_id_and_status_that_do_not_match_the_trace(tmp_path: Path) -> None:
    audit = valid_audit(tmp_path)
    audit.findings[0].id = "F-verified-deadbeef"
    with pytest.raises(CitationAuditIntegrityError, match="finding_id_mismatch"):
        validate_citation_audit(audit, ledger(), tmp_path)

    audit = valid_audit(tmp_path / "status")
    audit.findings[0].status = CitationFindingStatus.MISSING_REFERENCE
    audit.findings[0].reference_record_ids = []
    audit.findings[0].reference_evidence_ids = []
    audit.findings[0].verification_ids = []
    audit.findings[0].requires_human_review = True
    with pytest.raises(CitationAuditIntegrityError, match="inconsistent_finding_chain"):
        validate_citation_audit(audit, ledger(), tmp_path / "status")


def test_atomic_write_preserves_previous_file_and_publishes_valid(tmp_path: Path) -> None:
    index = tmp_path / "citation_audit.json"
    index.write_bytes(b'{"previous":true}')
    audit = valid_audit(tmp_path)
    invalid = audit.model_copy(deep=True)
    invalid.findings[0].verification_ids = ["missing-verification"]

    with pytest.raises(CitationAuditIntegrityError):
        write_citation_audit_atomic(index, invalid, ledger(), tmp_path)
    assert index.read_bytes() == b'{"previous":true}'

    output = write_citation_audit_atomic(index, audit, ledger(), tmp_path)
    assert output == index
    assert hashlib.sha256(index.read_bytes()).hexdigest()
    assert b'"schema_version":"peerassist.citation_audit.v1"' in index.read_bytes()

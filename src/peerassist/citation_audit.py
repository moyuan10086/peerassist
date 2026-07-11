"""Produce and safely publish traceable citation-audit findings."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Never

from pydantic import ValidationError

from peerassist.citation_artifacts import expected_response_artifact_path, validate_response_artifact
from peerassist.citation_metadata import normalize_doi, normalize_title, title_similarity
from schemas.citation import (
    CitationAudit,
    CitationAuditFinding,
    CitationFieldComparison,
    CitationFindingSeverity,
    CitationFindingStatus,
    CitationLink,
    CitationLinkStatus,
    CitationVerification,
    ReferenceRecord,
    VerificationStatus,
)
from schemas.peerassist import EvidenceLedger, EvidenceType

_TRACE_LIST_KEYS = frozenset(
    {
        "citation_link_ids",
        "reference_record_ids",
        "mention_evidence_ids",
        "reference_evidence_ids",
        "verification_ids",
        "difference_fields",
        "difference_rules",
    }
)
_PRESENTATION_TRACE_KEYS = frozenset({"message", "language", "concern_state", "requires_human_review", "severity"})
_REFERENCE_ONLY_STATUSES = frozenset(
    {
        CitationFindingStatus.UNCITED_REFERENCE,
        CitationFindingStatus.MALFORMED_REFERENCE,
        CitationFindingStatus.DUPLICATE_REFERENCE_METADATA,
    }
)
_NUMBERED_REFERENCE_PREFIX_RE = re.compile(r"^\s*\[\s*(?P<number>\d+)\s*\]")
_COMPARABLE_METADATA_FIELDS = frozenset({"doi", "title", "year"})


class CitationAuditIntegrityError(Exception):
    """A stable integrity error that prevents an audit index from publishing."""

    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(error_code)


def finding_status_for(
    citation_link: CitationLink, verification: CitationVerification | None
) -> CitationFindingStatus:
    """Apply the ordered citation-finding decision table without I/O."""
    if citation_link.status is CitationLinkStatus.MISSING_REFERENCE:
        return CitationFindingStatus.MISSING_REFERENCE
    if citation_link.status is CitationLinkStatus.AMBIGUOUS:
        return CitationFindingStatus.AMBIGUOUS
    if verification is None:
        return CitationFindingStatus.INSUFFICIENT_EVIDENCE
    if verification.status is VerificationStatus.NOT_FOUND:
        return CitationFindingStatus.NOT_FOUND
    if verification.status is VerificationStatus.AMBIGUOUS:
        return CitationFindingStatus.AMBIGUOUS
    if verification.status is VerificationStatus.UNAVAILABLE:
        return CitationFindingStatus.INSUFFICIENT_EVIDENCE
    if verification.status is VerificationStatus.FAILED:
        return CitationFindingStatus.VERIFICATION_FAILED

    differences = [
        item for item in verification.field_differences if item.field in _COMPARABLE_METADATA_FIELDS
    ]
    if any(item.comparison is CitationFieldComparison.MISMATCH for item in differences):
        return CitationFindingStatus.METADATA_MISMATCH
    comparable_fields = {
        item.field
        for item in differences
        if item.comparison in {CitationFieldComparison.MATCH, CitationFieldComparison.MISMATCH}
    }
    if len(comparable_fields) < 2:
        return CitationFindingStatus.INSUFFICIENT_EVIDENCE
    return CitationFindingStatus.VERIFIED


def make_finding_id(status: CitationFindingStatus, trace: Mapping[str, Any]) -> str:
    """Return an ID based solely on the canonical, non-narrative finding trace."""
    canonical = _canonical_trace(dict(trace))
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"F-{status.value}-{hashlib.sha256(encoded).hexdigest()[:8]}"


def build_citation_audit(
    *,
    paper_id: str,
    parse_version: str,
    ledger: EvidenceLedger,
    records: Sequence[ReferenceRecord],
    links: Sequence[CitationLink],
    selected_verifications: Sequence[CitationVerification],
    warnings: Sequence[str] = (),
) -> CitationAudit:
    """Build a citation audit from explicit links and already-selected attempts.

    Attempt selection is deliberately not repeated here: callers provide the
    authoritative verification for each reference record.
    """
    ordered_records = sorted(records, key=lambda item: item.id)
    ordered_links = sorted(links, key=lambda item: item.id)
    ordered_verifications = sorted(selected_verifications, key=lambda item: item.id)
    findings = _expected_findings(ordered_records, ordered_links, ordered_verifications)
    return CitationAudit(
        schema_version="peerassist.citation_audit.v1",
        paper_id=paper_id,
        parse_version=parse_version,
        records=ordered_records,
        links=ordered_links,
        verifications=ordered_verifications,
        findings=findings,
        coverage=_coverage(ledger, ordered_records, ordered_links, ordered_verifications, findings),
        warnings=list(warnings),
    )


def validate_citation_audit(
    audit: CitationAudit,
    ledger: EvidenceLedger,
    artifact_root: str | Path,
) -> None:
    """Validate all audit references and immutable response-artifact bindings."""
    _validate_verification_match_contracts(audit.verifications)
    _validate_schema(audit)
    if audit.paper_id != ledger.paper_id:
        _fail("paper_id_mismatch")

    evidence_by_id = _unique_index(ledger.items, "duplicate_evidence_id")
    records_by_id = _unique_index(audit.records, "duplicate_record_id")
    links_by_id = _unique_index(audit.links, "duplicate_link_id")
    verifications_by_id = _unique_index(audit.verifications, "duplicate_verification_id")
    _unique_index(audit.findings, "duplicate_finding_id")
    _selected_verifications_by_record(audit.verifications)

    for record in audit.records:
        _validate_distinct_ids(record.source_evidence_ids)
        for evidence_id in record.source_evidence_ids:
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None:
                _fail("dangling_evidence_id")
            if evidence.type is not EvidenceType.REFERENCE:
                _fail("inconsistent_reference_chain")

    for citation_link in audit.links:
        _validate_distinct_ids(citation_link.reference_record_ids)
        _validate_distinct_ids(citation_link.reference_evidence_ids)
        if citation_link.status is CitationLinkStatus.AMBIGUOUS and len(set(citation_link.reference_record_ids)) < 2:
            _fail("inconsistent_link_chain")
        mention = evidence_by_id.get(citation_link.mention_evidence_id)
        if mention is None:
            _fail("dangling_evidence_id")
        if mention.type is not EvidenceType.CITATION:
            _fail("inconsistent_link_chain")
        linked_records = _records_for_link(citation_link, records_by_id)
        linked_evidence_ids = _reference_evidence_ids(linked_records)
        for evidence_id in citation_link.reference_evidence_ids:
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None:
                _fail("dangling_evidence_id")
            if evidence.type is not EvidenceType.REFERENCE:
                _fail("inconsistent_link_chain")
        if set(citation_link.reference_evidence_ids) != linked_evidence_ids:
            _fail("inconsistent_link_chain")

    _validate_verification_provenance_uniqueness(audit.verifications)
    for verification in audit.verifications:
        if verification.match is not None:
            _validate_distinct_ids(verification.match.candidate_ids)
        if verification.reference_record_id not in records_by_id:
            _fail("dangling_reference_id")
        if verification.source != verification.adapter.name:
            _fail("inconsistent_verification_chain")
        _validate_response_artifact(verification, artifact_root)

    for finding in audit.findings:
        _validate_finding(
            finding,
            evidence_by_id=evidence_by_id,
            records_by_id=records_by_id,
            links_by_id=links_by_id,
            verifications_by_id=verifications_by_id,
        )
    _validate_finding_set(audit, _expected_findings(audit.records, audit.links, audit.verifications))
    _validate_coverage(audit, ledger)


def write_citation_audit_atomic(
    path: str | Path,
    audit: CitationAudit,
    ledger: EvidenceLedger,
    artifact_root: str | Path | None = None,
) -> Path:
    """Validate then atomically replace the canonical citation-audit index."""
    target = Path(path)
    validate_citation_audit(audit, ledger, artifact_root if artifact_root is not None else target.parent)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        audit.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    temporary_path: Path | None = None
    backup_path: Path | None = None
    replaced = False
    retain_backup = False
    try:
        temporary_path = _write_temporary_bytes(target.parent, f".{target.name}.new.", payload)
        if target.exists():
            backup_path = _write_temporary_bytes(target.parent, f".{target.name}.backup.", target.read_bytes())
        os.replace(temporary_path, target)
        temporary_path = None
        replaced = True
        _fsync_directory(target.parent)
    except OSError as exc:
        if replaced:
            if _restore_previous_index(target, backup_path):
                backup_path = None
            else:
                retain_backup = backup_path is not None
            raise CitationAuditIntegrityError("index_publish_failed") from exc
        raise CitationAuditIntegrityError("audit_write_failed") from exc
    finally:
        _best_effort_unlink(temporary_path)
        if not retain_backup:
            _best_effort_unlink(backup_path)
    return target


def _link_findings(
    links: Iterable[CitationLink], verification_by_record: Mapping[str, CitationVerification]
) -> list[CitationAuditFinding]:
    findings: list[CitationAuditFinding] = []
    for citation_link in links:
        verification = (
            verification_by_record.get(citation_link.reference_record_ids[0])
            if citation_link.status is CitationLinkStatus.LINKED
            else None
        )
        status = finding_status_for(citation_link, verification)
        findings.append(_finding_for_link(citation_link, verification, status))
    return findings


def _expected_findings(
    records: Sequence[ReferenceRecord], links: Sequence[CitationLink], verifications: Sequence[CitationVerification]
) -> list[CitationAuditFinding]:
    findings = _link_findings(links, _selected_verifications_by_record(verifications))
    findings.extend(_reference_only_findings(records, links))
    return sorted(findings, key=lambda item: item.id)


def _selected_verifications_by_record(
    verifications: Iterable[CitationVerification],
) -> dict[str, CitationVerification]:
    selected: dict[str, CitationVerification] = {}
    for verification in verifications:
        if verification.reference_record_id in selected:
            _fail("duplicate_reference_verification")
        selected[verification.reference_record_id] = verification
    return selected


def _finding_for_link(
    citation_link: CitationLink,
    verification: CitationVerification | None,
    status: CitationFindingStatus,
) -> CitationAuditFinding:
    is_missing = status is CitationFindingStatus.MISSING_REFERENCE
    verification_ids = [verification.id] if verification is not None and not is_missing else []
    trace = {
        "citation_link_ids": [citation_link.id],
        "reference_record_ids": [] if is_missing else citation_link.reference_record_ids,
        "mention_evidence_ids": [citation_link.mention_evidence_id],
        "reference_evidence_ids": [] if is_missing else citation_link.reference_evidence_ids,
        "verification_ids": verification_ids,
        "difference_fields": [item.field for item in verification.field_differences] if verification else [],
        "difference_rules": [item.rule for item in verification.field_differences] if verification else [],
    }
    metadata: dict[str, Any] = {
        "difference_fields": sorted(set(trace["difference_fields"])),
        "difference_rules": sorted(set(trace["difference_rules"])),
    }
    if verification is not None:
        metadata["attempt_id"] = verification.attempt_id
        metadata["error_code"] = verification.error_code
        if verification.raw_response_artifact is not None:
            metadata["raw_response_artifact"] = verification.raw_response_artifact.model_dump(mode="json")
    return CitationAuditFinding(
        id=make_finding_id(status, trace),
        status=status,
        severity=_severity_for(status),
        citation_link_ids=[citation_link.id],
        reference_record_ids=[] if is_missing else list(citation_link.reference_record_ids),
        mention_evidence_ids=[citation_link.mention_evidence_id],
        reference_evidence_ids=[] if is_missing else list(citation_link.reference_evidence_ids),
        verification_ids=verification_ids,
        message=_message_for(status),
        requires_human_review=status is not CitationFindingStatus.VERIFIED,
        metadata=metadata,
    )


def _reference_only_findings(
    records: Sequence[ReferenceRecord], links: Sequence[CitationLink]
) -> list[CitationAuditFinding]:
    cited_record_ids = {record_id for citation_link in links for record_id in citation_link.reference_record_ids}
    findings: list[CitationAuditFinding] = []
    for record in records:
        if record.id not in cited_record_ids:
            findings.append(_reference_finding(CitationFindingStatus.UNCITED_REFERENCE, [record]))
        if _is_malformed_reference(record):
            findings.append(_reference_finding(CitationFindingStatus.MALFORMED_REFERENCE, [record]))

    duplicate_groups: dict[tuple[str, ...], list[ReferenceRecord]] = {}
    for record in records:
        doi = normalize_doi(record.doi)
        if doi:
            duplicate_groups.setdefault(("doi", doi), []).append(record)
        title = normalize_title(record.title)
        if title and _valid_reference_year(record.year):
            duplicate_groups.setdefault(("title_year", title, str(record.year)), []).append(record)
    for index, record in enumerate(records):
        if not (normalize_title(record.title) and _valid_reference_year(record.year)):
            continue
        for candidate in records[index + 1 :]:
            if (
                candidate.year == record.year
                and _valid_reference_year(candidate.year)
                and title_similarity(record.title, candidate.title) >= 0.95
            ):
                duplicate_groups.setdefault(("similarity", record.id, candidate.id), []).extend(
                    [record, candidate]
                )
    emitted_members: set[tuple[str, ...]] = set()
    for group in duplicate_groups.values():
        member_ids = tuple(sorted(record.id for record in group))
        if len(group) > 1 and member_ids not in emitted_members:
            findings.append(_reference_finding(CitationFindingStatus.DUPLICATE_REFERENCE_METADATA, group))
            emitted_members.add(member_ids)
    return findings


def _reference_finding(status: CitationFindingStatus, records: Sequence[ReferenceRecord]) -> CitationAuditFinding:
    record_ids = sorted(record.id for record in records)
    evidence_ids = sorted({evidence_id for record in records for evidence_id in record.source_evidence_ids})
    trace = {
        "citation_link_ids": [],
        "reference_record_ids": record_ids,
        "mention_evidence_ids": [],
        "reference_evidence_ids": evidence_ids,
        "verification_ids": [],
        "difference_fields": [],
        "difference_rules": [],
    }
    return CitationAuditFinding(
        id=make_finding_id(status, trace),
        status=status,
        severity=CitationFindingSeverity.MINOR_CONCERN,
        citation_link_ids=[],
        reference_record_ids=record_ids,
        mention_evidence_ids=[],
        reference_evidence_ids=evidence_ids,
        verification_ids=[],
        message=_message_for(status),
        requires_human_review=True,
        metadata={},
    )


def _severity_for(status: CitationFindingStatus) -> CitationFindingSeverity:
    if status is CitationFindingStatus.VERIFIED:
        return CitationFindingSeverity.EDITOR_NOTE
    if status in _REFERENCE_ONLY_STATUSES:
        return CitationFindingSeverity.MINOR_CONCERN
    return CitationFindingSeverity.CLARIFICATION_NEEDED


def _message_for(status: CitationFindingStatus) -> str:
    return f"Citation audit status: {status.value}."


def _canonical_trace(value: Any, *, key: str = "") -> Any:
    if isinstance(value, Mapping):
        return {
            name: _canonical_trace(item, key=name)
            for name, item in sorted(value.items())
            if name not in _PRESENTATION_TRACE_KEYS
        }
    if isinstance(value, list):
        items = [_canonical_trace(item) for item in value]
        return sorted(items, key=_canonical_sort_key) if key in _TRACE_LIST_KEYS else items
    return value


def _canonical_sort_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _unique_index(items: Iterable[Any], duplicate_error: str) -> dict[str, Any]:
    index: dict[str, Any] = {}
    for item in items:
        if item.id in index:
            _fail(duplicate_error)
        index[item.id] = item
    return index


def _records_for_link(
    citation_link: CitationLink, records_by_id: Mapping[str, ReferenceRecord]
) -> list[ReferenceRecord]:
    records: list[ReferenceRecord] = []
    for record_id in citation_link.reference_record_ids:
        record = records_by_id.get(record_id)
        if record is None:
            _fail("dangling_reference_id")
        if record.reference_number != citation_link.reference_number:
            _fail("inconsistent_link_chain")
        records.append(record)
    return records


def _reference_evidence_ids(records: Iterable[ReferenceRecord]) -> set[str]:
    return {evidence_id for record in records for evidence_id in record.source_evidence_ids}


def _is_malformed_reference(record: ReferenceRecord) -> bool:
    match = _NUMBERED_REFERENCE_PREFIX_RE.match(record.raw_text)
    has_matching_prefix = match is not None and int(match.group("number")) == record.reference_number
    has_metadata = bool(normalize_doi(record.doi) or normalize_title(record.title) or record.year is not None)
    return not has_matching_prefix or not has_metadata


def _valid_reference_year(year: int | None) -> bool:
    return year is not None and 0 < year < 10000


def _coverage(
    ledger: EvidenceLedger,
    records: Sequence[ReferenceRecord],
    links: Sequence[CitationLink],
    verifications: Sequence[CitationVerification],
    findings: Sequence[CitationAuditFinding],
) -> dict[str, int]:
    coverage = {
        "records": len(records),
        "mentions": sum(item.type is EvidenceType.CITATION for item in ledger.items),
        "reference_evidence": sum(item.type is EvidenceType.REFERENCE for item in ledger.items),
        "links": len(links),
        "verifications": len(verifications),
        "findings": len(findings),
    }
    coverage.update({f"links.{status.value}": 0 for status in CitationLinkStatus if status is not CitationLinkStatus.UNSUPPORTED_SYNTAX})
    coverage.update({f"verifications.{status.value}": 0 for status in VerificationStatus})
    coverage.update({f"findings.{status.value}": 0 for status in CitationFindingStatus})
    for citation_link in links:
        coverage[f"links.{citation_link.status.value}"] += 1
    for verification in verifications:
        coverage[f"verifications.{verification.status.value}"] += 1
    for finding in findings:
        coverage[f"findings.{finding.status.value}"] += 1
    return coverage


def _validate_coverage(audit: CitationAudit, ledger: EvidenceLedger) -> None:
    if audit.coverage != _coverage(ledger, audit.records, audit.links, audit.verifications, audit.findings):
        _fail("coverage_mismatch")


def _validate_verification_provenance_uniqueness(verifications: Iterable[CitationVerification]) -> None:
    attempt_ids: set[str] = set()
    artifact_paths: set[str] = set()
    for verification in verifications:
        if verification.attempt_id in attempt_ids:
            _fail("duplicate_attempt_id")
        attempt_ids.add(verification.attempt_id)
        if verification.raw_response_artifact is None:
            continue
        if verification.raw_response_artifact.path in artifact_paths:
            _fail("duplicate_response_artifact_path")
        artifact_paths.add(verification.raw_response_artifact.path)


def _validate_verification_match_contracts(verifications: Iterable[CitationVerification]) -> None:
    for verification in verifications:
        match = verification.match
        if match is None:
            if verification.status in {
                VerificationStatus.COMPLETED,
                VerificationStatus.NOT_FOUND,
                VerificationStatus.AMBIGUOUS,
            }:
                _fail("verification_match_mismatch")
            continue

        candidate_ids = match.candidate_ids
        if len(candidate_ids) != len(set(candidate_ids)) or match.candidate_count != len(candidate_ids):
            _fail("verification_match_mismatch")
        if verification.status is VerificationStatus.COMPLETED:
            if (
                match.candidate_count != 1
                or not match.selected_candidate_id
                or match.selected_candidate_id not in candidate_ids
                or verification.source_record is None
                or verification.source_record.id != match.selected_candidate_id
            ):
                _fail("verification_match_mismatch")
        elif verification.status is VerificationStatus.NOT_FOUND:
            if match.candidate_count != 0 or candidate_ids or match.selected_candidate_id is not None:
                _fail("verification_match_mismatch")
        elif verification.status is VerificationStatus.AMBIGUOUS:
            if match.candidate_count < 1 or match.selected_candidate_id is not None:
                _fail("verification_match_mismatch")
        elif verification.status is VerificationStatus.UNAVAILABLE:
            _fail("verification_match_mismatch")


def _validate_response_artifact(verification: CitationVerification, artifact_root: str | Path) -> None:
    artifact = verification.raw_response_artifact
    if artifact is None:
        return
    try:
        expected_path = expected_response_artifact_path(verification.attempt_id)
    except ValueError:
        _fail("raw_response_path_mismatch")
    if artifact.path != expected_path:
        _fail("raw_response_path_mismatch")
    if not validate_response_artifact(artifact_root, artifact):
        _fail("response_hash_mismatch")


def _validate_finding(
    finding: CitationAuditFinding,
    *,
    evidence_by_id: Mapping[str, Any],
    records_by_id: Mapping[str, ReferenceRecord],
    links_by_id: Mapping[str, CitationLink],
    verifications_by_id: Mapping[str, CitationVerification],
) -> None:
    _validate_distinct_ids(finding.citation_link_ids)
    _validate_distinct_ids(finding.reference_record_ids)
    _validate_distinct_ids(finding.mention_evidence_ids)
    _validate_distinct_ids(finding.reference_evidence_ids)
    _validate_distinct_ids(finding.verification_ids)
    _validate_ids(finding.citation_link_ids, links_by_id, "dangling_link_id")
    _validate_ids(finding.reference_record_ids, records_by_id, "dangling_reference_id")
    _validate_ids(finding.mention_evidence_ids, evidence_by_id, "dangling_evidence_id")
    _validate_ids(finding.reference_evidence_ids, evidence_by_id, "dangling_evidence_id")
    _validate_ids(finding.verification_ids, verifications_by_id, "dangling_verification_id")

    if finding.status is CitationFindingStatus.MISSING_REFERENCE:
        linked = [links_by_id[link_id] for link_id in finding.citation_link_ids]
        if any(link.status is not CitationLinkStatus.MISSING_REFERENCE for link in linked):
            _fail("inconsistent_finding_chain")
        linked_mentions = {link.mention_evidence_id for link in linked}
        if set(finding.mention_evidence_ids) != linked_mentions:
            _fail("inconsistent_finding_chain")
        _validate_finding_status(finding, linked, verifications_by_id)
        _validate_finding_provenance(finding, verifications_by_id)
        _validate_finding_id(finding, verifications_by_id)
        return
    if finding.status in _REFERENCE_ONLY_STATUSES:
        records = [records_by_id[record_id] for record_id in finding.reference_record_ids]
        if set(finding.reference_evidence_ids) != _reference_evidence_ids(records):
            _fail("inconsistent_finding_chain")
        _validate_reference_only_finding_status(finding, records_by_id, links_by_id)
        _validate_finding_provenance(finding, verifications_by_id)
        _validate_finding_id(finding, verifications_by_id)
        return

    linked_records = [links_by_id[link_id] for link_id in finding.citation_link_ids]
    if set(finding.mention_evidence_ids) != {link.mention_evidence_id for link in linked_records}:
        _fail("inconsistent_finding_chain")
    if set(finding.reference_record_ids) != {record_id for link in linked_records for record_id in link.reference_record_ids}:
        _fail("inconsistent_finding_chain")
    if set(finding.reference_evidence_ids) != {
        evidence_id for link in linked_records for evidence_id in link.reference_evidence_ids
    }:
        _fail("inconsistent_finding_chain")
    for verification_id in finding.verification_ids:
        if verifications_by_id[verification_id].reference_record_id not in finding.reference_record_ids:
            _fail("inconsistent_finding_chain")
    _validate_finding_status(finding, linked_records, verifications_by_id)
    _validate_finding_provenance(finding, verifications_by_id)
    _validate_finding_id(finding, verifications_by_id)


def _validate_ids(ids: Iterable[str], index: Mapping[str, Any], error_code: str) -> None:
    if any(item_id not in index for item_id in ids):
        _fail(error_code)


def _validate_distinct_ids(ids: Sequence[str]) -> None:
    if len(ids) != len(set(ids)):
        _fail("duplicate_trace_id")


def _validate_finding_id(
    finding: CitationAuditFinding, verifications_by_id: Mapping[str, CitationVerification]
) -> None:
    differences = [
        difference
        for verification_id in finding.verification_ids
        for difference in verifications_by_id[verification_id].field_differences
    ]
    trace = {
        "citation_link_ids": finding.citation_link_ids,
        "reference_record_ids": finding.reference_record_ids,
        "mention_evidence_ids": finding.mention_evidence_ids,
        "reference_evidence_ids": finding.reference_evidence_ids,
        "verification_ids": finding.verification_ids,
        "difference_fields": [difference.field for difference in differences],
        "difference_rules": [difference.rule for difference in differences],
    }
    if finding.id != make_finding_id(finding.status, trace):
        _fail("finding_id_mismatch")


def _validate_finding_set(audit: CitationAudit, expected: Sequence[CitationAuditFinding]) -> None:
    actual_signatures = {
        _finding_signature(finding, {verification.id: verification for verification in audit.verifications})
        for finding in audit.findings
    }
    expected_signatures = {
        _finding_signature(finding, {verification.id: verification for verification in audit.verifications})
        for finding in expected
    }
    if actual_signatures != expected_signatures:
        _fail("finding_set_mismatch")


def _finding_signature(
    finding: CitationAuditFinding, verifications_by_id: Mapping[str, CitationVerification]
) -> tuple[str, str, str]:
    differences = [
        difference
        for verification_id in finding.verification_ids
        for difference in verifications_by_id[verification_id].field_differences
    ]
    trace = {
        "citation_link_ids": finding.citation_link_ids,
        "reference_record_ids": finding.reference_record_ids,
        "mention_evidence_ids": finding.mention_evidence_ids,
        "reference_evidence_ids": finding.reference_evidence_ids,
        "verification_ids": finding.verification_ids,
        "difference_fields": [difference.field for difference in differences],
        "difference_rules": [difference.rule for difference in differences],
    }
    return finding.id, finding.status.value, _canonical_sort_key(_canonical_trace(trace))


def _validate_finding_status(
    finding: CitationAuditFinding,
    citation_links: Sequence[CitationLink],
    verifications_by_id: Mapping[str, CitationVerification],
) -> None:
    expected_statuses: set[CitationFindingStatus] = set()
    for citation_link in citation_links:
        verifications = [
            verifications_by_id[verification_id]
            for verification_id in finding.verification_ids
            if verifications_by_id[verification_id].reference_record_id in citation_link.reference_record_ids
        ]
        if len(verifications) > 1:
            _fail("inconsistent_finding_chain")
        expected_statuses.add(finding_status_for(citation_link, verifications[0] if verifications else None))
    if expected_statuses != {finding.status}:
        _fail("finding_status_mismatch")


def _validate_reference_only_finding_status(
    finding: CitationAuditFinding,
    records_by_id: Mapping[str, ReferenceRecord],
    links_by_id: Mapping[str, CitationLink],
) -> None:
    expected = _reference_only_findings(
        sorted(records_by_id.values(), key=lambda record: record.id),
        sorted(links_by_id.values(), key=lambda citation_link: citation_link.id),
    )
    same_trace = [
        candidate
        for candidate in expected
        if set(candidate.reference_record_ids) == set(finding.reference_record_ids)
        and set(candidate.reference_evidence_ids) == set(finding.reference_evidence_ids)
    ]
    if not same_trace:
        _fail("inconsistent_finding_chain")
    if all(candidate.status is not finding.status for candidate in same_trace):
        _fail("finding_status_mismatch")


def _validate_finding_provenance(
    finding: CitationAuditFinding, verifications_by_id: Mapping[str, CitationVerification]
) -> None:
    verifications = [verifications_by_id[verification_id] for verification_id in finding.verification_ids]
    if not verifications and finding.status in _REFERENCE_ONLY_STATUSES:
        if finding.metadata != {}:
            _fail("finding_provenance_mismatch")
        return
    expected: dict[str, Any] = {
        "difference_fields": sorted({difference.field for verification in verifications for difference in verification.field_differences}),
        "difference_rules": sorted({difference.rule for verification in verifications for difference in verification.field_differences}),
    }
    if len(verifications) == 1:
        verification = verifications[0]
        expected["attempt_id"] = verification.attempt_id
        expected["error_code"] = verification.error_code
        if verification.raw_response_artifact is not None:
            expected["raw_response_artifact"] = verification.raw_response_artifact.model_dump(mode="json")
    if finding.metadata != expected:
        _fail("finding_provenance_mismatch")


def _validate_schema(audit: CitationAudit) -> None:
    try:
        CitationAudit.model_validate(audit.model_dump(mode="json"))
    except ValidationError as exc:
        raise CitationAuditIntegrityError("invalid_audit_schema") from exc


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_temporary_bytes(directory: Path, prefix: str, payload: bytes) -> Path:
    with tempfile.NamedTemporaryFile(mode="wb", dir=directory, prefix=prefix, delete=False) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        return Path(handle.name)


def _restore_previous_index(target: Path, backup_path: Path | None) -> bool:
    try:
        if backup_path is None:
            target.unlink(missing_ok=True)
        else:
            os.replace(backup_path, target)
    except OSError:
        return False
    _best_effort_fsync_directory(target.parent)
    return True


def _best_effort_fsync_directory(path: Path) -> None:
    try:
        _fsync_directory(path)
    except OSError:
        pass


def _best_effort_unlink(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _fail(error_code: str) -> Never:
    raise CitationAuditIntegrityError(error_code)

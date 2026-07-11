"""Produce and safely publish traceable citation-audit findings."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Never

from pydantic import ValidationError

from peerassist.citation_artifacts import expected_response_artifact_path, validate_response_artifact
from peerassist.citation_metadata import normalize_doi, normalize_title
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

    differences = verification.field_differences
    if any(item.comparison is CitationFieldComparison.MISMATCH for item in differences):
        return CitationFindingStatus.METADATA_MISMATCH
    if len(differences) < 2:
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
    verification_by_record = {item.reference_record_id: item for item in ordered_verifications}
    findings = _link_findings(ordered_links, verification_by_record)
    findings.extend(_reference_only_findings(ordered_records, ordered_links))
    findings.sort(key=lambda item: item.id)
    return CitationAudit(
        schema_version="peerassist.citation_audit.v1",
        paper_id=paper_id,
        parse_version=parse_version,
        records=ordered_records,
        links=ordered_links,
        verifications=ordered_verifications,
        findings=findings,
        coverage={
            "mentions": sum(item.type is EvidenceType.CITATION for item in ledger.items),
            "references": len(ordered_records),
            "links": len(ordered_links),
            "verifications": len(ordered_verifications),
            "findings": len(findings),
        },
        warnings=list(warnings),
    )


def validate_citation_audit(
    audit: CitationAudit,
    ledger: EvidenceLedger,
    artifact_root: str | Path,
) -> None:
    """Validate all audit references and immutable response-artifact bindings."""
    _validate_schema(audit)
    if audit.paper_id != ledger.paper_id:
        _fail("paper_id_mismatch")

    evidence_by_id = _unique_index(ledger.items, "duplicate_evidence_id")
    records_by_id = _unique_index(audit.records, "duplicate_record_id")
    links_by_id = _unique_index(audit.links, "duplicate_link_id")
    verifications_by_id = _unique_index(audit.verifications, "duplicate_verification_id")
    _unique_index(audit.findings, "duplicate_finding_id")

    for record in audit.records:
        for evidence_id in record.source_evidence_ids:
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None:
                _fail("dangling_evidence_id")
            if evidence.type is not EvidenceType.REFERENCE:
                _fail("inconsistent_reference_chain")

    for citation_link in audit.links:
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

    for verification in audit.verifications:
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
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=target.parent, prefix=f".{target.name}.", delete=False) as handle:
            temporary_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, target)
        _fsync_directory(target.parent)
    except OSError as exc:
        raise CitationAuditIntegrityError("audit_write_failed") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
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
        if not (normalize_doi(record.doi) or normalize_title(record.title) or record.year is not None):
            findings.append(_reference_finding(CitationFindingStatus.MALFORMED_REFERENCE, [record]))

    duplicate_groups: dict[tuple[str, ...], list[ReferenceRecord]] = {}
    for record in records:
        doi = normalize_doi(record.doi)
        if doi:
            duplicate_groups.setdefault(("doi", doi), []).append(record)
        title = normalize_title(record.title)
        if title and record.year is not None:
            duplicate_groups.setdefault(("title_year", title, str(record.year)), []).append(record)
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
        _validate_finding_id(finding, verifications_by_id)
        return
    if finding.status in _REFERENCE_ONLY_STATUSES:
        if set(finding.reference_evidence_ids) != _reference_evidence_ids(
            records_by_id[record_id] for record_id in finding.reference_record_ids
        ):
            _fail("inconsistent_finding_chain")
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
    _validate_finding_id(finding, verifications_by_id)


def _validate_ids(ids: Iterable[str], index: Mapping[str, Any], error_code: str) -> None:
    if any(item_id not in index for item_id in ids):
        _fail(error_code)


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


def _fail(error_code: str) -> Never:
    raise CitationAuditIntegrityError(error_code)

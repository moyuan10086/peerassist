"""Strict contracts for traceable citation evidence and audit artifacts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from schemas.peerassist import EvidenceItem


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


class CitationFieldComparison(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"


class CitationFindingSeverity(StrEnum):
    MAJOR_CONCERN = "major_concern"
    MINOR_CONCERN = "minor_concern"
    CLARIFICATION_NEEDED = "clarification_needed"
    EDITOR_NOTE = "editor_note"


class ReferenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    reference_number: int = Field(gt=0)
    source_evidence_ids: list[str] = Field(min_length=1)
    raw_text: str
    title: str = ""
    doi: str = ""
    year: int | None = None
    parse_confidence: float = Field(default=0, ge=0, le=1)


class CitationLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    mention_evidence_id: str
    reference_number: int = Field(gt=0)
    status: CitationLinkStatus
    reference_record_ids: list[str] = Field(default_factory=list)
    reference_evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status_cardinality(self) -> CitationLink:
        record_count = len(self.reference_record_ids)
        if self.status is CitationLinkStatus.UNSUPPORTED_SYNTAX:
            raise ValueError(
                "unsupported_syntax is represented by UnsupportedCitationMarker, not CitationLink"
            )
        if self.status is CitationLinkStatus.LINKED and record_count != 1:
            raise ValueError("linked citation link requires exactly one reference record")
        if self.status is CitationLinkStatus.AMBIGUOUS and record_count < 2:
            raise ValueError("ambiguous citation link requires at least two reference records")
        if self.status is CitationLinkStatus.MISSING_REFERENCE and (
            self.reference_record_ids or self.reference_evidence_ids
        ):
            raise ValueError("missing_reference citation link forbids reference trace")
        return self


class CitationFieldDifference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    manuscript_value: str
    external_value: str
    normalized_manuscript_value: str
    normalized_external_value: str
    comparison: CitationFieldComparison
    rule: str


class CitationAdapterInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)


class CitationSourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    url: str


class CitationMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: str
    candidate_count: int = Field(ge=0)
    selected_candidate_id: str | None
    selection_reason: str
    candidate_ids: list[str]


class RawResponseArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CitationVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    reference_record_id: str
    source: str
    status: VerificationStatus
    adapter: CitationAdapterInfo
    query: dict[str, JsonValue]
    attempt_id: str
    attempt_number: int = Field(gt=0)
    checked_at: str
    tool_call_id: str
    match: CitationMatch | None = None
    source_record: CitationSourceRecord | None = None
    raw_response_artifact: RawResponseArtifact | None = None
    error_code: str = ""
    observed_metadata: dict[str, JsonValue] = Field(default_factory=dict)
    field_differences: list[CitationFieldDifference] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status_contract(self) -> CitationVerification:
        status = self.status
        if status is VerificationStatus.COMPLETED:
            if (
                self.match is None
                or self.match.candidate_count != 1
                or not self.match.selected_candidate_id
                or self.source_record is None
                or self.raw_response_artifact is None
            ):
                raise ValueError(
                    "completed verification requires one selected match, source record, and raw response artifact"
                )
            if "observed_metadata" not in self.model_fields_set:
                raise ValueError("completed verification requires explicit observed_metadata")
            if (
                self.match.method.startswith("title_similarity")
                and self.match.method != "title_similarity_unique"
            ):
                raise ValueError(
                    "completed title similarity verification requires title_similarity_unique method"
                )
        elif status is VerificationStatus.NOT_FOUND:
            if (
                self.raw_response_artifact is None
                or self.match is None
                or self.match.candidate_count != 0
                or self.match.selected_candidate_id is not None
                or self.match.candidate_ids
                or self.source_record is not None
            ):
                raise ValueError(
                    "not_found verification requires a zero-candidate match and raw response without selected/source record"
                )
        elif status is VerificationStatus.AMBIGUOUS:
            if (
                self.raw_response_artifact is None
                or self.match is None
                or self.match.candidate_count < 1
                or self.match.selected_candidate_id is not None
                or self.source_record is not None
            ):
                raise ValueError(
                    "ambiguous verification requires candidate evidence and raw response without authoritative selected/source record"
                )
            if self.match.candidate_count == 1 and (self.match.method != "title_similarity_ambiguous"):
                raise ValueError(
                    "ambiguous one-candidate verification requires title_similarity_ambiguous method"
                )
        elif status is VerificationStatus.UNAVAILABLE:
            if not self.error_code:
                raise ValueError("unavailable verification requires error_code")
            if any(
                value is not None
                for value in (
                    self.raw_response_artifact,
                    self.match,
                    self.source_record,
                )
            ):
                raise ValueError("unavailable verification forbids response, match, and source record")
        elif status is VerificationStatus.FAILED:
            if not self.error_code:
                raise ValueError("failed verification requires error_code")
            response_data_present = (
                self.match is not None
                or self.source_record is not None
                or bool(self.observed_metadata)
                or bool(self.field_differences)
            )
            if response_data_present and self.raw_response_artifact is None:
                raise ValueError("failed verification with response data requires raw response artifact")
        return self


class UnsupportedCitationMarker(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source_evidence_id: str
    raw: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    status: Literal[CitationLinkStatus.UNSUPPORTED_SYNTAX]
    error_code: str

    @model_validator(mode="after")
    def validate_offsets(self) -> UnsupportedCitationMarker:
        if self.end < self.start:
            raise ValueError("unsupported citation marker end must be >= start")
        return self


_VERIFICATION_DERIVED_FINDINGS = {
    CitationFindingStatus.VERIFIED,
    CitationFindingStatus.METADATA_MISMATCH,
    CitationFindingStatus.NOT_FOUND,
    CitationFindingStatus.INSUFFICIENT_EVIDENCE,
    CitationFindingStatus.VERIFICATION_FAILED,
}

_REFERENCE_ONLY_FINDINGS = {
    CitationFindingStatus.UNCITED_REFERENCE,
    CitationFindingStatus.MALFORMED_REFERENCE,
    CitationFindingStatus.DUPLICATE_REFERENCE_METADATA,
}


class CitationAuditFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: CitationFindingStatus
    severity: CitationFindingSeverity
    citation_link_ids: list[str]
    reference_record_ids: list[str]
    mention_evidence_ids: list[str]
    reference_evidence_ids: list[str]
    verification_ids: list[str]
    message: str
    requires_human_review: bool
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_traceability(self) -> CitationAuditFinding:
        status = self.status
        if status is CitationFindingStatus.VERIFIED:
            if self.requires_human_review:
                raise ValueError("verified finding requires requires_human_review=false")
        elif not self.requires_human_review:
            raise ValueError(f"{status.value} finding requires human review")

        if status is CitationFindingStatus.MISSING_REFERENCE:
            if not self.citation_link_ids or not self.mention_evidence_ids:
                raise ValueError("missing_reference finding requires citation link and mention evidence")
            if self.reference_record_ids or self.reference_evidence_ids or self.verification_ids:
                raise ValueError("missing_reference finding forbids reference trace")
            return self

        if status in _REFERENCE_ONLY_FINDINGS:
            if not self.reference_record_ids or not self.reference_evidence_ids:
                raise ValueError(f"{status.value} finding requires reference record and reference evidence")
            if self.citation_link_ids or self.mention_evidence_ids or self.verification_ids:
                raise ValueError(f"{status.value} is a reference-only finding")
            return self

        required_trace = (
            self.citation_link_ids,
            self.reference_record_ids,
            self.mention_evidence_ids,
            self.reference_evidence_ids,
        )
        if not all(required_trace):
            raise ValueError(f"{status.value} finding requires complete citation trace")

        verification_required = status in _VERIFICATION_DERIVED_FINDINGS or (
            status is CitationFindingStatus.AMBIGUOUS and len(self.reference_record_ids) == 1
        )
        if verification_required and not self.verification_ids:
            raise ValueError(f"{status.value} finding requires verification trace")
        return self


class CitationEvidenceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mentions: list[EvidenceItem]
    references: list[ReferenceRecord]
    links: list[CitationLink]
    unsupported_markers: list[UnsupportedCitationMarker] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CitationAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["peerassist.citation_audit.v1"]
    paper_id: str
    parse_version: str
    records: list[ReferenceRecord] = Field(default_factory=list)
    links: list[CitationLink] = Field(default_factory=list)
    verifications: list[CitationVerification] = Field(default_factory=list)
    findings: list[CitationAuditFinding] = Field(default_factory=list)
    coverage: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

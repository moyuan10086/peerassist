"""PeerAssist artifact schemas.

These models define the stable JSON contracts for the PeerAssist review-aid
layer. They intentionally describe artifacts, not implementation details, so
pipeline stages and future UIs can exchange the same records.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceType(StrEnum):
    TEXT_SPAN = "text_span"
    SECTION = "section"
    FIGURE = "figure"
    FIGURE_CAPTION = "figure_caption"
    TABLE = "table"
    TABLE_CELL = "table_cell"
    FORMULA = "formula"
    CITATION = "citation"
    REFERENCE = "reference"
    SUPPLEMENT = "supplement"
    CODE_ARTIFACT = "code_artifact"


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    type: EvidenceType
    page: int | None = None
    section: str = ""
    locator: str = ""
    text: str = ""
    bbox: list[float] | None = None
    source_path: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceLedger(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: str = "peerassist.evidence_ledger.v1"
    paper_id: str
    source_sha256: str = ""
    items: list[EvidenceItem] = Field(default_factory=list)
    coverage: dict[str, int] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def derive_coverage(self) -> "EvidenceLedger":
        if not self.coverage:
            counts: dict[str, int] = {}
            for item in self.items:
                counts[item.type.value] = counts.get(item.type.value, 0) + 1
            self.coverage = counts
        return self


class DeterministicCheckApplicability(StrEnum):
    APPLICABLE = "applicable"
    NOT_APPLICABLE = "not_applicable"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    PARSER_UNCERTAIN = "parser_uncertain"


class DeterministicCheckStatus(StrEnum):
    PASS = "pass"
    LEAD = "lead"
    INCONCLUSIVE = "inconclusive"
    FAILED_TO_RUN = "failed_to_run"


class DeterministicCheck(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    kind: str
    applicability: DeterministicCheckApplicability
    status: DeterministicCheckStatus
    evidence_ids: list[str] = Field(default_factory=list)
    message: str = ""
    benign_explanations: list[str] = Field(default_factory=list)
    requires_human_review: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConcernLevel(StrEnum):
    MAJOR_CONCERN = "major_concern"
    MINOR_CONCERN = "minor_concern"
    CLARIFICATION_NEEDED = "clarification_needed"
    EDITOR_NOTE = "editor_note"


class ConcernStatus(StrEnum):
    PENDING_HUMAN_CONFIRMATION = "pending_human_confirmation"
    CONFIRMED = "confirmed"
    DOWNGRADED = "downgraded"
    REWRITTEN = "rewritten"
    DELETED = "deleted"


class Concern(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    level: ConcernLevel
    category: str
    title: str
    evidence_ids: list[str] = Field(default_factory=list)
    impact: str = ""
    benign_explanation: str = ""
    author_action: str = ""
    status: ConcernStatus = ConcernStatus.PENDING_HUMAN_CONFIRMATION
    source_agent_ids: list[str] = Field(default_factory=list)
    source_check_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanConfirmationAction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    concern_id: str
    action: str
    previous_text: str = ""
    new_text: str = ""
    reviewer_id: str = "local-reviewer"
    timestamp: str
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolTraceStatus(StrEnum):
    QUEUED = "queued"
    STARTED = "started"
    PROGRESS = "progress"
    APPROVAL_REQUIRED = "approval_required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ARTIFACT_CREATED = "artifact_created"


class ToolTraceEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_id: str
    call_id: str
    agent_id: str = ""
    source: str
    tool: str
    status: ToolTraceStatus
    ts: str
    input_summary: str = ""
    output_summary: str = ""
    artifact_ids: list[str] = Field(default_factory=list)
    duration_ms: int | None = None
    error_code: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

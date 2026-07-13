"""PeerAssist artifact schemas.

These models define the stable JSON contracts for the PeerAssist review-aid
layer. They intentionally describe artifacts, not implementation details, so
pipeline stages and future UIs can exchange the same records.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
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
    def derive_coverage(self) -> EvidenceLedger:
        if not self.coverage:
            counts: dict[str, int] = {}
            for item in self.items:
                counts[item.type.value] = counts.get(item.type.value, 0) + 1
            self.coverage = counts
        return self


class ProvenanceKind(StrEnum):
    REPORTED = "reported"
    INFERRED = "inferred"
    NOT_FOUND = "not_found"


class GroundedField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Any = None
    evidence_ids: list[str] = Field(default_factory=list)
    provenance: ProvenanceKind = ProvenanceKind.NOT_FOUND
    needs_human_review: bool = False

    @model_validator(mode="after")
    def enforce_grounding(self) -> GroundedField:
        has_value = self.value not in (None, "", [], {})
        if has_value and not self.evidence_ids:
            self.needs_human_review = True
        if not has_value:
            self.needs_human_review = True
            self.provenance = ProvenanceKind.NOT_FOUND
        return self


class PaperProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "peerassist.paper_profile.v1"
    paper_id: str
    title: GroundedField = Field(default_factory=GroundedField)
    abstract: GroundedField = Field(default_factory=GroundedField)
    domain: GroundedField = Field(default_factory=GroundedField)
    paper_type: GroundedField = Field(default_factory=GroundedField)
    research_question: GroundedField = Field(default_factory=GroundedField)
    contributions: GroundedField = Field(default_factory=lambda: GroundedField(value=[]))
    section_roles: GroundedField = Field(default_factory=lambda: GroundedField(value=[]))
    method_inputs: GroundedField = Field(default_factory=GroundedField)
    method_outputs: GroundedField = Field(default_factory=GroundedField)
    method_assumptions: GroundedField = Field(default_factory=GroundedField)
    conclusions: GroundedField = Field(default_factory=lambda: GroundedField(value=[]))
    conclusion_boundaries: GroundedField = Field(default_factory=lambda: GroundedField(value=[]))
    parse_warnings: GroundedField = Field(default_factory=lambda: GroundedField(value=[]))

    def grounded_fields(self) -> list[GroundedField]:
        return [
            self.title,
            self.abstract,
            self.domain,
            self.paper_type,
            self.research_question,
            self.contributions,
            self.section_roles,
            self.method_inputs,
            self.method_outputs,
            self.method_assumptions,
            self.conclusions,
            self.conclusion_boundaries,
            self.parse_warnings,
        ]


class ClaimSupportStatus(StrEnum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    CONFLICTING = "conflicting"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ProfileClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    text: str
    claim_type: str
    centrality: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)
    support_evidence_ids: list[str] = Field(default_factory=list)
    support_status: ClaimSupportStatus = ClaimSupportStatus.INSUFFICIENT_EVIDENCE
    conclusion_boundaries: GroundedField = Field(default_factory=lambda: GroundedField(value=[]))
    benign_explanations: GroundedField = Field(default_factory=lambda: GroundedField(value=[]))


class ClaimSupportEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_evidence_id: str
    target_claim_id: str
    relation: str = "supported_by"
    evidence_ids: list[str] = Field(default_factory=list)


class ClaimGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "peerassist.claim_graph.v1"
    paper_id: str
    claims: list[ProfileClaim] = Field(default_factory=list)
    edges: list[ClaimSupportEdge] = Field(default_factory=list)


class ExperimentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    label: GroundedField = Field(default_factory=GroundedField)
    importance_score: float = Field(default=0.5, ge=0, le=1)
    datasets: GroundedField = Field(default_factory=lambda: GroundedField(value=[]))
    sample_sizes: GroundedField = Field(default_factory=lambda: GroundedField(value=[]))
    data_splits: GroundedField = Field(default_factory=lambda: GroundedField(value=[]))
    baselines: GroundedField = Field(default_factory=lambda: GroundedField(value=[]))
    metrics: GroundedField = Field(default_factory=lambda: GroundedField(value=[]))
    random_seeds: GroundedField = Field(default_factory=lambda: GroundedField(value=[]))
    statistics: GroundedField = Field(default_factory=lambda: GroundedField(value=[]))
    ablations: GroundedField = Field(default_factory=lambda: GroundedField(value=[]))
    key_figures: GroundedField = Field(default_factory=lambda: GroundedField(value=[]))
    key_tables: GroundedField = Field(default_factory=lambda: GroundedField(value=[]))

    def grounded_fields(self) -> list[GroundedField]:
        return [
            self.label,
            self.datasets,
            self.sample_sizes,
            self.data_splits,
            self.baselines,
            self.metrics,
            self.random_seeds,
            self.statistics,
            self.ablations,
            self.key_figures,
            self.key_tables,
        ]


class ExperimentInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "peerassist.experiment_inventory.v1"
    paper_id: str
    experiments: list[ExperimentRecord] = Field(default_factory=list)


class ReviewRouteItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int = Field(ge=1)
    priority: str
    reason: str
    claim_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    warning_codes: list[str] = Field(default_factory=list)
    needs_human_review: bool = False


class AgentReviewAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    focus: str
    claim_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class ReviewPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "peerassist.review_plan.v2"
    paper_id: str
    core_claim_ids: list[str] = Field(default_factory=list)
    reading_route: list[ReviewRouteItem] = Field(default_factory=list)
    agent_assignments: list[AgentReviewAssignment] = Field(default_factory=list)
    collapsed_minor_categories: list[str] = Field(
        default_factory=lambda: ["wording", "formatting", "minor_style"]
    )
    collapsed_evidence_ids: list[str] = Field(default_factory=list)


class PaperUnderstandingArtifacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: PaperProfile
    claim_graph: ClaimGraph
    experiment_inventory: ExperimentInventory
    review_plan: ReviewPlan
    artifact_paths: dict[str, str] = Field(default_factory=dict)


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


class FindingImportance(StrEnum):
    CORE = "core"
    SUPPORTING = "supporting"
    MINOR = "minor"


def _normalized_finding_anchor(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", str(value or "").lower()).strip()


def build_finding_identity(
    *,
    producer_namespace: str,
    check_type: str,
    issue_anchor: str,
    affected_claim_ids: list[str] | None = None,
    evidence_ids: list[str] | None = None,
    severity: str = "",
    producer_version: str = "v1",
    issue_semantics: str = "",
) -> tuple[str, str]:
    """Build stable lineage and revision-specific finding identifiers."""

    lineage_payload = {
        "producer_namespace": _normalized_finding_anchor(producer_namespace) or "peerassist",
        "check_type": _normalized_finding_anchor(check_type) or "review_concern",
        "affected_claim_ids": sorted(set(affected_claim_ids or [])),
        "issue_anchor": _normalized_finding_anchor(issue_anchor),
    }
    lineage_digest = hashlib.sha256(
        json.dumps(lineage_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:24]
    lineage_id = f"fln_{lineage_digest}"
    content_payload = {
        "finding_lineage_id": lineage_id,
        "evidence_ids": sorted(set(evidence_ids or [])),
        "issue_semantics": _normalized_finding_anchor(issue_semantics or issue_anchor),
        "severity": _normalized_finding_anchor(severity),
        "producer_version": str(producer_version or "v1"),
    }
    content_digest = hashlib.sha256(
        json.dumps(content_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:24]
    return lineage_id, f"fnd_{content_digest}"


class Concern(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    finding_lineage_id: str = ""
    finding_id: str = ""
    revision: int = Field(default=1, ge=1)
    supersedes: list[str] = Field(default_factory=list)
    reconciles: list[str] = Field(default_factory=list)
    affected_claim_ids: list[str] = Field(default_factory=list)
    importance: FindingImportance = FindingImportance.SUPPORTING
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

    @model_validator(mode="before")
    @classmethod
    def derive_default_importance(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "importance" in value:
            return value
        payload = dict(value)
        level = str(payload.get("level") or "")
        if level == ConcernLevel.MAJOR_CONCERN.value:
            payload["importance"] = FindingImportance.CORE.value
        elif level in {ConcernLevel.MINOR_CONCERN.value, ConcernLevel.EDITOR_NOTE.value}:
            payload["importance"] = FindingImportance.MINOR.value
        return payload

    @model_validator(mode="after")
    def ensure_finding_identity(self) -> Concern:
        if self.finding_lineage_id and self.finding_id:
            return self
        producer_namespace = str(
            self.metadata.get("producer_namespace")
            or (self.source_agent_ids[0] if self.source_agent_ids else "peerassist")
        )
        producer_version = str(self.metadata.get("producer_version") or "v1")
        lineage_id, finding_id = build_finding_identity(
            producer_namespace=producer_namespace,
            check_type=self.category,
            issue_anchor=str(self.metadata.get("issue_anchor") or self.title),
            affected_claim_ids=self.affected_claim_ids,
            evidence_ids=self.evidence_ids,
            severity=self.level.value,
            producer_version=producer_version,
            issue_semantics=" ".join([self.title, self.impact, self.author_action]),
        )
        self.finding_lineage_id = self.finding_lineage_id or lineage_id
        self.finding_id = self.finding_id or finding_id
        return self


class AgentRunStatus(StrEnum):
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"
    FAILED = "failed"


class AgentInputPacket(BaseModel):
    model_config = ConfigDict(extra="ignore")

    agent_id: str
    mode: str
    evidence_ids: list[str] = Field(default_factory=list)
    check_ids: list[str] = Field(default_factory=list)
    capability_names: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentConcernDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    finding_lineage_id: str = ""
    finding_id: str = ""
    revision: int = Field(default=1, ge=1)
    supersedes: list[str] = Field(default_factory=list)
    reconciles: list[str] = Field(default_factory=list)
    affected_claim_ids: list[str] = Field(default_factory=list)
    importance: FindingImportance = FindingImportance.SUPPORTING
    level: ConcernLevel
    category: str
    title: str
    evidence_ids: list[str] = Field(default_factory=list)
    source_check_ids: list[str] = Field(default_factory=list)
    impact: str = ""
    benign_explanation: str = ""
    author_action: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def ensure_finding_identity(self) -> AgentConcernDraft:
        if self.finding_lineage_id and self.finding_id:
            return self
        lineage_id, finding_id = build_finding_identity(
            producer_namespace=str(self.metadata.get("producer_namespace") or "peerassist_agent"),
            check_type=self.category,
            issue_anchor=str(self.metadata.get("issue_anchor") or self.title),
            affected_claim_ids=self.affected_claim_ids,
            evidence_ids=self.evidence_ids,
            severity=self.level.value,
            producer_version=str(self.metadata.get("producer_version") or "v1"),
            issue_semantics=" ".join([self.title, self.impact, self.author_action]),
        )
        self.finding_lineage_id = self.finding_lineage_id or lineage_id
        self.finding_id = self.finding_id or finding_id
        return self


class AgentReviewResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    agent_id: str
    status: AgentRunStatus
    drafts: list[AgentConcernDraft] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanConfirmationAction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    concern_id: str
    finding_lineage_id: str = ""
    finding_id: str = ""
    revision: int | None = Field(default=None, ge=1)
    action: str
    previous_text: str = ""
    new_text: str = ""
    reviewer_id: str = "local-reviewer"
    timestamp: str
    reason: str = ""
    citation_finding_ids: list[str] = Field(default_factory=list)
    audit_version: str = ""
    reconciliation_status: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_utc_timestamp_for_citation_actions(self) -> HumanConfirmationAction:
        if not self.citation_finding_ids:
            return self
        try:
            timestamp = datetime.fromisoformat(self.timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("citation confirmation timestamp must be UTC-aware") from exc
        if timestamp.tzinfo is None or timestamp.utcoffset() is None or timestamp.utcoffset().total_seconds() != 0:
            raise ValueError("citation confirmation timestamp must be UTC-aware")
        return self


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

"""Cross-stage Pydantic contracts.

Every data structure that crosses a module boundary in the project
is defined here. Internal per-module types stay local.
"""

from __future__ import annotations

from schemas.claim import ClaimLabel
from schemas.execution import (
    ExecutionEvidence,
    ExecutionExitStatus,
    ExecutionPayload,
    ExecutionStageStatus,
    RunArtifact,
    Task,
)
from schemas.paper import Figure, Paper, PaperMetadata, Section, Table
from schemas.peerassist import (
    AgentConcernDraft,
    AgentInputPacket,
    AgentReviewResult,
    AgentRunStatus,
    Concern,
    ConcernLevel,
    ConcernStatus,
    DeterministicCheck,
    DeterministicCheckApplicability,
    DeterministicCheckStatus,
    EvidenceItem,
    EvidenceLedger,
    EvidenceType,
    HumanConfirmationAction,
    ToolTraceEvent,
    ToolTraceStatus,
)
from schemas.positioning import LiteratureContext, NeighborMethod, NoveltyType
from schemas.review import ClaimAssessment, EvidenceLink, FinalReview
from schemas.stage import StageResult, StageStatus

__all__ = [
    "AgentConcernDraft",
    "AgentInputPacket",
    "AgentReviewResult",
    "AgentRunStatus",
    "ClaimAssessment",
    "ClaimLabel",
    "Concern",
    "ConcernLevel",
    "ConcernStatus",
    "DeterministicCheck",
    "DeterministicCheckApplicability",
    "DeterministicCheckStatus",
    "EvidenceLink",
    "EvidenceItem",
    "EvidenceLedger",
    "EvidenceType",
    "ExecutionEvidence",
    "ExecutionExitStatus",
    "ExecutionPayload",
    "ExecutionStageStatus",
    "Figure",
    "FinalReview",
    "HumanConfirmationAction",
    "LiteratureContext",
    "NeighborMethod",
    "NoveltyType",
    "Paper",
    "PaperMetadata",
    "RunArtifact",
    "Section",
    "StageResult",
    "StageStatus",
    "Table",
    "Task",
    "ToolTraceEvent",
    "ToolTraceStatus",
]

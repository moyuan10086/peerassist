from __future__ import annotations

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


def test_evidence_ledger_requires_stable_item_ids() -> None:
    ledger = EvidenceLedger(
        paper_id="demo",
        source_sha256="abc",
        items=[
            EvidenceItem(
                id="P01-L001",
                type=EvidenceType.TEXT_SPAN,
                page=1,
                locator="page 1, line 1",
                text="The method uses a fixed seed.",
                source_path="mineru_full.md",
            )
        ],
    )

    assert ledger.items[0].id == "P01-L001"
    assert ledger.coverage["text_span"] == 1


def test_deterministic_check_records_applicability_and_evidence() -> None:
    check = DeterministicCheck(
        id="check_table_percentage_001",
        kind="percentage_consistency",
        applicability=DeterministicCheckApplicability.APPLICABLE,
        status=DeterministicCheckStatus.LEAD,
        evidence_ids=["T2-R4-C3", "T2-R4-C4"],
        message="Reported percentage does not match count/sample size.",
        benign_explanations=["rounding", "different denominator"],
        requires_human_review=True,
    )

    assert check.status is DeterministicCheckStatus.LEAD
    assert check.evidence_ids == ["T2-R4-C3", "T2-R4-C4"]


def test_concern_without_evidence_stays_pending() -> None:
    concern = Concern(
        id="concern_001",
        level=ConcernLevel.CLARIFICATION_NEEDED,
        category="method",
        title="Missing parameter details",
        impact="The method cannot be reproduced.",
        author_action="Please provide the parameter values.",
        status=ConcernStatus.PENDING_HUMAN_CONFIRMATION,
    )

    assert concern.evidence_ids == []
    assert concern.status is ConcernStatus.PENDING_HUMAN_CONFIRMATION


def test_human_confirmation_action_preserves_revision_text() -> None:
    action = HumanConfirmationAction(
        concern_id="concern_001",
        action="rewrite",
        previous_text="old wording",
        new_text="more cautious wording",
        reviewer_id="local-reviewer",
        timestamp="2026-07-10T00:00:00Z",
        reason="Keep the review language restrained.",
    )

    assert action.action == "rewrite"
    assert action.new_text == "more cautious wording"


def test_tool_trace_event_has_required_lifecycle_fields() -> None:
    event = ToolTraceEvent(
        task_id="task",
        call_id="call",
        agent_id="statistics_agent",
        source="builtin",
        tool="percentage_check",
        status=ToolTraceStatus.STARTED,
        ts="2026-07-10T00:00:00Z",
    )

    assert event.status is ToolTraceStatus.STARTED


def test_agent_input_packet_records_scope_and_capabilities() -> None:
    packet = AgentInputPacket(
        agent_id="statistics_agent",
        mode="fast",
        evidence_ids=["P01-L001"],
        check_ids=["check_percentage_consistency_001"],
        capability_names=["percentage_consistency_check"],
    )

    assert packet.agent_id == "statistics_agent"
    assert packet.evidence_ids == ["P01-L001"]
    assert packet.capability_names == ["percentage_consistency_check"]


def test_agent_review_result_preserves_draft_concerns_and_status() -> None:
    draft = AgentConcernDraft(
        id="draft_001",
        level=ConcernLevel.CLARIFICATION_NEEDED,
        category="statistics",
        title="Percentage needs clarification",
        evidence_ids=["P01-L001"],
        source_check_ids=["check_percentage_consistency_001"],
        impact="May affect support for the reported result.",
        benign_explanation="A different denominator may have been used.",
        author_action="Please clarify the denominator.",
    )
    result = AgentReviewResult(
        agent_id="statistics_agent",
        status=AgentRunStatus.COMPLETED,
        drafts=[draft],
        warnings=["review language kept cautious"],
    )

    assert result.status is AgentRunStatus.COMPLETED
    assert result.drafts[0].source_check_ids == ["check_percentage_consistency_001"]
    assert result.warnings == ["review language kept cautious"]

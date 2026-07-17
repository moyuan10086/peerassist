from __future__ import annotations

from peerassist.concerns import concern_from_agent_draft, concerns_from_checks
from peerassist.confirmations import apply_confirmations
from schemas.peerassist import (
    AgentConcernDraft,
    ConcernLevel,
    ConcernStatus,
    DeterministicCheck,
    DeterministicCheckApplicability,
    DeterministicCheckStatus,
    HumanConfirmationAction,
)


def _lead_check() -> DeterministicCheck:
    return DeterministicCheck(
        id="check_percentage_consistency_001",
        kind="percentage_consistency",
        applicability=DeterministicCheckApplicability.APPLICABLE,
        status=DeterministicCheckStatus.LEAD,
        evidence_ids=["P01-L001"],
        message="Reported percentage 40.0% does not match 30/100 = 30.0%.",
        benign_explanations=["rounding", "different denominator"],
    )


def test_deterministic_lead_becomes_pending_concern() -> None:
    concerns = concerns_from_checks([_lead_check()])

    assert len(concerns) == 1
    assert concerns[0].id == "concern_check_percentage_consistency_001"
    assert concerns[0].status is ConcernStatus.PENDING_HUMAN_CONFIRMATION
    assert concerns[0].evidence_ids == ["P01-L001"]
    assert "different denominator" in concerns[0].benign_explanation


def test_apply_confirmations_rewrites_and_deletes_concerns() -> None:
    concerns = concerns_from_checks([_lead_check()])

    rewritten = apply_confirmations(
        concerns,
        [
            HumanConfirmationAction(
                concern_id=concerns[0].id,
                action="rewrite",
                previous_text=concerns[0].author_action,
                new_text="Please clarify the denominator used for this percentage.",
                reviewer_id="reviewer",
                timestamp="2026-07-10T00:00:00Z",
            )
        ],
    )
    assert rewritten[0].status is ConcernStatus.REWRITTEN
    assert rewritten[0].author_action == "Please clarify the denominator used for this percentage."

    deleted = apply_confirmations(
        rewritten,
        [
            HumanConfirmationAction(
                concern_id=concerns[0].id,
                action="delete",
                reviewer_id="reviewer",
                timestamp="2026-07-10T00:00:00Z",
            )
        ],
    )
    assert deleted[0].status is ConcernStatus.DELETED


def test_agent_draft_becomes_pending_concern_with_agent_provenance() -> None:
    concern = concern_from_agent_draft(
        AgentConcernDraft(
            id="check_percentage_consistency_001",
            level=ConcernLevel.CLARIFICATION_NEEDED,
            category="statistics",
            title="Reported percentage needs clarification",
            evidence_ids=["P01-L001"],
            source_check_ids=["check_percentage_consistency_001"],
            impact="This may affect support for the result.",
            benign_explanation="A different denominator may have been used.",
            author_action="Please clarify the denominator.",
        ),
        source_agent_ids=["statistics_agent", "integrator_agent"],
    )

    assert concern.id == "concern_check_percentage_consistency_001"
    assert concern.status is ConcernStatus.PENDING_HUMAN_CONFIRMATION
    assert concern.source_agent_ids == ["statistics_agent", "integrator_agent"]

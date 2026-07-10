from __future__ import annotations

from peerassist.agents import (
    build_agent_input_packet,
    integrate_agent_results,
    run_peerassist_agents,
)
from schemas.peerassist import (
    AgentReviewResult,
    AgentRunStatus,
    ConcernStatus,
    DeterministicCheck,
    DeterministicCheckApplicability,
    DeterministicCheckStatus,
    EvidenceItem,
    EvidenceLedger,
    EvidenceType,
)


def _ledger() -> EvidenceLedger:
    return EvidenceLedger(
        paper_id="demo",
        items=[
            EvidenceItem(
                id="P01-L001",
                type=EvidenceType.TEXT_SPAN,
                page=1,
                locator="p.1 line 1",
                text="The success rate was 30/100 (40%).",
            )
        ],
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


def test_build_agent_input_packet_records_review_scope() -> None:
    packet = build_agent_input_packet(
        agent_id="statistics_agent",
        mode="fast",
        ledger=_ledger(),
        checks=[_lead_check()],
        capability_names=["percentage_consistency_check"],
    )

    assert packet.evidence_ids == ["P01-L001"]
    assert packet.check_ids == ["check_percentage_consistency_001"]
    assert packet.capability_names == ["percentage_consistency_check"]


def test_run_peerassist_agents_emits_statistics_and_defense_results() -> None:
    results = run_peerassist_agents(mode="fast", ledger=_ledger(), checks=[_lead_check()])

    by_agent = {result.agent_id: result for result in results}
    assert by_agent["statistics_agent"].status is AgentRunStatus.COMPLETED
    assert by_agent["statistics_agent"].drafts[0].source_check_ids == [
        "check_percentage_consistency_001"
    ]
    assert by_agent["defense_agent"].status is AgentRunStatus.COMPLETED
    assert "different denominator" in by_agent["defense_agent"].metadata["pressure_tests"][0]
    assert by_agent["integrator_agent"].status is AgentRunStatus.COMPLETED


def test_integrate_agent_results_returns_pending_concerns_with_agent_provenance() -> None:
    results = run_peerassist_agents(mode="fast", ledger=_ledger(), checks=[_lead_check()])

    concerns = integrate_agent_results(results)

    assert len(concerns) == 1
    assert concerns[0].id == "concern_check_percentage_consistency_001"
    assert concerns[0].status is ConcernStatus.PENDING_HUMAN_CONFIRMATION
    assert concerns[0].source_agent_ids == ["statistics_agent", "integrator_agent"]
    assert concerns[0].source_check_ids == ["check_percentage_consistency_001"]


def test_failed_agent_result_becomes_pending_manual_check_concern() -> None:
    concerns = integrate_agent_results(
        [
            AgentReviewResult(
                agent_id="citation_agent",
                status=AgentRunStatus.FAILED,
                warnings=["reference parser unavailable"],
            )
        ]
    )

    assert len(concerns) == 1
    assert concerns[0].category == "manual_check"
    assert concerns[0].status is ConcernStatus.PENDING_HUMAN_CONFIRMATION
    assert concerns[0].source_agent_ids == ["citation_agent"]
    assert "reference parser unavailable" in concerns[0].benign_explanation

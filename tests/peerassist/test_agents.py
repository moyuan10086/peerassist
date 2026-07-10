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


def _citation_lead_check() -> DeterministicCheck:
    return DeterministicCheck(
        id="check_numbered_citation_reference_001",
        kind="numbered_citation_reference",
        applicability=DeterministicCheckApplicability.APPLICABLE,
        status=DeterministicCheckStatus.LEAD,
        evidence_ids=["P01-L001"],
        message="Numbered citation [2] was not found in parsed references.",
        benign_explanations=["parser missed a reference entry"],
    )


def _figure_table_lead_check() -> DeterministicCheck:
    return DeterministicCheck(
        id="check_figure_table_reference_001",
        kind="figure_table_reference",
        applicability=DeterministicCheckApplicability.APPLICABLE,
        status=DeterministicCheckStatus.LEAD,
        evidence_ids=["P01-L001"],
        message="Referenced Figure 2 was not found in parsed figure/table evidence.",
        benign_explanations=["parser missed a caption"],
    )


def _significance_lead_check() -> DeterministicCheck:
    return DeterministicCheck(
        id="check_significance_star_consistency_001",
        kind="significance_star_consistency",
        applicability=DeterministicCheckApplicability.APPLICABLE,
        status=DeterministicCheckStatus.LEAD,
        evidence_ids=["P01-L001"],
        message="Significance stars do not match exact p-values under the paper's own legend.",
        benign_explanations=["table transcription issue", "p-value was rounded"],
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


def test_fast_mode_figure_table_agent_reviews_figure_table_leads() -> None:
    results = run_peerassist_agents(mode="fast", ledger=_ledger(), checks=[_figure_table_lead_check()])

    by_agent = {result.agent_id: result for result in results}
    assert by_agent["figure_table_agent"].status is AgentRunStatus.COMPLETED
    assert by_agent["figure_table_agent"].drafts[0].category == "figure_table"
    assert by_agent["statistics_agent"].drafts == []


def test_statistics_agent_reviews_significance_star_leads() -> None:
    results = run_peerassist_agents(mode="fast", ledger=_ledger(), checks=[_significance_lead_check()])

    by_agent = {result.agent_id: result for result in results}
    assert by_agent["statistics_agent"].drafts[0].category == "statistics"
    assert by_agent["statistics_agent"].drafts[0].source_check_ids == [
        "check_significance_star_consistency_001"
    ]
    assert by_agent["figure_table_agent"].drafts == []


def test_standard_mode_citation_agent_reviews_citation_reference_leads() -> None:
    results = run_peerassist_agents(mode="standard", ledger=_ledger(), checks=[_citation_lead_check()])

    by_agent = {result.agent_id: result for result in results}
    assert by_agent["citation_agent"].status is AgentRunStatus.COMPLETED
    assert by_agent["citation_agent"].drafts[0].category == "citation"
    assert by_agent["citation_agent"].drafts[0].source_check_ids == [
        "check_numbered_citation_reference_001"
    ]
    assert by_agent["citation_agent"].warnings == []


def test_integrate_agent_results_returns_pending_concerns_with_agent_provenance() -> None:
    results = run_peerassist_agents(mode="fast", ledger=_ledger(), checks=[_lead_check()])

    concerns = integrate_agent_results(results)

    assert len(concerns) == 1
    assert concerns[0].id == "concern_check_percentage_consistency_001"
    assert concerns[0].status is ConcernStatus.PENDING_HUMAN_CONFIRMATION
    assert concerns[0].source_agent_ids == ["statistics_agent", "integrator_agent"]
    assert concerns[0].source_check_ids == ["check_percentage_consistency_001"]


def test_integrated_citation_concern_records_citation_agent_provenance() -> None:
    concerns = integrate_agent_results(
        run_peerassist_agents(mode="standard", ledger=_ledger(), checks=[_citation_lead_check()])
    )

    citation = next(concern for concern in concerns if concern.category == "citation")
    assert citation.source_agent_ids == ["citation_agent"]


def test_integrated_figure_table_concern_records_figure_table_agent_provenance() -> None:
    concerns = integrate_agent_results(
        run_peerassist_agents(mode="fast", ledger=_ledger(), checks=[_figure_table_lead_check()])
    )

    figure_table = next(concern for concern in concerns if concern.category == "figure_table")
    assert figure_table.source_agent_ids == ["figure_table_agent"]


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

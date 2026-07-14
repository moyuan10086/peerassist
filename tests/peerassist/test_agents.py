from __future__ import annotations

import pytest

from peerassist.agents import (
    build_agent_input_packet,
    integrate_agent_results,
    run_peerassist_agents,
)
from schemas.citation import CitationAudit
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
    PaperUnderstandingArtifacts,
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


def _citation_audit() -> CitationAudit:
    return CitationAudit.model_validate(
        {
            "schema_version": "peerassist.citation_audit.v1",
            "paper_id": "demo",
            "parse_version": "parser-1",
            "findings": [
                {
                    "id": "F-missing-001",
                    "status": "missing_reference",
                    "severity": "clarification_needed",
                    "citation_link_ids": ["link-1"],
                    "reference_record_ids": [],
                    "mention_evidence_ids": ["P01-L001"],
                    "reference_evidence_ids": [],
                    "verification_ids": [],
                    "message": "Citation is not linked.",
                    "requires_human_review": True,
                }
            ],
        }
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


def _understanding() -> PaperUnderstandingArtifacts:
    return PaperUnderstandingArtifacts.model_validate(
        {
            "profile": {
                "paper_id": "demo",
                "title": {
                    "value": "Evidence-Grounded Review",
                    "evidence_ids": ["P01-L001"],
                    "provenance": "reported",
                },
                "method_assumptions": {"value": None},
            },
            "claim_graph": {"paper_id": "demo", "claims": [], "edges": []},
            "experiment_inventory": {"paper_id": "demo", "experiments": []},
            "review_plan": {
                "paper_id": "demo",
                "agent_assignments": [
                    {
                        "agent_id": "method",
                        "focus": "method assumptions",
                        "evidence_ids": ["P01-L001"],
                    }
                ],
            },
        }
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


def test_fast_mode_runs_all_professional_specialists_with_scoped_metadata() -> None:
    results = run_peerassist_agents(
        mode="fast",
        ledger=_ledger(),
        checks=[],
        understanding=_understanding(),
    )

    by_agent = {result.agent_id: result for result in results}
    for agent_id in (
        "structure_agent",
        "methodology_agent",
        "experiment_agent",
        "statistics_agent",
        "citation_agent",
        "ethics_agent",
        "reproducibility_agent",
    ):
        assert by_agent[agent_id].status is AgentRunStatus.COMPLETED
        assert by_agent[agent_id].metadata["responsibility"]
        assert by_agent[agent_id].metadata["evidence_count"] >= 0


def test_methodology_gap_is_grounded_in_assigned_evidence() -> None:
    results = run_peerassist_agents(
        mode="fast",
        ledger=_ledger(),
        checks=[],
        understanding=_understanding(),
    )

    methodology = next(result for result in results if result.agent_id == "methodology_agent")
    assert methodology.drafts
    assert methodology.drafts[0].category == "methodology"
    assert methodology.drafts[0].evidence_ids == ["P01-L001"]


def test_model_enhancement_rejects_concerns_with_unselected_evidence() -> None:
    def enhance(_context: dict) -> dict:
        return {
            "concerns": [
                {
                    "agent_id": "structure_agent",
                    "level": "major_concern",
                    "category": "structure",
                    "title": "Grounded finding",
                    "evidence_ids": ["P01-L001"],
                    "impact": "The central argument may be hard to evaluate.",
                    "benign_explanation": "The framing may be distributed across sections.",
                    "author_action": "State the central question explicitly.",
                },
                {
                    "agent_id": "methodology_agent",
                    "level": "major_concern",
                    "category": "methodology",
                    "title": "Ungrounded finding",
                    "evidence_ids": ["NOT-IN-CONTEXT"],
                    "impact": "Unsupported.",
                    "benign_explanation": "None.",
                    "author_action": "Do something.",
                },
            ],
            "usage": {"input_tokens": 120, "output_tokens": 40, "total_tokens": 160},
        }

    results = run_peerassist_agents(
        mode="fast",
        ledger=_ledger(),
        checks=[],
        understanding=_understanding(),
        model_enhancer=enhance,
    )

    by_agent = {result.agent_id: result for result in results}
    assert any(draft.title == "Grounded finding" for draft in by_agent["structure_agent"].drafts)
    assert not any(
        draft.title == "Ungrounded finding"
        for result in results
        for draft in result.drafts
    )
    assert by_agent["structure_agent"].metadata["model_usage"]["total_tokens"] == 160


@pytest.mark.parametrize("mode", ["standard", "deep"])
def test_citation_agent_is_observable_but_does_not_generate_drafts(mode: str) -> None:
    results = run_peerassist_agents(mode=mode, ledger=_ledger(), checks=[_citation_lead_check()])

    by_agent = {result.agent_id: result for result in results}
    assert by_agent["citation_agent"].status is AgentRunStatus.COMPLETED
    assert by_agent["citation_agent"].drafts == []
    assert by_agent["citation_agent"].metadata["source"] == "citation_audit"


def test_integrate_agent_results_returns_pending_concerns_with_agent_provenance() -> None:
    results = run_peerassist_agents(mode="fast", ledger=_ledger(), checks=[_lead_check()])

    concerns = integrate_agent_results(results)

    assert len(concerns) == 1
    assert concerns[0].id == "concern_check_percentage_consistency_001"
    assert concerns[0].status is ConcernStatus.PENDING_HUMAN_CONFIRMATION
    assert concerns[0].source_agent_ids == ["statistics_agent", "integrator_agent"]
    assert concerns[0].source_check_ids == ["check_percentage_consistency_001"]


def test_integrated_results_do_not_create_a_second_citation_concern() -> None:
    concerns = integrate_agent_results(
        run_peerassist_agents(mode="standard", ledger=_ledger(), checks=[_citation_lead_check()])
    )

    assert not any(concern.category == "citation" for concern in concerns)


@pytest.mark.parametrize(
    ("mode", "source_agent_id"),
    [("fast", "citation_audit"), ("standard", "citation_agent"), ("deep", "citation_agent")],
)
def test_audit_is_the_single_citation_concern_path_for_each_mode(
    mode: str, source_agent_id: str
) -> None:
    concerns = integrate_agent_results(
        run_peerassist_agents(mode=mode, ledger=_ledger(), checks=[_citation_lead_check()]),
        citation_audit=_citation_audit(),
        mode=mode,
    )

    citation_concerns = [concern for concern in concerns if concern.category == "citation"]
    assert len(citation_concerns) == 1
    assert citation_concerns[0].source_agent_ids == [source_agent_id]


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

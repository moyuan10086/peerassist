"""Deterministic local PeerAssist agent runner.

The first runner is intentionally local and reproducible. It emits the same
typed artifacts an LLM/MCP-backed runner will later produce, so downstream
confirmation and export code can remain stable.
"""

from __future__ import annotations

from collections import defaultdict

from peerassist.concerns import concern_from_agent_draft
from schemas.peerassist import (
    AgentConcernDraft,
    AgentInputPacket,
    AgentReviewResult,
    AgentRunStatus,
    Concern,
    ConcernLevel,
    ConcernStatus,
    DeterministicCheck,
    DeterministicCheckStatus,
    EvidenceLedger,
)

FAST_AGENT_IDS = ("statistics_agent", "figure_table_agent", "defense_agent", "integrator_agent")
STANDARD_EXTRA_AGENT_IDS = ("citation_agent", "novelty_agent")
DEEP_EXTRA_AGENT_IDS = (*STANDARD_EXTRA_AGENT_IDS, "methodology_agent", "reproducibility_agent")


def build_agent_input_packet(
    *,
    agent_id: str,
    mode: str,
    ledger: EvidenceLedger,
    checks: list[DeterministicCheck],
    capability_names: list[str] | None = None,
) -> AgentInputPacket:
    evidence_ids = [item.id for item in ledger.items]
    check_ids = [check.id for check in checks]
    return AgentInputPacket(
        agent_id=agent_id,
        mode=mode,
        evidence_ids=evidence_ids,
        check_ids=check_ids,
        capability_names=list(capability_names or []),
        metadata={"paper_id": ledger.paper_id},
    )


def _draft_for_check(check: DeterministicCheck) -> AgentConcernDraft:
    return AgentConcernDraft(
        id=check.id,
        level=ConcernLevel.CLARIFICATION_NEEDED,
        category=_category_for_check_kind(check.kind),
        title=_title_for_check(check),
        evidence_ids=list(check.evidence_ids),
        source_check_ids=[check.id],
        impact="This may affect whether the reported result supports the paper's claim.",
        benign_explanation="; ".join(check.benign_explanations),
        author_action=(
            "Please clarify the calculation basis and provide enough detail for readers to reproduce it."
        ),
        metadata={"check_message": check.message},
    )


def _category_for_check_kind(kind: str) -> str:
    if "percentage" in kind or "stat" in kind or "mean" in kind:
        return "statistics"
    if "figure" in kind or "table" in kind:
        return "figure_table"
    if "reference" in kind or "citation" in kind:
        return "citation"
    return "other"


def _title_for_check(check: DeterministicCheck) -> str:
    if check.kind == "percentage_consistency":
        return "Reported percentage needs clarification"
    return check.kind.replace("_", " ").title()


def _lead_checks(checks: list[DeterministicCheck]) -> list[DeterministicCheck]:
    return [check for check in checks if check.status is DeterministicCheckStatus.LEAD]


def _lead_checks_for_category(checks: list[DeterministicCheck], category: str) -> list[DeterministicCheck]:
    return [
        check
        for check in _lead_checks(checks)
        if _category_for_check_kind(check.kind) == category
    ]


def _statistics_agent(checks: list[DeterministicCheck]) -> AgentReviewResult:
    drafts = [_draft_for_check(check) for check in _lead_checks_for_category(checks, "statistics")]
    return AgentReviewResult(
        agent_id="statistics_agent",
        status=AgentRunStatus.COMPLETED,
        drafts=drafts,
        metadata={"lead_count": len(drafts)},
    )


def _figure_table_agent(checks: list[DeterministicCheck]) -> AgentReviewResult:
    drafts = [_draft_for_check(check) for check in _lead_checks_for_category(checks, "figure_table")]
    return AgentReviewResult(
        agent_id="figure_table_agent",
        status=AgentRunStatus.COMPLETED,
        drafts=drafts,
        warnings=[] if drafts else ["No figure/table deterministic leads found."],
        metadata={"lead_count": len(drafts)},
    )


def _defense_agent(checks: list[DeterministicCheck]) -> AgentReviewResult:
    pressure_tests: list[str] = []
    for check in _lead_checks(checks):
        explanations = "; ".join(check.benign_explanations) or "no benign explanation recorded"
        pressure_tests.append(f"{check.id}: pressure-test against {explanations}")
    return AgentReviewResult(
        agent_id="defense_agent",
        status=AgentRunStatus.COMPLETED,
        warnings=[] if pressure_tests else ["No deterministic leads to pressure-test."],
        metadata={"pressure_tests": pressure_tests},
    )


def _integrator_agent(statistics_result: AgentReviewResult) -> AgentReviewResult:
    return AgentReviewResult(
        agent_id="integrator_agent",
        status=AgentRunStatus.COMPLETED,
        drafts=[draft.model_copy(deep=True) for draft in statistics_result.drafts],
        metadata={"integrated_from": ["statistics_agent", "defense_agent"]},
    )


def _citation_agent(checks: list[DeterministicCheck]) -> AgentReviewResult:
    drafts = [_draft_for_check(check) for check in _lead_checks_for_category(checks, "citation")]
    return AgentReviewResult(
        agent_id="citation_agent",
        status=AgentRunStatus.COMPLETED,
        drafts=drafts,
        warnings=[] if drafts else ["No citation/reference deterministic leads found."],
        metadata={"lead_count": len(drafts)},
    )


def _unsupported_agent(agent_id: str, mode: str) -> AgentReviewResult:
    return AgentReviewResult(
        agent_id=agent_id,
        status=AgentRunStatus.INCOMPLETE,
        warnings=[f"{agent_id} is not implemented in the local {mode} runner."],
    )


def run_peerassist_agents(
    *,
    mode: str,
    ledger: EvidenceLedger,
    checks: list[DeterministicCheck],
    capability_names: list[str] | None = None,
) -> list[AgentReviewResult]:
    normalized_mode = str(mode or "fast").strip().lower()
    for agent_id in FAST_AGENT_IDS:
        build_agent_input_packet(
            agent_id=agent_id,
            mode=normalized_mode,
            ledger=ledger,
            checks=checks,
            capability_names=capability_names,
        )

    statistics_result = _statistics_agent(checks)
    results = [
        statistics_result,
        _figure_table_agent(checks),
        _defense_agent(checks),
        _integrator_agent(statistics_result),
    ]
    if normalized_mode == "standard":
        results.extend(
            [
                _citation_agent(checks),
                _unsupported_agent("novelty_agent", normalized_mode),
            ]
        )
    elif normalized_mode == "deep":
        results.extend(
            [
                _citation_agent(checks),
                _unsupported_agent("novelty_agent", normalized_mode),
                _unsupported_agent("methodology_agent", normalized_mode),
                _unsupported_agent("reproducibility_agent", normalized_mode),
            ]
        )
    return results


def integrate_agent_results(results: list[AgentReviewResult]) -> list[Concern]:
    draft_sources: dict[str, list[str]] = defaultdict(list)
    drafts_by_id: dict[str, AgentConcernDraft] = {}
    concerns: list[Concern] = []

    for result in results:
        if result.status in {AgentRunStatus.FAILED, AgentRunStatus.INCOMPLETE}:
            concerns.append(_manual_check_concern(result))
            continue
        for draft in result.drafts:
            drafts_by_id.setdefault(draft.id, draft)
            if result.agent_id not in draft_sources[draft.id]:
                draft_sources[draft.id].append(result.agent_id)

    for draft_id, draft in drafts_by_id.items():
        concerns.append(concern_from_agent_draft(draft, source_agent_ids=draft_sources[draft_id]))
    return concerns


def _manual_check_concern(result: AgentReviewResult) -> Concern:
    warning_text = "; ".join(result.warnings) or f"{result.agent_id} did not complete."
    return Concern(
        id=f"concern_manual_check_{result.agent_id}",
        level=ConcernLevel.EDITOR_NOTE,
        category="manual_check",
        title=f"{result.agent_id} requires manual follow-up",
        impact="A planned PeerAssist review agent did not complete its local check.",
        benign_explanation=warning_text,
        author_action="Please review this check manually before relying on the report.",
        status=ConcernStatus.PENDING_HUMAN_CONFIRMATION,
        source_agent_ids=[result.agent_id],
        metadata={"agent_status": result.status.value},
    )

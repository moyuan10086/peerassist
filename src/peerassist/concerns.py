"""Convert deterministic leads into PeerAssist review concerns."""

from __future__ import annotations

from schemas.peerassist import (
    AgentConcernDraft,
    Concern,
    ConcernLevel,
    ConcernStatus,
    DeterministicCheck,
    DeterministicCheckStatus,
)


def _category_for_check(kind: str) -> str:
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


def concerns_from_checks(checks: list[DeterministicCheck]) -> list[Concern]:
    concerns: list[Concern] = []
    for check in checks:
        if check.status is not DeterministicCheckStatus.LEAD:
            continue
        concerns.append(
            Concern(
                id=f"concern_{check.id}",
                level=ConcernLevel.CLARIFICATION_NEEDED,
                category=_category_for_check(check.kind),
                title=_title_for_check(check),
                evidence_ids=list(check.evidence_ids),
                impact="This may affect whether the reported result supports the paper's claim.",
                benign_explanation="; ".join(check.benign_explanations),
                author_action="Please clarify the calculation basis and provide enough detail for readers to reproduce it.",
                status=ConcernStatus.PENDING_HUMAN_CONFIRMATION,
                source_check_ids=[check.id],
            )
        )
    return concerns


def concern_from_agent_draft(
    draft: AgentConcernDraft, *, source_agent_ids: list[str] | None = None
) -> Concern:
    concern_id = draft.id if draft.id.startswith("concern_") else f"concern_{draft.id}"
    return Concern(
        id=concern_id,
        level=draft.level,
        category=draft.category,
        title=draft.title,
        evidence_ids=list(draft.evidence_ids),
        impact=draft.impact,
        benign_explanation=draft.benign_explanation,
        author_action=draft.author_action,
        status=ConcernStatus.PENDING_HUMAN_CONFIRMATION,
        source_agent_ids=list(source_agent_ids or []),
        source_check_ids=list(draft.source_check_ids),
        metadata=dict(draft.metadata),
    )

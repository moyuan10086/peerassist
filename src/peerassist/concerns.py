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

_DISPLAY_FIELDS = ("title", "impact", "benign_explanation", "author_action")


def reconcile_finding_revisions(
    previous_concerns: list[Concern],
    candidate_concerns: list[Concern],
) -> list[Concern]:
    """Carry stable finding lineage history into a regenerated candidate set."""

    latest_by_lineage: dict[str, Concern] = {}
    previous_by_id = {concern.id: concern for concern in previous_concerns}
    for concern in previous_concerns:
        previous = latest_by_lineage.get(concern.finding_lineage_id)
        if previous is None or (concern.revision, concern.display_revision) > (
            previous.revision,
            previous.display_revision,
        ):
            latest_by_lineage[concern.finding_lineage_id] = concern

    reconciled: list[Concern] = []
    for candidate in candidate_concerns:
        previous = latest_by_lineage.get(candidate.finding_lineage_id)
        if previous is None:
            replaced = previous_by_id.get(candidate.id)
            updated = candidate.model_copy(deep=True)
            if replaced is not None and replaced.finding_lineage_id != candidate.finding_lineage_id:
                updated.supersedes = list(
                    dict.fromkeys([*replaced.supersedes, replaced.finding_id])
                )
                updated.metadata = dict(updated.metadata)
                updated.metadata["lineage_replaced_from"] = replaced.finding_lineage_id
            reconciled.append(updated)
            latest_by_lineage[updated.finding_lineage_id] = updated
            continue
        updated = candidate.model_copy(deep=True)
        if candidate.finding_id == previous.finding_id:
            updated.revision = previous.revision
            updated.supersedes = list(previous.supersedes)
            updated.reconciles = list(previous.reconciles)
            display_changed = any(
                getattr(candidate, field) != getattr(previous, field) for field in _DISPLAY_FIELDS
            )
            updated.display_revision = previous.display_revision + int(display_changed)
            if display_changed:
                updated.metadata = dict(updated.metadata)
                updated.metadata["display_revision_from"] = previous.display_revision
        else:
            updated.revision = previous.revision + 1
            updated.display_revision = 1
            updated.supersedes = list(
                dict.fromkeys([*previous.supersedes, previous.finding_id])
            )
            updated.metadata = dict(updated.metadata)
            updated.metadata["reconciled_from_revision"] = previous.revision
        reconciled.append(updated)
        latest_by_lineage[updated.finding_lineage_id] = updated
    return reconciled


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
                metadata={
                    "producer_namespace": "deterministic_checks",
                    "producer_version": "v1",
                    "issue_anchor": check.id,
                    "finding_check_type": check.kind,
                    "finding_semantic_key": check.kind,
                },
            )
        )
    return concerns


def concern_from_agent_draft(
    draft: AgentConcernDraft, *, source_agent_ids: list[str] | None = None
) -> Concern:
    concern_id = draft.id if draft.id.startswith("concern_") else f"concern_{draft.id}"
    return Concern(
        id=concern_id,
        finding_lineage_id=draft.finding_lineage_id,
        finding_id=draft.finding_id,
        revision=draft.revision,
        display_revision=draft.display_revision,
        supersedes=list(draft.supersedes),
        reconciles=list(draft.reconciles),
        affected_claim_ids=list(draft.affected_claim_ids),
        importance=draft.importance,
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

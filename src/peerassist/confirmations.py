"""Apply human confirmation actions to PeerAssist concerns."""

from __future__ import annotations

from schemas.peerassist import Concern, ConcernLevel, ConcernStatus, HumanConfirmationAction


def _apply_action(concern: Concern, action: HumanConfirmationAction) -> Concern:
    updated = concern.model_copy(deep=True)
    normalized = action.action.strip().lower()
    if normalized == "confirm":
        updated.status = ConcernStatus.CONFIRMED
    elif normalized == "rewrite":
        updated.status = ConcernStatus.REWRITTEN
        if action.new_text.strip():
            updated.author_action = action.new_text.strip()
    elif normalized == "downgrade":
        updated.status = ConcernStatus.DOWNGRADED
        if updated.level is ConcernLevel.MAJOR_CONCERN:
            updated.level = ConcernLevel.MINOR_CONCERN
        if action.new_text.strip():
            updated.author_action = action.new_text.strip()
    elif normalized == "delete":
        updated.status = ConcernStatus.DELETED
    elif normalized == "mark_pending":
        updated.status = ConcernStatus.PENDING_HUMAN_CONFIRMATION
    else:
        updated.metadata = dict(updated.metadata)
        updated.metadata.setdefault("unsupported_confirmation_actions", []).append(normalized)
    updated.metadata = dict(updated.metadata)
    updated.metadata["last_confirmation_action"] = action.model_dump(mode="json")
    return updated


def apply_confirmations(
    concerns: list[Concern],
    actions: list[HumanConfirmationAction],
) -> list[Concern]:
    actions_by_concern: dict[str, list[HumanConfirmationAction]] = {}
    for action in actions:
        actions_by_concern.setdefault(action.concern_id, []).append(action)

    result: list[Concern] = []
    for concern in concerns:
        updated = concern
        for action in actions_by_concern.get(concern.id, []):
            updated = _apply_action(updated, action)
        result.append(updated)
    return result

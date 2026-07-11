from __future__ import annotations

from copy import deepcopy

import pytest

from peerassist.confirmations import reconcile_citation_confirmations
from schemas.citation import CitationAudit
from schemas.peerassist import Concern, ConcernLevel, HumanConfirmationAction


def _audit(*, parse_version: str = "parser-1", finding_id: str = "F-1") -> CitationAudit:
    return CitationAudit.model_validate(
        {
            "schema_version": "peerassist.citation_audit.v1",
            "paper_id": "paper-1",
            "parse_version": parse_version,
            "findings": [
                {
                    "id": finding_id,
                    "status": "missing_reference",
                    "severity": "clarification_needed",
                    "citation_link_ids": ["link-1"],
                    "reference_record_ids": [],
                    "mention_evidence_ids": ["mention-1"],
                    "reference_evidence_ids": [],
                    "verification_ids": [],
                    "message": "Citation needs clarification.",
                    "requires_human_review": True,
                }
            ],
        }
    )


def _concern() -> Concern:
    return Concern(
        id="concern_citation_F-1",
        level=ConcernLevel.CLARIFICATION_NEEDED,
        category="citation",
        title="引用关联需要核对",
        evidence_ids=["mention-1"],
        metadata={"citation_finding_ids": ["F-1"], "audit_schema_version": "peerassist.citation_audit.v1", "audit_parse_version": "parser-1"},
    )


def _action() -> HumanConfirmationAction:
    return HumanConfirmationAction(
        concern_id="concern_citation_F-1",
        action="rewrite",
        previous_text="旧文本",
        new_text="请核对引用对应的参考文献。",
        reviewer_id="reviewer-1",
        timestamp="2026-07-11T08:00:00+00:00",
        reason="措辞更准确",
        citation_finding_ids=["F-1"],
        audit_version="peerassist.citation_audit.v1:parser-1",
        metadata={"source": "test"},
    )


def test_unchanged_citation_action_replays_without_mutating_history() -> None:
    action = _action()
    original = deepcopy(action.model_dump(mode="json"))

    result = reconcile_citation_confirmations([_concern()], [action], _audit())

    assert result.replayable_actions == [action]
    assert result.unresolved_historical_actions == []
    assert result.concern_ids_needing_reconciliation == []
    assert action.model_dump(mode="json") == original


@pytest.mark.parametrize("audit", [_audit(finding_id="F-2"), _audit(parse_version="parser-2")])
def test_vanished_or_changed_audit_requires_reconciliation(audit: CitationAudit) -> None:
    action = _action()
    original = deepcopy(action.model_dump(mode="json"))

    result = reconcile_citation_confirmations([_concern()], [action], audit)

    assert result.replayable_actions == []
    assert result.unresolved_historical_actions == [action]
    assert result.concern_ids_needing_reconciliation == ["concern_citation_F-1"]
    assert action.model_dump(mode="json") == original


def test_legacy_action_stays_compatible_and_citation_actions_require_utc_time() -> None:
    legacy = HumanConfirmationAction(
        concern_id="concern_citation_F-1", action="confirm", timestamp="2026-07-11T08:00:00"
    )
    assert legacy.citation_finding_ids == []
    assert reconcile_citation_confirmations([_concern()], [legacy], _audit()).replayable_actions == [legacy]

    with pytest.raises(ValueError, match="UTC-aware"):
        HumanConfirmationAction(
            concern_id="concern_citation_F-1",
            action="confirm",
            timestamp="2026-07-11T08:00:00+08:00",
            citation_finding_ids=["F-1"],
        )

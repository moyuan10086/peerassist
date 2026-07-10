from __future__ import annotations

from peerassist.confirmations import (
    build_confirmation_bundle,
    build_confirmation_review_queue,
    confirmation_action_from_queue_decision,
)
from schemas.peerassist import Concern, ConcernLevel, ConcernStatus


def test_build_confirmation_bundle_groups_concerns_and_adds_evidence_locators() -> None:
    bundle = build_confirmation_bundle(
        concerns=[
            Concern(
                id="concern_pending_001",
                level=ConcernLevel.CLARIFICATION_NEEDED,
                category="statistics",
                title="Reported percentage needs clarification",
                evidence_ids=["P01-L001"],
                impact="May affect support for the result.",
                benign_explanation="A different denominator may have been used.",
                author_action="Please clarify the denominator.",
                status=ConcernStatus.PENDING_HUMAN_CONFIRMATION,
                source_agent_ids=["statistics_agent", "integrator_agent"],
            ),
            Concern(
                id="concern_confirmed_001",
                level=ConcernLevel.MINOR_CONCERN,
                category="citation",
                title="Citation locator needs checking",
                status=ConcernStatus.CONFIRMED,
            ),
        ],
        evidence_lookup={"P01-L001": "p.1 line 1"},
    )

    assert bundle["schema_version"] == "peerassist.confirmation_bundle.v1"
    assert bundle["counts_by_status"]["pending_human_confirmation"] == 1
    pending = bundle["groups"]["pending_human_confirmation"][0]
    assert pending["id"] == "concern_pending_001"
    assert pending["evidence"][0] == {"id": "P01-L001", "locator": "p.1 line 1"}
    assert pending["source_agent_ids"] == ["statistics_agent", "integrator_agent"]
    assert bundle["groups"]["confirmed"][0]["id"] == "concern_confirmed_001"


def test_build_confirmation_review_queue_prioritizes_pending_items() -> None:
    bundle = build_confirmation_bundle(
        concerns=[
            Concern(
                id="concern_confirmed_001",
                level=ConcernLevel.MINOR_CONCERN,
                category="citation",
                title="Citation locator needs checking",
                status=ConcernStatus.CONFIRMED,
            ),
            Concern(
                id="concern_pending_001",
                level=ConcernLevel.CLARIFICATION_NEEDED,
                category="statistics",
                title="Reported percentage needs clarification",
                evidence_ids=["P01-L001"],
                status=ConcernStatus.PENDING_HUMAN_CONFIRMATION,
                source_agent_ids=["statistics_agent"],
            ),
        ],
        evidence_lookup={"P01-L001": "p.1 line 1"},
    )

    queue = build_confirmation_review_queue(bundle)

    assert queue["schema_version"] == "peerassist.confirmation_review_queue.v1"
    assert [item["id"] for item in queue["items"]] == [
        "concern_pending_001",
        "concern_confirmed_001",
    ]
    first = queue["items"][0]
    assert first["position"] == 1
    assert first["allowed_actions"] == [
        "confirm",
        "rewrite",
        "downgrade",
        "delete",
        "mark_pending",
    ]
    assert first["evidence"][0] == {"id": "P01-L001", "locator": "p.1 line 1"}
    assert first["source_agent_ids"] == ["statistics_agent"]


def test_confirmation_action_from_queue_decision_is_human_confirmation_compatible() -> None:
    action = confirmation_action_from_queue_decision(
        concern_id="concern_pending_001",
        action="rewrite",
        reviewer_id="reviewer-1",
        timestamp="2026-07-10T00:00:00Z",
        previous_text="Please clarify the calculation basis.",
        new_text="Please clarify the denominator used for this percentage.",
        reason="More precise wording.",
    )

    assert action.concern_id == "concern_pending_001"
    assert action.action == "rewrite"
    assert action.previous_text == "Please clarify the calculation basis."
    assert action.new_text == "Please clarify the denominator used for this percentage."
    assert action.reason == "More precise wording."

from __future__ import annotations

from peerassist.confirmations import build_confirmation_bundle
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

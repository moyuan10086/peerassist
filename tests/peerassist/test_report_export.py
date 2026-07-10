from __future__ import annotations

import pytest

from peerassist.confirmations import apply_confirmations
from peerassist.concerns import concerns_from_checks
from peerassist.report_export import export_peerassist_report
from schemas.peerassist import (
    Concern,
    ConcernLevel,
    ConcernStatus,
    DeterministicCheck,
    DeterministicCheckApplicability,
    DeterministicCheckStatus,
    HumanConfirmationAction,
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


def test_export_includes_confirmed_rewritten_concerns_and_pending_appendix() -> None:
    concerns = concerns_from_checks([_lead_check()])
    confirmed = apply_confirmations(
        concerns,
        [
            HumanConfirmationAction(
                concern_id=concerns[0].id,
                action="rewrite",
                previous_text=concerns[0].author_action,
                new_text="Please clarify the denominator used for this percentage.",
                reviewer_id="reviewer",
                timestamp="2026-07-10T00:00:00Z",
            )
        ],
    )
    pending = Concern(
        id="concern_manual_001",
        level=ConcernLevel.CLARIFICATION_NEEDED,
        category="method",
        title="Needs manual method check",
        impact="May affect reproducibility.",
        author_action="Please verify manually.",
        status=ConcernStatus.PENDING_HUMAN_CONFIRMATION,
    )

    markdown, payload = export_peerassist_report(
        paper_id="demo",
        concerns=[*confirmed, pending],
        evidence_lookup={"P01-L001": "page 1, line 1"},
    )

    assert "Please clarify the denominator used for this percentage." in markdown
    assert "Needs manual method check" in markdown
    assert "## Pending Manual Checks" in markdown
    assert payload["confirmed_count"] == 1
    assert payload["pending_count"] == 1
    assert payload["language"] == "en"


def test_export_supports_chinese_report_template_without_rewriting_evidence() -> None:
    concerns = concerns_from_checks([_lead_check()])

    markdown, payload = export_peerassist_report(
        paper_id="demo",
        concerns=concerns,
        evidence_lookup={"P01-L001": "第 1 页，第 1 行"},
        language="zh",
    )

    assert "# PeerAssist 论文审核辅助报告" in markdown
    assert "## 待人工确认的检查项" in markdown
    assert "证据：" in markdown
    assert "第 1 页，第 1 行" in markdown
    assert "Reported percentage needs clarification" in markdown
    assert payload["language"] == "zh"


def test_export_omits_deleted_concerns() -> None:
    concerns = concerns_from_checks([_lead_check()])
    deleted = apply_confirmations(
        concerns,
        [
            HumanConfirmationAction(
                concern_id=concerns[0].id,
                action="delete",
                reviewer_id="reviewer",
                timestamp="2026-07-10T00:00:00Z",
            )
        ],
    )

    markdown, payload = export_peerassist_report(
        paper_id="demo",
        concerns=deleted,
        evidence_lookup={"P01-L001": "page 1, line 1"},
    )

    assert "Reported percentage" not in markdown
    assert payload["confirmed_count"] == 0


def test_export_rejects_confirmed_concern_without_evidence() -> None:
    concern = Concern(
        id="concern_no_evidence_001",
        level=ConcernLevel.CLARIFICATION_NEEDED,
        category="statistics",
        title="Needs evidence before becoming a review concern",
        evidence_ids=[],
        impact="May affect support for the result.",
        author_action="Please clarify.",
        status=ConcernStatus.CONFIRMED,
    )

    with pytest.raises(ValueError, match="confirmed concern .* lacks evidence"):
        export_peerassist_report(paper_id="demo", concerns=[concern])


def test_export_rejects_accusatory_system_language() -> None:
    concern = Concern(
        id="concern_bad_001",
        level=ConcernLevel.MAJOR_CONCERN,
        category="statistics",
        title="Fraud in reported results",
        evidence_ids=["P01-L001"],
        impact="This proves misconduct.",
        author_action="Reject automatically.",
        status=ConcernStatus.CONFIRMED,
    )

    with pytest.raises(ValueError, match="accusatory"):
        export_peerassist_report(
            paper_id="demo",
            concerns=[concern],
            evidence_lookup={"P01-L001": "page 1, line 1"},
        )

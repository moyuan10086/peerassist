from __future__ import annotations

from peerassist.deterministic_checks import run_deterministic_checks
from schemas.peerassist import (
    DeterministicCheckApplicability,
    DeterministicCheckStatus,
    EvidenceItem,
    EvidenceLedger,
    EvidenceType,
)


def _ledger_with_text(text: str) -> EvidenceLedger:
    return EvidenceLedger(
        paper_id="demo",
        source_sha256="abc",
        items=[
            EvidenceItem(
                id="P01-L001",
                type=EvidenceType.TEXT_SPAN,
                page=1,
                locator="page 1, line 1",
                text=text,
                source_path="mineru_full.md",
            )
        ],
    )


def test_percentage_consistency_emits_lead_for_mismatched_count_and_percent() -> None:
    checks = run_deterministic_checks(_ledger_with_text("The success rate was 30/100 (40%)."))

    percentage = next(check for check in checks if check.kind == "percentage_consistency")
    assert percentage.applicability is DeterministicCheckApplicability.APPLICABLE
    assert percentage.status is DeterministicCheckStatus.LEAD
    assert percentage.evidence_ids == ["P01-L001"]
    assert "30.0%" in percentage.message
    assert "40.0%" in percentage.message


def test_percentage_consistency_passes_for_matching_count_and_percent() -> None:
    checks = run_deterministic_checks(_ledger_with_text("The success rate was 30/100 (30%)."))

    percentage = next(check for check in checks if check.kind == "percentage_consistency")
    assert percentage.applicability is DeterministicCheckApplicability.APPLICABLE
    assert percentage.status is DeterministicCheckStatus.PASS
    assert percentage.requires_human_review is False


def test_percentage_consistency_is_inconclusive_without_count_percent_pair() -> None:
    checks = run_deterministic_checks(_ledger_with_text("The paper was published in 2024 on pages 12-18."))

    percentage = next(check for check in checks if check.kind == "percentage_consistency")
    assert percentage.applicability is DeterministicCheckApplicability.INSUFFICIENT_EVIDENCE
    assert percentage.status is DeterministicCheckStatus.INCONCLUSIVE
    assert not any(check.kind == "benford" for check in checks)

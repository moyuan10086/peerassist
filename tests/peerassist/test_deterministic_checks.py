from __future__ import annotations

from peerassist.evidence_ledger import build_evidence_ledger
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


def _ledger_with_items(items: list[EvidenceItem]) -> EvidenceLedger:
    return EvidenceLedger(paper_id="demo", source_sha256="abc", items=items)


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


def test_figure_reference_check_emits_lead_when_referenced_figure_is_missing() -> None:
    checks = run_deterministic_checks(
        _ledger_with_items(
            [
                EvidenceItem(
                    id="P01-L001",
                    type=EvidenceType.TEXT_SPAN,
                    page=1,
                    locator="page 1, line 1",
                    text="As shown in Figure 2, the ablation improves accuracy.",
                    source_path="mineru_full.md",
                ),
                EvidenceItem(
                    id="P04-L001",
                    type=EvidenceType.FIGURE_CAPTION,
                    page=4,
                    locator="page 4, line 1",
                    text="Figure 1: Main model architecture.",
                    source_path="mineru_full.md",
                ),
            ]
        )
    )

    figure_check = next(check for check in checks if check.kind == "figure_table_reference")
    assert figure_check.applicability is DeterministicCheckApplicability.APPLICABLE
    assert figure_check.status is DeterministicCheckStatus.LEAD
    assert figure_check.evidence_ids == ["P01-L001"]
    assert figure_check.metadata["missing_references"] == ["Figure 2"]


def test_figure_reference_check_passes_when_referenced_figure_caption_exists() -> None:
    checks = run_deterministic_checks(
        _ledger_with_items(
            [
                EvidenceItem(
                    id="P01-L001",
                    type=EvidenceType.TEXT_SPAN,
                    page=1,
                    locator="page 1, line 1",
                    text="As shown in Figure 2, the ablation improves accuracy.",
                    source_path="mineru_full.md",
                ),
                EvidenceItem(
                    id="P04-L001",
                    type=EvidenceType.FIGURE_CAPTION,
                    page=4,
                    locator="page 4, line 1",
                    text="Figure 2: Ablation study.",
                    source_path="mineru_full.md",
                ),
            ]
        )
    )

    figure_check = next(check for check in checks if check.kind == "figure_table_reference")
    assert figure_check.applicability is DeterministicCheckApplicability.APPLICABLE
    assert figure_check.status is DeterministicCheckStatus.PASS
    assert figure_check.requires_human_review is False


def test_numbered_citation_check_emits_lead_when_reference_is_missing() -> None:
    checks = run_deterministic_checks(
        _ledger_with_items(
            [
                EvidenceItem(
                    id="P01-L001",
                    type=EvidenceType.TEXT_SPAN,
                    page=1,
                    locator="page 1, line 1",
                    text="This benchmark follows prior evaluation practice [2].",
                    source_path="mineru_full.md",
                ),
                EvidenceItem(
                    id="P08-L001",
                    type=EvidenceType.REFERENCE,
                    page=8,
                    locator="page 8, line 1",
                    text="[1] Smith et al. Benchmark paper.",
                    source_path="mineru_full.md",
                    metadata={"reference_number": "1"},
                ),
            ]
        )
    )

    citation_check = next(check for check in checks if check.kind == "numbered_citation_reference")
    assert citation_check.applicability is DeterministicCheckApplicability.APPLICABLE
    assert citation_check.status is DeterministicCheckStatus.LEAD
    assert citation_check.evidence_ids == ["P01-L001"]
    assert citation_check.metadata["missing_references"] == ["[2]"]


def test_numbered_citation_check_passes_when_reference_exists() -> None:
    checks = run_deterministic_checks(
        _ledger_with_items(
            [
                EvidenceItem(
                    id="P01-L001",
                    type=EvidenceType.TEXT_SPAN,
                    page=1,
                    locator="page 1, line 1",
                    text="This benchmark follows prior evaluation practice [2].",
                    source_path="mineru_full.md",
                ),
                EvidenceItem(
                    id="P08-L001",
                    type=EvidenceType.REFERENCE,
                    page=8,
                    locator="page 8, line 1",
                    text="[2] Smith et al. Benchmark paper.",
                    source_path="mineru_full.md",
                    metadata={"reference_number": "2"},
                ),
            ]
        )
    )

    citation_check = next(check for check in checks if check.kind == "numbered_citation_reference")
    assert citation_check.applicability is DeterministicCheckApplicability.APPLICABLE
    assert citation_check.status is DeterministicCheckStatus.PASS
    assert citation_check.requires_human_review is False


def test_table_reference_check_uses_body_cross_reference_from_markdown_ledger(tmp_path) -> None:
    source_pdf = tmp_path / "paper.pdf"
    source_pdf.write_bytes(b"%PDF demo")
    markdown = tmp_path / "mineru_full.md"
    markdown.write_text(
        "\n".join(
            [
                "# Results",
                "Table 3 reports the error breakdown.",
                "Table 1: Main results.",
            ]
        ),
        encoding="utf-8",
    )
    ledger = build_evidence_ledger(
        paper_id="demo",
        source_pdf=source_pdf,
        mineru_markdown_path=markdown,
    )

    checks = run_deterministic_checks(ledger)

    figure_check = next(check for check in checks if check.kind == "figure_table_reference")
    assert figure_check.status is DeterministicCheckStatus.LEAD
    assert figure_check.evidence_ids == ["P01-L002"]
    assert figure_check.metadata["missing_references"] == ["Table 3"]

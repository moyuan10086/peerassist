from __future__ import annotations

import json

from peerassist.evidence_ledger import build_evidence_ledger
from schemas.peerassist import EvidenceType


def test_build_evidence_ledger_from_mineru_markdown(tmp_path) -> None:
    source_pdf = tmp_path / "paper.pdf"
    source_pdf.write_bytes(b"%PDF demo")
    markdown = tmp_path / "mineru_full.md"
    markdown.write_text(
        "\n".join(
            [
                "<!-- page: 1 -->",
                "# Method",
                "The method uses a fixed seed.",
                "Figure 1: Overview of the pipeline.",
                "| Model | Accuracy |",
                "|---|---:|",
                "| Base | 30/100 (40%) |",
                "<!-- page: 2 -->",
                "Table 2 reports the main comparison.",
            ]
        ),
        encoding="utf-8",
    )

    ledger = build_evidence_ledger(
        paper_id="demo",
        source_pdf=source_pdf,
        mineru_markdown_path=markdown,
    )

    assert ledger.source_sha256
    assert ledger.items[0].id == "P01-L001"
    assert ledger.items[0].type is EvidenceType.SECTION
    assert any(item.id == "P01-L002" and item.text == "The method uses a fixed seed." for item in ledger.items)
    assert any(item.type is EvidenceType.FIGURE_CAPTION for item in ledger.items)
    assert any(item.type is EvidenceType.TABLE for item in ledger.items)
    assert ledger.coverage["section"] == 1
    assert ledger.coverage["figure_caption"] == 1
    assert ledger.coverage["table"] >= 1
    assert ledger.metadata["provider"] == "mineru"


def test_build_evidence_ledger_uses_content_list_for_table_cells(tmp_path) -> None:
    source_pdf = tmp_path / "paper.pdf"
    source_pdf.write_bytes(b"%PDF demo")
    markdown = tmp_path / "mineru_full.md"
    markdown.write_text("Table 1: Results\n", encoding="utf-8")
    content = tmp_path / "content_list.json"
    content.write_text(
        json.dumps(
            [
                {
                    "type": "table",
                    "page": 3,
                    "table_id": "Table 1",
                    "rows": [["Model", "Accuracy"], ["Base", "98.7%"]],
                }
            ]
        ),
        encoding="utf-8",
    )

    ledger = build_evidence_ledger(
        paper_id="demo",
        source_pdf=source_pdf,
        mineru_markdown_path=markdown,
        mineru_content_list_path=content,
    )

    cells = [item for item in ledger.items if item.type is EvidenceType.TABLE_CELL]
    assert cells
    assert cells[0].id.startswith("T001-R001-C001")
    assert cells[0].metadata["table"] == "Table 1"


def test_build_evidence_ledger_records_missing_parser_coverage(tmp_path) -> None:
    source_pdf = tmp_path / "paper.pdf"
    source_pdf.write_bytes(b"%PDF demo")

    ledger = build_evidence_ledger(
        paper_id="demo",
        source_pdf=source_pdf,
        mineru_markdown_path=None,
    )

    assert ledger.items == []
    assert ledger.coverage["text_span"] == 0
    assert "mineru_markdown_missing" in ledger.metadata["warnings"]


def test_build_evidence_ledger_marks_numbered_reference_lines(tmp_path) -> None:
    source_pdf = tmp_path / "paper.pdf"
    source_pdf.write_bytes(b"%PDF demo")
    markdown = tmp_path / "mineru_full.md"
    markdown.write_text(
        "\n".join(
            [
                "# Introduction",
                "Prior work established this benchmark [1].",
                "# References",
                "[1] Smith et al. Benchmark paper.",
            ]
        ),
        encoding="utf-8",
    )

    ledger = build_evidence_ledger(
        paper_id="demo",
        source_pdf=source_pdf,
        mineru_markdown_path=markdown,
    )

    references = [item for item in ledger.items if item.type is EvidenceType.REFERENCE]
    assert len(references) == 1
    assert references[0].text.startswith("[1]")
    assert references[0].metadata["reference_number"] == "1"


def test_build_evidence_ledger_does_not_classify_body_cross_references_as_captions(tmp_path) -> None:
    source_pdf = tmp_path / "paper.pdf"
    source_pdf.write_bytes(b"%PDF demo")
    markdown = tmp_path / "mineru_full.md"
    markdown.write_text(
        "\n".join(
            [
                "# Results",
                "Figure 2 shows the ablation trend.",
                "Table 3 reports the error breakdown.",
                "Figure 1: Overview of the pipeline.",
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

    by_text = {item.text: item for item in ledger.items}
    assert by_text["Figure 2 shows the ablation trend."].type is EvidenceType.TEXT_SPAN
    assert by_text["Table 3 reports the error breakdown."].type is EvidenceType.TEXT_SPAN
    assert by_text["Figure 1: Overview of the pipeline."].type is EvidenceType.FIGURE_CAPTION
    assert by_text["Table 1: Main results."].type is EvidenceType.TABLE

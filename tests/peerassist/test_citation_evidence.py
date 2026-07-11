from __future__ import annotations

import pytest

from peerassist.citation_evidence import (
    build_citation_links,
    build_reference_records,
    extract_citation_evidence,
    parse_numeric_citation,
)
from peerassist.evidence_ledger import with_citation_evidence
from schemas.citation import CitationLinkStatus
from schemas.peerassist import EvidenceItem, EvidenceLedger, EvidenceType


def evidence(
    item_id: str,
    item_type: EvidenceType,
    text: str,
    *,
    page: int = 2,
    section: str = "Related Work",
    bbox: list[float] | None = None,
    source_path: str = "/tmp/manuscript.md",
    metadata: dict[str, object] | None = None,
) -> EvidenceItem:
    return EvidenceItem(
        id=item_id,
        type=item_type,
        page=page,
        section=section,
        locator="page 2, line 4",
        text=text,
        bbox=bbox,
        source_path=source_path,
        metadata=metadata or {},
    )


def ledger_with(*items: EvidenceItem) -> EvidenceLedger:
    return EvidenceLedger(paper_id="paper-1", items=list(items))


@pytest.mark.parametrize(
    ("raw", "numbers"),
    [
        ("[1]", [1]),
        ("[1,3-5]", [1, 3, 4, 5]),
        ("[1, 1, 2]", [1, 2]),
    ],
)
def test_parse_numeric_citation_expands_valid_markers(raw: str, numbers: list[int]) -> None:
    parsed = parse_numeric_citation(raw)

    assert parsed.numbers == numbers
    assert parsed.status == "supported"
    assert parsed.raw == raw
    assert parsed.error_code == ""


@pytest.mark.parametrize("raw", ["[5-3]", "[1-]", "[-3]", "[a-3]", "[1-a]", "[1-101]"])
def test_parse_numeric_citation_rejects_invalid_ranges_without_partial_expansion(raw: str) -> None:
    parsed = parse_numeric_citation(raw)

    assert parsed.numbers == []
    assert parsed.status == "unsupported"
    assert parsed.error_code


def test_extracts_expanded_body_citations_and_inherits_location() -> None:
    source = evidence("P02-L004", EvidenceType.TEXT_SPAN, "Prior work [1, 3-5] established this.")

    result = extract_citation_evidence(ledger_with(source))

    assert [mention.metadata["number"] for mention in result.mentions] == [1, 3, 4, 5]
    assert [link.reference_number for link in result.links] == [1, 3, 4, 5]
    assert all(mention.page == 2 and mention.section == "Related Work" for mention in result.mentions)
    assert all(mention.metadata["source_evidence_id"] == "P02-L004" for mention in result.mentions)
    assert result.links and all(link.status is CitationLinkStatus.MISSING_REFERENCE for link in result.links)


def test_extract_deduplicates_repeated_marker_numbers_into_unique_mentions_and_links() -> None:
    source = evidence("P02-L004", EvidenceType.TEXT_SPAN, "Prior work [1, 1, 2].")

    result = extract_citation_evidence(ledger_with(source))

    assert [mention.id for mention in result.mentions] == ["C-P02-L004-11-20-1", "C-P02-L004-11-20-2"]
    assert [link.reference_number for link in result.links] == [1, 2]


def test_extract_ignores_non_numeric_and_statistical_brackets_but_keeps_body_citations() -> None:
    default = evidence("P02-L004", EvidenceType.TEXT_SPAN, "The mode is [default].")
    vector = evidence("P02-L005", EvidenceType.TEXT_SPAN, "The vector is [1, 3].")
    citation = evidence("P02-L006", EvidenceType.TEXT_SPAN, "Prior work [1, 3].")

    result = extract_citation_evidence(ledger_with(default, vector, citation))

    assert [mention.metadata["number"] for mention in result.mentions] == [1, 3]
    assert [link.reference_number for link in result.links] == [1, 3]
    assert result.unsupported_markers == []
    assert result.warnings == []


def test_extract_uses_only_immediate_non_citation_context_before_bracket() -> None:
    vector = evidence("P02-L004", EvidenceType.TEXT_SPAN, "The vector is [1, 3].")
    range = evidence("P02-L005", EvidenceType.TEXT_SPAN, "Across a broad range of methods, prior work [2].")
    baseline = evidence("P02-L006", EvidenceType.TEXT_SPAN, "The vector baseline follows prior work [1].")

    result = extract_citation_evidence(ledger_with(vector, range, baseline))

    assert [mention.metadata["number"] for mention in result.mentions] == [2, 1]
    assert result.unsupported_markers == []


def test_extract_routes_mixed_hyphenated_numeric_markers_to_unsupported() -> None:
    source = evidence("P02-L004", EvidenceType.TEXT_SPAN, "Broken [1-a] and [a-3].")

    result = extract_citation_evidence(ledger_with(source))

    assert result.mentions == []
    assert result.links == []
    assert [marker.raw for marker in result.unsupported_markers] == ["[1-a]", "[a-3]"]
    assert [marker.error_code for marker in result.unsupported_markers] == [
        "invalid_range_endpoint",
        "invalid_range_endpoint",
    ]


def test_extract_ignores_non_citation_brackets_without_unsupported_warning() -> None:
    result = extract_citation_evidence(ledger_with(evidence("P02-L004", EvidenceType.TEXT_SPAN, "Use [default].")))

    assert result.mentions == []
    assert result.links == []
    assert result.unsupported_markers == []
    assert result.warnings == []


def test_mention_inherits_trace_and_uses_stable_offsets() -> None:
    source = evidence(
        "P02-L004",
        EvidenceType.TEXT_SPAN,
        "See [7].",
        bbox=[1, 2, 3, 4],
        source_path="/tmp/paper.md",
    )

    result = extract_citation_evidence(ledger_with(source))
    mention = result.mentions[0]

    assert mention.id == "C-P02-L004-4-7-7"
    assert mention.text == "[7]"
    assert mention.page == source.page
    assert mention.section == source.section
    assert mention.locator == source.locator
    assert mention.bbox == [1, 2, 3, 4]
    assert mention.source_path == "/tmp/paper.md"
    assert mention.metadata == {
        "source_evidence_id": "P02-L004",
        "source_type": "text_span",
        "start": 4,
        "end": 7,
        "raw_marker": "[7]",
        "confidence": 1.0,
        "number": 7,
        "original_sentence": "See [7].",
    }


def test_extracts_invalid_markers_as_unsupported_without_links() -> None:
    source = evidence("P02-L004", EvidenceType.TEXT_SPAN, "Broken [5-3], [1-], and [1-101].")

    result = extract_citation_evidence(ledger_with(source))

    assert result.mentions == []
    assert result.links == []
    assert [marker.raw for marker in result.unsupported_markers] == ["[5-3]", "[1-]", "[1-101]"]
    assert all(marker.status is CitationLinkStatus.UNSUPPORTED_SYNTAX for marker in result.unsupported_markers)
    assert len(result.warnings) == 3


def test_excludes_reference_section_and_confidence_interval_brackets() -> None:
    body = evidence("P02-L004", EvidenceType.TEXT_SPAN, "The effect was 95% CI [1, 3].")
    reference = evidence("P08-L014", EvidenceType.REFERENCE, "[1] Alpha Study. 2020.", page=8, section="References")

    result = extract_citation_evidence(ledger_with(body, reference))

    assert result.mentions == []
    assert result.links == []
    assert len(result.references) == 1


def test_excludes_text_span_citations_in_reference_section_but_keeps_continuation() -> None:
    reference = evidence("P08-L014", EvidenceType.REFERENCE, "[1] Alpha Study.", page=8, section="References")
    continuation = evidence(
        "P08-L015",
        EvidenceType.TEXT_SPAN,
        "Journal discussion cites [9]. 2020.",
        page=8,
        section="References",
    )

    result = extract_citation_evidence(ledger_with(reference, continuation))

    assert result.mentions == []
    assert result.links == []
    assert result.references[0].raw_text == "[1] Alpha Study. Journal discussion cites [9]. 2020."
    assert result.references[0].source_evidence_ids == ["P08-L014", "P08-L015"]


@pytest.mark.parametrize("item_type", [EvidenceType.FIGURE_CAPTION, EvidenceType.TABLE, EvidenceType.TABLE_CELL])
def test_caption_and_table_mentions_preserve_source_type_at_lower_confidence(item_type: EvidenceType) -> None:
    source = evidence("P02-L004", item_type, "Figure 1: Adapted from [2].")

    result = extract_citation_evidence(ledger_with(source))

    assert result.mentions[0].metadata["source_type"] == item_type.value
    assert result.mentions[0].metadata["confidence"] < 1.0


def test_build_reference_records_uses_stable_hash_and_aggregates_identical_duplicates() -> None:
    first = evidence("P08-L014", EvidenceType.REFERENCE, "[1] Alpha Study. 2020. doi:10.1/a", page=8, section="References")
    duplicate = evidence("P09-L002", EvidenceType.REFERENCE, "[1] Alpha Study. 2020. doi:10.1/a", page=9, section="References")

    records = build_reference_records([first, duplicate])

    assert len(records) == 1
    assert records[0].id == "R-1-61339f76"
    assert records[0].source_evidence_ids == ["P08-L014", "P09-L002"]
    assert records[0].doi == "10.1/a"
    assert records[0].year == 2020
    assert records[0].title == "Alpha Study"


def test_build_reference_records_extracts_only_reliable_apa_like_titles() -> None:
    apa = evidence(
        "P08-L014",
        EvidenceType.REFERENCE,
        "[2] Smith, J. (2020). Alpha Study.",
        page=8,
        section="References",
    )
    year_leading = evidence(
        "P08-L015",
        EvidenceType.REFERENCE,
        "[3] 2020. Alpha Study.",
        page=8,
        section="References",
    )
    ambiguous = evidence(
        "P08-L016",
        EvidenceType.REFERENCE,
        "[4] Smith 2020. Alpha Study.",
        page=8,
        section="References",
    )

    records = build_reference_records([apa, year_leading, ambiguous])

    assert [record.title for record in records] == ["Alpha Study", "", ""]


@pytest.mark.parametrize(
    ("raw", "doi"),
    [
        ("[1] Alpha Study. doi:10.1000/foo(bar)", "10.1000/foo(bar)"),
        ("[1] Alpha Study. doi:10.1000/foo(bar).", "10.1000/foo(bar)"),
        ("[1] Alpha Study. doi:10.1000/foo;", "10.1000/foo"),
    ],
)
def test_build_reference_records_trims_only_unbalanced_doi_sentence_punctuation(raw: str, doi: str) -> None:
    record = build_reference_records([evidence("P08-L014", EvidenceType.REFERENCE, raw, section="References")])[0]

    assert record.doi == doi


@pytest.mark.parametrize(
    "doi_source",
    [
        "doi:10.1000/foo(bar).",
        "https://doi.org/10.1000/foo(bar).",
        "http://doi.org/10.1000/foo(bar).",
        "https://dx.doi.org/10.1000/foo(bar).",
        "http://dx.doi.org/10.1000/foo(bar).",
    ],
)
def test_build_reference_records_normalizes_doi_url_forms(doi_source: str) -> None:
    raw = f"[1] Alpha Study. 2020. {doi_source}"

    record = build_reference_records([evidence("P08-L014", EvidenceType.REFERENCE, raw, section="References")])[0]

    assert record.doi == "10.1000/foo(bar)"


def test_build_reference_records_groups_multiline_continuations() -> None:
    first = evidence("P08-L014", EvidenceType.REFERENCE, "[1] Alpha Study.", page=8, section="References")
    continuation = evidence("P08-L015", EvidenceType.TEXT_SPAN, "Journal of Testing. 2020.", page=8, section="References")
    second = evidence("P08-L016", EvidenceType.REFERENCE, "[2] Beta Study. 2021.", page=8, section="References")

    records = build_reference_records([first, continuation, second])

    assert len(records) == 2
    assert records[0].raw_text == "[1] Alpha Study. Journal of Testing. 2020."
    assert records[0].source_evidence_ids == ["P08-L014", "P08-L015"]


def test_links_explicit_numbers_as_linked_ambiguous_and_missing() -> None:
    mention = evidence(
        "C-P02-L004-4-7-1",
        EvidenceType.CITATION,
        "[1]",
        metadata={"number": 1},
    )
    one = evidence("P08-L014", EvidenceType.REFERENCE, "[1] Alpha Study. 2020.", page=8, section="References")
    two = evidence("P08-L015", EvidenceType.REFERENCE, "[1] Beta Study. 2021.", page=8, section="References")

    ambiguous = build_citation_links([mention], build_reference_records([one, two]))
    missing = build_citation_links([mention.model_copy(update={"metadata": {"number": 9}})], [])

    assert ambiguous[0].id == "citation-link-C-P02-L004-4-7-1"
    assert ambiguous[0].status is CitationLinkStatus.AMBIGUOUS
    assert len(ambiguous[0].reference_record_ids) == 2
    assert missing[0].status is CitationLinkStatus.MISSING_REFERENCE
    assert missing[0].reference_record_ids == []


def test_extract_warns_for_duplicate_doi_without_aggregating_distinct_records() -> None:
    body = evidence("P02-L004", EvidenceType.TEXT_SPAN, "See [1, 2].")
    one = evidence("P08-L014", EvidenceType.REFERENCE, "[1] Alpha Study. 2020. doi:10.1/a", page=8, section="References")
    two = evidence("P08-L015", EvidenceType.REFERENCE, "[2] Beta Study. 2021. doi:10.1/a", page=8, section="References")

    result = extract_citation_evidence(ledger_with(body, one, two))

    assert len(result.references) == 2
    assert any(warning.startswith("duplicate_doi:10.1/a") for warning in result.warnings)


def test_extract_warns_for_duplicate_doi_across_url_forms() -> None:
    one = evidence(
        "P08-L014",
        EvidenceType.REFERENCE,
        "[1] Alpha Study. 2020. doi:10.1000/foo(bar).",
        page=8,
        section="References",
    )
    two = evidence(
        "P08-L015",
        EvidenceType.REFERENCE,
        "[2] Beta Study. 2021. https://doi.org/10.1000/foo(bar).",
        page=8,
        section="References",
    )

    result = extract_citation_evidence(ledger_with(one, two))

    assert any(warning == "duplicate_doi:10.1000/foo(bar)" for warning in result.warnings)


def test_extract_builds_stable_link_for_see_marker() -> None:
    body = evidence("P02-L004", EvidenceType.TEXT_SPAN, "See [1].")
    reference = evidence("P08-L014", EvidenceType.REFERENCE, "[1] Alpha Study. 2020.", page=8, section="References")

    result = extract_citation_evidence(ledger_with(body, reference))

    assert result.mentions[0].id == "C-P02-L004-4-7-1"
    assert result.links[0].id == "citation-link-C-P02-L004-4-7-1"
    assert result.links[0].status is CitationLinkStatus.LINKED


def test_with_citation_evidence_copies_ledger_and_recomputes_coverage() -> None:
    source = evidence("P02-L004", EvidenceType.TEXT_SPAN, "See [1].", metadata={"nested": {"value": "source"}})
    original = EvidenceLedger(paper_id="paper-1", items=[source], metadata={"nested": {"value": "source"}})

    augmented = with_citation_evidence(original)

    assert original.items == [source]
    assert original.coverage.get("citation", 0) == 0
    assert len(augmented.items) == 2
    assert augmented.coverage["citation"] == 1
    assert augmented.items[-1].id == "C-P02-L004-4-7-1"

    augmented.items[0].text = "changed"
    augmented.items[0].metadata["nested"]["value"] = "changed"
    augmented.metadata["nested"]["value"] = "changed"

    assert source.text == "See [1]."
    assert source.metadata["nested"]["value"] == "source"
    assert original.metadata["nested"]["value"] == "source"

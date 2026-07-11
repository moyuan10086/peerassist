"""Deterministic citation metadata normalization and comparison helpers."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from typing import Any

from schemas.citation import CitationFieldComparison, CitationFieldDifference, ReferenceRecord

_DOI_PREFIX_RE = re.compile(r"^(?:doi\s*:\s*|https?://(?:dx\.)?doi\.org/)", re.IGNORECASE)


def normalize_doi(value: object) -> str:
    """Return a canonical DOI without changing meaningful DOI punctuation."""
    doi = _DOI_PREFIX_RE.sub("", str(value or "").strip())
    return doi.lower()


def normalize_title(value: object) -> str:
    """Normalize a title with NFKC, case folding, whitespace folding, and punctuation removal."""
    normalized = unicodedata.normalize("NFKC", str(value or "")).lower()
    without_punctuation = "".join(
        character for character in normalized if not unicodedata.category(character).startswith("P")
    )
    return " ".join(without_punctuation.split())


def title_similarity(left: object, right: object) -> float:
    """Return deterministic Jaccard similarity over normalized title token sets."""
    left_tokens = set(normalize_title(left).split())
    right_tokens = set(normalize_title(right).split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _field_difference(
    *,
    field: str,
    manuscript_value: object,
    external_value: object,
    normalized_manuscript_value: str,
    normalized_external_value: str,
    is_match: bool,
    rule: str,
) -> CitationFieldDifference:
    return CitationFieldDifference(
        field=field,
        manuscript_value=str(manuscript_value),
        external_value=str(external_value),
        normalized_manuscript_value=normalized_manuscript_value,
        normalized_external_value=normalized_external_value,
        comparison=CitationFieldComparison.MATCH if is_match else CitationFieldComparison.MISMATCH,
        rule=rule,
    )


def _external_years(metadata: Mapping[str, Any]) -> list[tuple[str, int]]:
    years: list[tuple[str, int]] = []
    for key in ("year", "online_year", "print_year"):
        value = metadata.get(key)
        if value is None or value == "":
            continue
        parsed = _valid_year(value)
        if parsed is not None:
            years.append((key, parsed))
    return years


def _valid_year(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def compare_reference_metadata(
    reference: ReferenceRecord, external_metadata: Mapping[str, Any]
) -> list[CitationFieldDifference]:
    """Compare the independently observable DOI, title, and year fields.

    Authors are intentionally omitted: they may rank candidates but do not produce
    standalone metadata mismatches in this audit version.
    """
    differences: list[CitationFieldDifference] = []

    external_doi = external_metadata.get("doi")
    if reference.doi and external_doi:
        manuscript_doi = normalize_doi(reference.doi)
        observed_doi = normalize_doi(external_doi)
        differences.append(
            _field_difference(
                field="doi",
                manuscript_value=reference.doi,
                external_value=external_doi,
                normalized_manuscript_value=manuscript_doi,
                normalized_external_value=observed_doi,
                is_match=bool(manuscript_doi and observed_doi and manuscript_doi == observed_doi),
                rule="normalized_doi_exact",
            )
        )

    external_title = external_metadata.get("title")
    if reference.title and external_title:
        manuscript_title = normalize_title(reference.title)
        observed_title = normalize_title(external_title)
        similarity = title_similarity(reference.title, external_title)
        differences.append(
            _field_difference(
                field="title",
                manuscript_value=reference.title,
                external_value=external_title,
                normalized_manuscript_value=manuscript_title,
                normalized_external_value=observed_title,
                is_match=similarity >= 0.95,
                rule="title_similarity_unique" if similarity >= 0.95 else "title_similarity_below_unique",
            )
        )

    external_years = _external_years(external_metadata)
    if reference.year is not None and external_years:
        matched_key, matched_year = next(
            ((key, year) for key, year in external_years if year == reference.year), external_years[0]
        )
        external_text = str(matched_year)
        if matched_key != "year":
            external_text = f"{matched_key}:{matched_year}"
        differences.append(
            _field_difference(
                field="year",
                manuscript_value=reference.year,
                external_value=external_text,
                normalized_manuscript_value=str(reference.year),
                normalized_external_value=str(matched_year),
                is_match=matched_year == reference.year,
                rule="year_exact_or_online_print_alias",
            )
        )

    return differences

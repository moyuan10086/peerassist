"""Extract traceable numeric citation and reference evidence without inference."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

from schemas.citation import (
    CitationEvidenceResult,
    CitationLink,
    CitationLinkStatus,
    ReferenceRecord,
    UnsupportedCitationMarker,
)
from schemas.peerassist import EvidenceItem, EvidenceLedger, EvidenceType

NUMERIC_MARKER_RE = re.compile(r"\[(?P<body>[^\]\r\n]+)\]")
NUMBERED_REFERENCE_RE = re.compile(r"^\[\s*(?P<number>\d+)\s*\]\s*(?P<body>.*)$")
DOI_RE = re.compile(
    r"(?:\bdoi\s*:\s*|https?://(?:dx\.)?doi\.org/)(?P<doi>10\.\d{1,9}/[-._;()/:A-Z0-9]+)",
    re.I,
)
YEAR_RE = re.compile(r"\b(?P<year>(?:19|20)\d{2})\b")
CI_PREFIX_RE = re.compile(r"(?:\b\d+(?:\.\d+)?%\s*)?\bCI\s*$", re.I)
NUMERIC_CITATION_SHAPED_RE = re.compile(
    r"\[\s*(?:[\d,\-\s]+|(?:\d\s*-\s*[a-z]|[a-z]\s*-\s*\d))\s*\]"
)
IMMEDIATE_NON_CITATION_CONTEXT_RE = re.compile(
    r"(?:\b(?:x|values)\s*(?:=|:)|\b(?:bounds|shape|dimensions)\s+(?:were|is|are)|"
    r"\b(?:tensor|array|vector|coordinates|indices)\s*(?:is|are|=|:)|"
    r"\b(?:confidence\s+)?interval|\brange|\bCI)\s*[\s:;,=]*$",
    re.I,
)
APA_TITLE_RE = re.compile(
    r"^.+?\(\s*(?:19|20)\d{2}[a-z]?\s*\)\.\s*(?P<title>[^.]+)\.",
    re.I,
)
REFERENCE_SECTIONS = frozenset({"references", "bibliography", "参考文献"})


@dataclass(frozen=True)
class NumericCitationParse:
    numbers: list[int]
    status: str
    raw: str
    error_code: str = ""


def parse_numeric_citation(raw: str, *, max_range: int = 100) -> NumericCitationParse:
    """Parse a bracketed numeric marker, never returning partial range expansion."""
    if not raw.startswith("[") or not raw.endswith("]"):
        return NumericCitationParse([], "unsupported", raw, "invalid_brackets")

    body = raw[1:-1].strip()
    if not body:
        return NumericCitationParse([], "unsupported", raw, "empty_marker")

    numbers: list[int] = []
    for component in body.split(","):
        component = component.strip()
        if not component:
            return NumericCitationParse([], "unsupported", raw, "empty_component")
        if "-" in component:
            endpoints = component.split("-")
            if len(endpoints) != 2 or not all(endpoint.strip().isdigit() for endpoint in endpoints):
                return NumericCitationParse([], "unsupported", raw, "invalid_range_endpoint")
            start, end = (int(endpoint.strip()) for endpoint in endpoints)
            if start <= 0 or end <= 0:
                return NumericCitationParse([], "unsupported", raw, "nonpositive_number")
            if end < start:
                return NumericCitationParse([], "unsupported", raw, "descending_range")
            if end - start + 1 > max_range:
                return NumericCitationParse([], "unsupported", raw, "range_expansion_limit")
            numbers.extend(range(start, end + 1))
            continue
        if not component.isdigit() or int(component) <= 0:
            return NumericCitationParse([], "unsupported", raw, "nonnumeric_component")
        numbers.append(int(component))

    return NumericCitationParse(list(dict.fromkeys(numbers)), "supported", raw)


def _is_citation_source(item: EvidenceItem) -> bool:
    return (
        item.section.strip().casefold() not in REFERENCE_SECTIONS
        and item.type
        in {EvidenceType.TEXT_SPAN, EvidenceType.FIGURE_CAPTION, EvidenceType.TABLE, EvidenceType.TABLE_CELL}
    )


def _citation_confidence(item: EvidenceItem) -> float:
    return 1.0 if item.type is EvidenceType.TEXT_SPAN else 0.7


def _is_confidence_interval(text: str, start: int) -> bool:
    return bool(CI_PREFIX_RE.search(text[:start]))


def _is_non_citation_context(text: str, start: int) -> bool:
    return bool(IMMEDIATE_NON_CITATION_CONTEXT_RE.search(text[:start]))


def _citation_mention(source: EvidenceItem, raw: str, start: int, end: int, number: int) -> EvidenceItem:
    return EvidenceItem(
        id=f"C-{source.id}-{start}-{end}-{number}",
        type=EvidenceType.CITATION,
        page=source.page,
        section=source.section,
        locator=source.locator,
        text=raw,
        bbox=source.bbox,
        source_path=source.source_path,
        metadata={
            "source_evidence_id": source.id,
            "source_type": source.type.value,
            "start": start,
            "end": end,
            "raw_marker": raw,
            "confidence": _citation_confidence(source),
            "number": number,
            "original_sentence": source.text,
        },
    )


def _unsupported_marker(source: EvidenceItem, raw: str, start: int, end: int, error_code: str) -> UnsupportedCitationMarker:
    return UnsupportedCitationMarker(
        id=f"unsupported-citation-{source.id}-{start}-{end}",
        source_evidence_id=source.id,
        raw=raw,
        start=start,
        end=end,
        status=CitationLinkStatus.UNSUPPORTED_SYNTAX,
        error_code=error_code,
    )


def _normalized_raw(raw: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", raw).lower().split())


def _reference_id(number: int, raw: str) -> str:
    digest = hashlib.sha256(_normalized_raw(raw).encode()).hexdigest()[:8]
    return f"R-{number}-{digest}"


def _reference_groups(items: list[EvidenceItem]) -> list[tuple[int, str, list[str]]]:
    groups: list[tuple[int, str, list[str]]] = []
    current: tuple[int, str, list[str]] | None = None
    for item in items:
        if item.type is EvidenceType.REFERENCE:
            match = NUMBERED_REFERENCE_RE.match(item.text.strip())
            if not match:
                current = None
                continue
            current = (int(match.group("number")), item.text.strip(), [item.id])
            groups.append(current)
            continue
        if current is not None and item.section.strip().casefold() in REFERENCE_SECTIONS:
            continuation = item.text.strip()
            if continuation:
                number, raw, source_ids = current
                current = (number, f"{raw} {continuation}", [*source_ids, item.id])
                groups[-1] = current
    return groups


def _conservative_title(raw: str, number: int, year: int | None) -> str:
    match = NUMBERED_REFERENCE_RE.match(raw)
    body = match.group("body").strip() if match else raw.strip()
    body = DOI_RE.sub("", body).strip()
    apa_match = APA_TITLE_RE.match(body)
    if apa_match:
        return apa_match.group("title").strip(" .;,:")
    body = body.strip(" .;,:" )
    if year is None or body.startswith(str(year)):
        return ""
    before_year = body.split(str(year), maxsplit=1)[0].strip()
    if not before_year.endswith(".") or "," in before_year or re.search(r"\bet\s+al\b|\b[A-Z]\.", before_year):
        return ""
    return before_year.rstrip(".").strip()


def _trim_doi_sentence_punctuation(doi: str) -> str:
    """Remove terminal prose punctuation while retaining balanced DOI delimiters."""
    trimmed = doi.rstrip(".,;:")
    while trimmed.endswith(")"):
        opening = trimmed.count("(")
        closing = trimmed.count(")")
        if closing <= opening:
            break
        trimmed = trimmed[:-1].rstrip(".,;:")
    return trimmed


def build_reference_records(items: list[EvidenceItem]) -> list[ReferenceRecord]:
    """Build stable records, retaining different text under the same number."""
    records_by_key: dict[tuple[int, str], ReferenceRecord] = {}
    for number, raw, source_ids in _reference_groups(items):
        key = (number, _normalized_raw(raw))
        existing = records_by_key.get(key)
        if existing is not None:
            existing.source_evidence_ids.extend(source_ids)
            continue
        doi_match = DOI_RE.search(raw)
        year_match = YEAR_RE.search(raw)
        doi = _trim_doi_sentence_punctuation(doi_match.group("doi")).lower() if doi_match else ""
        year = int(year_match.group("year")) if year_match else None
        records_by_key[key] = ReferenceRecord(
            id=_reference_id(number, raw),
            reference_number=number,
            source_evidence_ids=list(source_ids),
            raw_text=raw,
            title=_conservative_title(raw, number, year),
            doi=doi,
            year=year,
            parse_confidence=0.9,
        )
    return list(records_by_key.values())


def build_citation_links(mentions: list[EvidenceItem], references: list[ReferenceRecord]) -> list[CitationLink]:
    """Join mentions to references only by their explicitly parsed number."""
    references_by_number: dict[int, list[ReferenceRecord]] = {}
    for reference in references:
        references_by_number.setdefault(reference.reference_number, []).append(reference)

    links: list[CitationLink] = []
    for mention in mentions:
        number = int(mention.metadata["number"])
        candidates = references_by_number.get(number, [])
        if len(candidates) == 1:
            status = CitationLinkStatus.LINKED
        elif candidates:
            status = CitationLinkStatus.AMBIGUOUS
        else:
            status = CitationLinkStatus.MISSING_REFERENCE
        links.append(
            CitationLink(
                id=f"citation-link-{mention.id}",
                mention_evidence_id=mention.id,
                reference_number=number,
                status=status,
                reference_record_ids=[record.id for record in candidates],
                reference_evidence_ids=[
                    evidence_id for record in candidates for evidence_id in record.source_evidence_ids
                ],
            )
        )
    return links


def _duplicate_doi_warnings(references: list[ReferenceRecord]) -> list[str]:
    by_doi: dict[str, list[str]] = {}
    for reference in references:
        if reference.doi:
            by_doi.setdefault(reference.doi, []).append(reference.id)
    return [f"duplicate_doi:{doi}" for doi, ids in by_doi.items() if len(ids) > 1]


def extract_citation_evidence(ledger: EvidenceLedger) -> CitationEvidenceResult:
    """Return numeric citation mentions, parsed references, links, and warnings."""
    mentions: list[EvidenceItem] = []
    unsupported_markers: list[UnsupportedCitationMarker] = []
    warnings: list[str] = []
    for source in ledger.items:
        if not _is_citation_source(source):
            continue
        for match in NUMERIC_MARKER_RE.finditer(source.text):
            raw = match.group(0)
            if not NUMERIC_CITATION_SHAPED_RE.fullmatch(raw):
                continue
            if _is_confidence_interval(source.text, match.start()) or _is_non_citation_context(source.text, match.start()):
                continue
            parsed = parse_numeric_citation(raw)
            if parsed.status == "unsupported":
                unsupported_markers.append(
                    _unsupported_marker(source, raw, match.start(), match.end(), parsed.error_code)
                )
                warnings.append(f"unsupported_citation_marker:{source.id}:{match.start()}:{parsed.error_code}")
                continue
            mentions.extend(
                _citation_mention(source, raw, match.start(), match.end(), number)
                for number in parsed.numbers
            )

    references = build_reference_records(ledger.items)
    warnings.extend(_duplicate_doi_warnings(references))
    return CitationEvidenceResult(
        mentions=mentions,
        references=references,
        links=build_citation_links(mentions, references),
        unsupported_markers=unsupported_markers,
        warnings=warnings,
    )

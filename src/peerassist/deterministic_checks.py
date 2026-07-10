"""Deterministic PeerAssist checks.

Checks in this module produce review leads, not final judgments. They must
record applicability explicitly so downstream agents cannot treat missing
evidence as a clean pass.
"""

from __future__ import annotations

import re

from schemas.peerassist import (
    DeterministicCheck,
    DeterministicCheckApplicability,
    DeterministicCheckStatus,
    EvidenceItem,
    EvidenceLedger,
    EvidenceType,
)

COUNT_PERCENT_RE = re.compile(
    r"(?P<count>\d+(?:\.\d+)?)\s*/\s*(?P<denom>\d+(?:\.\d+)?)\s*"
    r"\(\s*(?P<percent>\d+(?:\.\d+)?)\s*%\s*\)"
)
FIGURE_REF_RE = re.compile(r"\b(?:fig(?:ure)?\.?)\s*(?P<num>S?\d+[A-Za-z0-9_.-]*)\b", re.I)
TABLE_REF_RE = re.compile(r"\btable\s*(?P<num>S?\d+[A-Za-z0-9_.-]*)\b", re.I)
CITATION_BRACKET_RE = re.compile(r"\[(?P<body>\d+(?:\s*,\s*\d+)*)\]")
REFERENCE_NUMBER_RE = re.compile(r"^\[\s*(?P<num>\d+)\s*\]")
SIGNIFICANCE_LEGEND_RE = re.compile(
    r"(?P<stars>\*{1,3})\s*p\s*(?:<|≤)\s*(?P<threshold>0?\.\d+|\d+(?:\.\d+)?)",
    re.I,
)
SIGNIFICANCE_VALUE_RE = re.compile(
    r"(?P<value>[-+]?\d+(?:\.\d+)?)\s*(?P<stars>\*{1,3})\s*"
    r"(?:\(|,)?\s*p\s*=\s*(?P<pvalue>0?\.\d+|\d+(?:\.\d+)?)",
    re.I,
)


def _checkable_items(ledger: EvidenceLedger) -> list[EvidenceItem]:
    return [
        item
        for item in ledger.items
        if item.type in {EvidenceType.TEXT_SPAN, EvidenceType.TABLE, EvidenceType.TABLE_CELL}
    ]


def _percentage_check_for_item(item: EvidenceItem, idx: int) -> DeterministicCheck | None:
    match = COUNT_PERCENT_RE.search(item.text)
    if not match:
        return None
    count = float(match.group("count"))
    denom = float(match.group("denom"))
    reported = float(match.group("percent"))
    if denom == 0:
        return DeterministicCheck(
            id=f"check_percentage_consistency_{idx:03d}",
            kind="percentage_consistency",
            applicability=DeterministicCheckApplicability.NOT_APPLICABLE,
            status=DeterministicCheckStatus.INCONCLUSIVE,
            evidence_ids=[item.id],
            message="Cannot verify percentage consistency because the denominator is zero.",
            benign_explanations=["table transcription issue", "non-count denominator"],
        )

    computed = count / denom * 100.0
    delta = abs(computed - reported)
    if delta <= 0.5:
        return DeterministicCheck(
            id=f"check_percentage_consistency_{idx:03d}",
            kind="percentage_consistency",
            applicability=DeterministicCheckApplicability.APPLICABLE,
            status=DeterministicCheckStatus.PASS,
            evidence_ids=[item.id],
            message=f"Reported percentage {reported:.1f}% is consistent with {count:g}/{denom:g}.",
            benign_explanations=["rounding"],
            requires_human_review=False,
            metadata={"computed_percent": computed, "reported_percent": reported, "delta": delta},
        )
    return DeterministicCheck(
        id=f"check_percentage_consistency_{idx:03d}",
        kind="percentage_consistency",
        applicability=DeterministicCheckApplicability.APPLICABLE,
        status=DeterministicCheckStatus.LEAD,
        evidence_ids=[item.id],
        message=(
            f"Reported percentage {reported:.1f}% does not match "
            f"{count:g}/{denom:g} = {computed:.1f}%."
        ),
        benign_explanations=["rounding", "different denominator", "filtered sample"],
        metadata={"computed_percent": computed, "reported_percent": reported, "delta": delta},
    )


def _percentage_consistency_checks(ledger: EvidenceLedger) -> list[DeterministicCheck]:
    checks: list[DeterministicCheck] = []
    for idx, item in enumerate(_checkable_items(ledger), start=1):
        check = _percentage_check_for_item(item, idx)
        if check is not None:
            checks.append(check)
    if checks:
        return checks
    return [
        DeterministicCheck(
            id="check_percentage_consistency_000",
            kind="percentage_consistency",
            applicability=DeterministicCheckApplicability.INSUFFICIENT_EVIDENCE,
            status=DeterministicCheckStatus.INCONCLUSIVE,
            evidence_ids=[],
            message="No count/denominator plus percentage pattern was available for deterministic checking.",
            benign_explanations=["paper may not report count-based percentages"],
            requires_human_review=False,
        )
    ]


def _normalized_num(raw: str) -> str:
    return str(raw or "").strip().rstrip(".,;:").upper()


def _reference_label(kind: str, number: str) -> str:
    return f"{kind.title()} {number}"


def _available_figure_table_numbers(ledger: EvidenceLedger) -> dict[str, set[str]]:
    available = {"figure": set(), "table": set()}
    for item in ledger.items:
        if item.type in {EvidenceType.FIGURE, EvidenceType.FIGURE_CAPTION}:
            available["figure"].update(
                _normalized_num(match.group("num")) for match in FIGURE_REF_RE.finditer(item.text)
            )
        elif item.type is EvidenceType.TABLE:
            available["table"].update(
                _normalized_num(match.group("num")) for match in TABLE_REF_RE.finditer(item.text)
            )
    return available


def _referenced_figure_table_numbers(ledger: EvidenceLedger) -> list[tuple[str, str, EvidenceItem]]:
    references: list[tuple[str, str, EvidenceItem]] = []
    for item in ledger.items:
        if item.type not in {EvidenceType.TEXT_SPAN, EvidenceType.TABLE_CELL}:
            continue
        references.extend(
            ("figure", _normalized_num(match.group("num")), item)
            for match in FIGURE_REF_RE.finditer(item.text)
        )
        references.extend(
            ("table", _normalized_num(match.group("num")), item)
            for match in TABLE_REF_RE.finditer(item.text)
        )
    return [(kind, number, item) for kind, number, item in references if number]


def _figure_table_reference_checks(ledger: EvidenceLedger) -> list[DeterministicCheck]:
    references = _referenced_figure_table_numbers(ledger)
    if not references:
        return [
            DeterministicCheck(
                id="check_figure_table_reference_000",
                kind="figure_table_reference",
                applicability=DeterministicCheckApplicability.INSUFFICIENT_EVIDENCE,
                status=DeterministicCheckStatus.INCONCLUSIVE,
                evidence_ids=[],
                message="No explicit Figure/Table references were available for deterministic checking.",
                benign_explanations=["paper may not include explicit figure or table cross-references"],
                requires_human_review=False,
            )
        ]

    available = _available_figure_table_numbers(ledger)
    missing: list[str] = []
    missing_evidence_ids: list[str] = []
    for kind, number, item in references:
        if number in available[kind]:
            continue
        label = _reference_label(kind, number)
        if label not in missing:
            missing.append(label)
        if item.id not in missing_evidence_ids:
            missing_evidence_ids.append(item.id)

    if not missing:
        return [
            DeterministicCheck(
                id="check_figure_table_reference_001",
                kind="figure_table_reference",
                applicability=DeterministicCheckApplicability.APPLICABLE,
                status=DeterministicCheckStatus.PASS,
                evidence_ids=[],
                message="All explicit Figure/Table references found matching parsed figure/table evidence.",
                benign_explanations=["parser may normalize labels differently"],
                requires_human_review=False,
                metadata={"checked_references": len(references)},
            )
        ]

    return [
        DeterministicCheck(
            id="check_figure_table_reference_001",
            kind="figure_table_reference",
            applicability=DeterministicCheckApplicability.APPLICABLE,
            status=DeterministicCheckStatus.LEAD,
            evidence_ids=missing_evidence_ids,
            message=(
                "Referenced Figure/Table labels were not found in parsed figure/table evidence: "
                + ", ".join(missing)
                + "."
            ),
            benign_explanations=[
                "parser missed a caption",
                "supplementary material reference",
                "label formatting changed",
            ],
            metadata={"missing_references": missing},
        )
    ]


def _available_reference_numbers(ledger: EvidenceLedger) -> set[str]:
    numbers: set[str] = set()
    for item in ledger.items:
        if item.type is not EvidenceType.REFERENCE:
            continue
        metadata_number = str(item.metadata.get("reference_number") or "").strip()
        if metadata_number:
            numbers.add(metadata_number)
            continue
        match = REFERENCE_NUMBER_RE.search(item.text)
        if match:
            numbers.add(match.group("num"))
    return numbers


def _referenced_citation_numbers(ledger: EvidenceLedger) -> list[tuple[str, EvidenceItem]]:
    references: list[tuple[str, EvidenceItem]] = []
    for item in ledger.items:
        if item.type is not EvidenceType.TEXT_SPAN:
            continue
        for match in CITATION_BRACKET_RE.finditer(item.text):
            numbers = [part.strip() for part in match.group("body").split(",")]
            references.extend((number, item) for number in numbers if number)
    return references


def _numbered_citation_reference_checks(ledger: EvidenceLedger) -> list[DeterministicCheck]:
    citations = _referenced_citation_numbers(ledger)
    if not citations:
        return [
            DeterministicCheck(
                id="check_numbered_citation_reference_000",
                kind="numbered_citation_reference",
                applicability=DeterministicCheckApplicability.INSUFFICIENT_EVIDENCE,
                status=DeterministicCheckStatus.INCONCLUSIVE,
                evidence_ids=[],
                message="No standard numbered citation brackets were available for deterministic checking.",
                benign_explanations=["paper may use author-year citations or parser may split citation tokens"],
                requires_human_review=False,
            )
        ]

    available = _available_reference_numbers(ledger)
    if not available:
        return [
            DeterministicCheck(
                id="check_numbered_citation_reference_001",
                kind="numbered_citation_reference",
                applicability=DeterministicCheckApplicability.PARSER_UNCERTAIN,
                status=DeterministicCheckStatus.INCONCLUSIVE,
                evidence_ids=[item.id for _number, item in citations][:1],
                message="Numbered citations were found, but parsed reference entries were unavailable.",
                benign_explanations=["reference section parser missing", "references may be in supplementary material"],
            )
        ]

    missing: list[str] = []
    missing_evidence_ids: list[str] = []
    for number, item in citations:
        if number in available:
            continue
        label = f"[{number}]"
        if label not in missing:
            missing.append(label)
        if item.id not in missing_evidence_ids:
            missing_evidence_ids.append(item.id)

    if not missing:
        return [
            DeterministicCheck(
                id="check_numbered_citation_reference_001",
                kind="numbered_citation_reference",
                applicability=DeterministicCheckApplicability.APPLICABLE,
                status=DeterministicCheckStatus.PASS,
                evidence_ids=[],
                message="All standard numbered citations found matching parsed reference entries.",
                benign_explanations=["parser may normalize references differently"],
                requires_human_review=False,
                metadata={"checked_citations": len(citations)},
            )
        ]

    return [
        DeterministicCheck(
            id="check_numbered_citation_reference_001",
            kind="numbered_citation_reference",
            applicability=DeterministicCheckApplicability.APPLICABLE,
            status=DeterministicCheckStatus.LEAD,
            evidence_ids=missing_evidence_ids,
            message="Numbered citations were not found in parsed references: " + ", ".join(missing) + ".",
            benign_explanations=["parser missed a reference entry", "citation points to supplementary references"],
            metadata={"missing_references": missing},
        )
    ]


def _significance_legend(ledger: EvidenceLedger) -> tuple[dict[str, float], list[str]]:
    thresholds: dict[str, float] = {}
    evidence_ids: list[str] = []
    for item in ledger.items:
        for match in SIGNIFICANCE_LEGEND_RE.finditer(item.text):
            stars = match.group("stars")
            thresholds[stars] = float(match.group("threshold"))
            if item.id not in evidence_ids:
                evidence_ids.append(item.id)
    return thresholds, evidence_ids


def _significance_star_consistency_checks(ledger: EvidenceLedger) -> list[DeterministicCheck]:
    thresholds, legend_evidence_ids = _significance_legend(ledger)
    if not thresholds:
        return [
            DeterministicCheck(
                id="check_significance_star_consistency_000",
                kind="significance_star_consistency",
                applicability=DeterministicCheckApplicability.INSUFFICIENT_EVIDENCE,
                status=DeterministicCheckStatus.INCONCLUSIVE,
                evidence_ids=[],
                message="No explicit significance-star legend was available for deterministic checking.",
                benign_explanations=["paper may use a nonstandard or implicit significance convention"],
                requires_human_review=False,
            )
        ]

    checked = 0
    mismatches: list[dict[str, object]] = []
    mismatch_evidence_ids: list[str] = []
    for item in _checkable_items(ledger):
        for match in SIGNIFICANCE_VALUE_RE.finditer(item.text):
            stars = match.group("stars")
            threshold = thresholds.get(stars)
            if threshold is None:
                continue
            checked += 1
            pvalue = float(match.group("pvalue"))
            if pvalue <= threshold:
                continue
            mismatches.append(
                {
                    "evidence_id": item.id,
                    "stars": stars,
                    "p_value": pvalue,
                    "threshold": threshold,
                    "reported_value": float(match.group("value")),
                }
            )
            if item.id not in mismatch_evidence_ids:
                mismatch_evidence_ids.append(item.id)

    if checked == 0:
        return [
            DeterministicCheck(
                id="check_significance_star_consistency_001",
                kind="significance_star_consistency",
                applicability=DeterministicCheckApplicability.INSUFFICIENT_EVIDENCE,
                status=DeterministicCheckStatus.INCONCLUSIVE,
                evidence_ids=legend_evidence_ids[:1],
                message="A significance-star legend was found, but no exact p-value plus star pairs were available.",
                benign_explanations=["paper may report stars without exact p-values"],
                requires_human_review=False,
            )
        ]

    if not mismatches:
        return [
            DeterministicCheck(
                id="check_significance_star_consistency_001",
                kind="significance_star_consistency",
                applicability=DeterministicCheckApplicability.APPLICABLE,
                status=DeterministicCheckStatus.PASS,
                evidence_ids=[],
                message="All exact p-value plus significance-star pairs match the parsed legend thresholds.",
                benign_explanations=["threshold convention explicitly parsed from the paper"],
                requires_human_review=False,
                metadata={"checked_pairs": checked, "thresholds": thresholds},
            )
        ]

    return [
        DeterministicCheck(
            id="check_significance_star_consistency_001",
            kind="significance_star_consistency",
            applicability=DeterministicCheckApplicability.APPLICABLE,
            status=DeterministicCheckStatus.LEAD,
            evidence_ids=[*mismatch_evidence_ids, *legend_evidence_ids],
            message="Significance stars do not match exact p-values under the paper's own legend.",
            benign_explanations=[
                "table transcription issue",
                "star legend differs for this table",
                "p-value was rounded from a more precise value",
            ],
            metadata={"checked_pairs": checked, "thresholds": thresholds, "mismatches": mismatches},
        )
    ]


def run_deterministic_checks(ledger: EvidenceLedger) -> list[DeterministicCheck]:
    return [
        *_percentage_consistency_checks(ledger),
        *_figure_table_reference_checks(ledger),
        *_numbered_citation_reference_checks(ledger),
        *_significance_star_consistency_checks(ledger),
    ]

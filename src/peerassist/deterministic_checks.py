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


def run_deterministic_checks(ledger: EvidenceLedger) -> list[DeterministicCheck]:
    return [*_percentage_consistency_checks(ledger)]

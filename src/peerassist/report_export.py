"""Export confirmation-aware PeerAssist reports."""

from __future__ import annotations

import re
from typing import Any

from schemas.peerassist import Concern, ConcernStatus

CONFIRMED_STATUSES = {
    ConcernStatus.CONFIRMED,
    ConcernStatus.DOWNGRADED,
    ConcernStatus.REWRITTEN,
}

ACCUSATORY_RE = re.compile(
    r"\b(fraud|fabricated|misconduct|definitely fake|reject automatically|automatic reject|"
    r"accept automatically|automatic accept)\b",
    re.I,
)


def _guard_review_language(concerns: list[Concern]) -> None:
    for concern in concerns:
        text = " ".join([concern.title, concern.impact, concern.benign_explanation, concern.author_action])
        if ACCUSATORY_RE.search(text):
            raise ValueError(
                f"accusatory or automatic-decision language is not allowed in concern {concern.id}"
            )


def _evidence_text(concern: Concern, evidence_lookup: dict[str, str]) -> str:
    if not concern.evidence_ids:
        return "Evidence: pending manual localization"
    locations = [evidence_lookup.get(eid, eid) for eid in concern.evidence_ids]
    return "Evidence: " + "; ".join(locations)


def _concern_block(concern: Concern, evidence_lookup: dict[str, str]) -> list[str]:
    return [
        f"### {concern.title}",
        "",
        f"- Level: `{concern.level.value}`",
        f"- Category: `{concern.category}`",
        f"- {_evidence_text(concern, evidence_lookup)}",
        f"- Why it matters: {concern.impact or 'Pending reviewer assessment.'}",
        f"- Possible benign explanation: {concern.benign_explanation or 'Not yet assessed.'}",
        f"- Suggested author action: {concern.author_action or 'Please clarify in revision.'}",
        "",
    ]


def export_peerassist_report(
    *,
    paper_id: str,
    concerns: list[Concern],
    evidence_lookup: dict[str, str] | None = None,
) -> tuple[str, dict[str, Any]]:
    evidence_lookup = evidence_lookup or {}
    active = [concern for concern in concerns if concern.status is not ConcernStatus.DELETED]
    _guard_review_language(active)

    confirmed = [concern for concern in active if concern.status in CONFIRMED_STATUSES]
    pending = [concern for concern in active if concern.status is ConcernStatus.PENDING_HUMAN_CONFIRMATION]

    lines: list[str] = [
        "# PeerAssist Review Aid Report",
        "",
        f"Paper ID: `{paper_id}`",
        "",
        "## Paper Summary",
        "",
        "Pending integration with the parsed paper summary.",
        "",
        "## Confirmed Review Concerns",
        "",
    ]
    if confirmed:
        for concern in confirmed:
            lines.extend(_concern_block(concern, evidence_lookup))
    else:
        lines.extend(["No confirmed concerns yet.", ""])

    lines.extend(["## Pending Manual Checks", ""])
    if pending:
        for concern in pending:
            lines.extend(_concern_block(concern, evidence_lookup))
    else:
        lines.extend(["No pending manual checks.", ""])

    lines.extend(
        [
            "## System Limitations",
            "",
            "- PeerAssist provides review leads and editable comments, not misconduct findings.",
            "- Parser coverage and missing evidence can limit deterministic checks.",
            "- Final judgment remains with the human reviewer.",
            "",
            "## Provenance Appendix",
            "",
            f"- Confirmed concerns: `{len(confirmed)}`",
            f"- Pending manual checks: `{len(pending)}`",
        ]
    )
    payload: dict[str, Any] = {
        "schema_version": "peerassist.report.v1",
        "paper_id": paper_id,
        "confirmed_count": len(confirmed),
        "pending_count": len(pending),
        "concerns": [concern.model_dump(mode="json") for concern in active],
    }
    return "\n".join(lines).rstrip() + "\n", payload

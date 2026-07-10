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

REPORT_COPY = {
    "en": {
        "title": "# PeerAssist Review Aid Report",
        "paper_id": "Paper ID",
        "summary": "## Paper Summary",
        "summary_placeholder": "Pending integration with the parsed paper summary.",
        "confirmed": "## Confirmed Review Concerns",
        "no_confirmed": "No confirmed concerns yet.",
        "pending": "## Pending Manual Checks",
        "no_pending": "No pending manual checks.",
        "level": "Level",
        "category": "Category",
        "evidence": "Evidence",
        "pending_evidence": "pending manual localization",
        "why": "Why it matters",
        "why_placeholder": "Pending reviewer assessment.",
        "benign": "Possible benign explanation",
        "benign_placeholder": "Not yet assessed.",
        "action": "Suggested author action",
        "action_placeholder": "Please clarify in revision.",
        "limitations": "## System Limitations",
        "limitation_items": [
            "PeerAssist provides review leads and editable comments, not misconduct findings.",
            "Parser coverage and missing evidence can limit deterministic checks.",
            "Final judgment remains with the human reviewer.",
        ],
        "provenance": "## Provenance Appendix",
        "confirmed_count": "Confirmed concerns",
        "pending_count": "Pending manual checks",
    },
    "zh": {
        "title": "# PeerAssist 论文审核辅助报告",
        "paper_id": "论文 ID",
        "summary": "## 论文概要",
        "summary_placeholder": "等待接入解析后的论文概要。",
        "confirmed": "## 已确认的审稿意见",
        "no_confirmed": "暂无已确认意见。",
        "pending": "## 待人工确认的检查项",
        "no_pending": "暂无待人工确认检查项。",
        "level": "级别",
        "category": "类别",
        "evidence": "证据",
        "pending_evidence": "待人工定位",
        "why": "影响说明",
        "why_placeholder": "待审稿人评估。",
        "benign": "可能的善意解释",
        "benign_placeholder": "尚未评估。",
        "action": "建议作者操作",
        "action_placeholder": "请在修改稿中澄清。",
        "limitations": "## 系统限制",
        "limitation_items": [
            "PeerAssist 只提供审稿线索和可编辑意见，不作学术不端判断。",
            "解析覆盖率和缺失证据会限制确定性核查。",
            "最终判断仍由人工审稿人作出。",
        ],
        "provenance": "## 溯源附录",
        "confirmed_count": "已确认意见",
        "pending_count": "待人工确认检查项",
    },
}


def _guard_review_language(concerns: list[Concern]) -> None:
    for concern in concerns:
        text = " ".join([concern.title, concern.impact, concern.benign_explanation, concern.author_action])
        if ACCUSATORY_RE.search(text):
            raise ValueError(
                f"accusatory or automatic-decision language is not allowed in concern {concern.id}"
            )


def _copy(language: str) -> dict[str, object]:
    return REPORT_COPY.get(language, REPORT_COPY["en"])


def _evidence_text(concern: Concern, evidence_lookup: dict[str, str], copy: dict[str, object]) -> str:
    separator = "：" if copy["evidence"] == "证据" else ": "
    if not concern.evidence_ids:
        return f"{copy['evidence']}{separator}{copy['pending_evidence']}"
    locations = [evidence_lookup.get(eid, eid) for eid in concern.evidence_ids]
    return f"{copy['evidence']}{separator}" + "; ".join(locations)


def _concern_block(
    concern: Concern, evidence_lookup: dict[str, str], copy: dict[str, object]
) -> list[str]:
    return [
        f"### {concern.title}",
        "",
        f"- {copy['level']}: `{concern.level.value}`",
        f"- {copy['category']}: `{concern.category}`",
        f"- {_evidence_text(concern, evidence_lookup, copy)}",
        f"- {copy['why']}: {concern.impact or copy['why_placeholder']}",
        f"- {copy['benign']}: {concern.benign_explanation or copy['benign_placeholder']}",
        f"- {copy['action']}: {concern.author_action or copy['action_placeholder']}",
        "",
    ]


def export_peerassist_report(
    *,
    paper_id: str,
    concerns: list[Concern],
    evidence_lookup: dict[str, str] | None = None,
    language: str = "en",
) -> tuple[str, dict[str, Any]]:
    evidence_lookup = evidence_lookup or {}
    normalized_language = str(language or "en").strip().lower()
    copy = _copy(normalized_language)
    if normalized_language not in REPORT_COPY:
        normalized_language = "en"
    active = [concern for concern in concerns if concern.status is not ConcernStatus.DELETED]
    _guard_review_language(active)

    confirmed = [concern for concern in active if concern.status in CONFIRMED_STATUSES]
    pending = [concern for concern in active if concern.status is ConcernStatus.PENDING_HUMAN_CONFIRMATION]

    lines: list[str] = [
        str(copy["title"]),
        "",
        f"{copy['paper_id']}: `{paper_id}`",
        "",
        str(copy["summary"]),
        "",
        str(copy["summary_placeholder"]),
        "",
        str(copy["confirmed"]),
        "",
    ]
    if confirmed:
        for concern in confirmed:
            lines.extend(_concern_block(concern, evidence_lookup, copy))
    else:
        lines.extend([str(copy["no_confirmed"]), ""])

    lines.extend([str(copy["pending"]), ""])
    if pending:
        for concern in pending:
            lines.extend(_concern_block(concern, evidence_lookup, copy))
    else:
        lines.extend([str(copy["no_pending"]), ""])

    lines.extend(
        [
            str(copy["limitations"]),
            "",
            *[f"- {item}" for item in copy["limitation_items"]],
            "",
            str(copy["provenance"]),
            "",
            f"- {copy['confirmed_count']}: `{len(confirmed)}`",
            f"- {copy['pending_count']}: `{len(pending)}`",
        ]
    )
    payload: dict[str, Any] = {
        "schema_version": "peerassist.report.v1",
        "paper_id": paper_id,
        "language": normalized_language,
        "confirmed_count": len(confirmed),
        "pending_count": len(pending),
        "concerns": [concern.model_dump(mode="json") for concern in active],
    }
    return "\n".join(lines).rstrip() + "\n", payload

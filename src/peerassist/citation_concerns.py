"""Convert citation-audit findings into conservative human review concerns."""

from __future__ import annotations

from schemas.citation import (
    CitationAudit,
    CitationAuditFinding,
    CitationFindingStatus,
)
from schemas.peerassist import Concern, ConcernLevel, ConcernStatus

_CONCERN_DETAILS: dict[CitationFindingStatus, tuple[ConcernLevel, str, str, str, str]] = {
    CitationFindingStatus.METADATA_MISMATCH: (
        ConcernLevel.CLARIFICATION_NEEDED,
        "引文元数据需要核对",
        "文稿中的引文信息与已核验记录存在差异，可能影响读者定位相关来源。",
        "该差异也可能来自格式化、转录或解析方式。",
        "请核对该引文的书目信息，并在需要时说明或更正。",
    ),
    CitationFindingStatus.MISSING_REFERENCE: (
        ConcernLevel.CLARIFICATION_NEEDED,
        "文内引文缺少对应参考文献",
        "该文内引文目前未关联到解析出的参考文献记录，读者可能无法定位来源。",
        "参考文献条目也可能因版式或解析限制而未被识别。",
        "请核对文内引文与参考文献列表的对应关系。",
    ),
    CitationFindingStatus.NOT_FOUND: (
        ConcernLevel.CLARIFICATION_NEEDED,
        "引文外部检索未找到匹配记录",
        "当前核验来源未找到唯一匹配记录，读者可能难以核对书目信息。",
        "检索范围、索引覆盖或元数据格式可能影响该结果。",
        "请核对引文标识和书目信息，必要时补充可核对的信息。",
    ),
    CitationFindingStatus.AMBIGUOUS: (
        ConcernLevel.CLARIFICATION_NEEDED,
        "引文匹配结果需要澄清",
        "当前核验无法确定唯一的对应记录，可能影响来源定位。",
        "多个候选记录或相近的书目信息可能造成这种情况。",
        "请提供足以区分该参考文献的书目信息。",
    ),
    CitationFindingStatus.INSUFFICIENT_EVIDENCE: (
        ConcernLevel.EDITOR_NOTE,
        "引文核验信息不足",
        "现有证据不足以完成该引文的核验，建议在编辑核对时保留该记录。",
        "这可能与可用元数据或核验服务范围有关。",
        "请在后续核对中确认是否需要补充书目信息。",
    ),
    CitationFindingStatus.VERIFICATION_FAILED: (
        ConcernLevel.EDITOR_NOTE,
        "引文核验未完成",
        "该引文的核验过程未能完成，当前无法据此得出书目信息结论。",
        "工具、服务或响应格式问题可能导致核验未完成。",
        "请在条件允许时重新核对该引文的书目信息。",
    ),
    CitationFindingStatus.UNCITED_REFERENCE: (
        ConcernLevel.MINOR_CONCERN,
        "参考文献未见文内关联",
        "该参考文献记录目前未见对应的文内引文，可能影响参考文献列表的完整性。",
        "文内引文也可能因解析范围或格式而未被识别。",
        "请核对该条目是否需要在文内关联或从列表中移除。",
    ),
    CitationFindingStatus.MALFORMED_REFERENCE: (
        ConcernLevel.EDITOR_NOTE,
        "参考文献格式需要核对",
        "该参考文献记录的格式可能不完整，建议在编辑核对时确认。",
        "版式、导出或解析限制可能影响记录形式。",
        "请核对该参考文献的格式和可读性。",
    ),
    CitationFindingStatus.DUPLICATE_REFERENCE_METADATA: (
        ConcernLevel.EDITOR_NOTE,
        "参考文献元数据可能重复",
        "多个参考文献记录的元数据可能重复，建议在编辑核对时确认。",
        "不同条目也可能因信息不完整而呈现相同元数据。",
        "请核对这些参考文献记录是否需要区分或合并。",
    ),
}


def concerns_from_citation_audit(audit: CitationAudit, *, source_agent_id: str) -> list[Concern]:
    """Return the only deterministic concern representation of audit findings."""
    verification_by_id = {verification.id: verification for verification in audit.verifications}
    concerns: list[Concern] = []
    for finding in audit.findings:
        if finding.status is CitationFindingStatus.VERIFIED:
            continue
        details = _CONCERN_DETAILS.get(finding.status)
        if details is None:
            continue
        level, title, impact, benign_explanation, author_action = details
        concerns.append(
            Concern(
                id=f"concern_citation_{finding.id}",
                level=level,
                category="citation",
                title=title,
                evidence_ids=_evidence_ids_from_finding(finding),
                impact=impact,
                benign_explanation=benign_explanation,
                author_action=author_action,
                status=ConcernStatus.PENDING_HUMAN_CONFIRMATION,
                source_agent_ids=[source_agent_id],
                metadata=_metadata_for_finding(audit, finding, verification_by_id),
            )
        )
    if len({concern.id for concern in concerns}) != len(concerns):
        raise ValueError("citation audit contains duplicate concern identifiers")
    return concerns


def _evidence_ids_from_finding(finding: CitationAuditFinding) -> list[str]:
    return list(dict.fromkeys([*finding.mention_evidence_ids, *finding.reference_evidence_ids]))


def _metadata_for_finding(
    audit: CitationAudit,
    finding: CitationAuditFinding,
    verification_by_id: dict[str, object],
) -> dict[str, object]:
    artifacts: list[dict[str, str]] = []
    error_codes: list[str] = []
    for verification_id in finding.verification_ids:
        verification = verification_by_id.get(verification_id)
        if verification is None:
            continue
        artifact = verification.raw_response_artifact
        if artifact is not None:
            artifacts.append({"path": artifact.path, "sha256": artifact.sha256})
        if verification.error_code:
            error_codes.append(verification.error_code)
    metadata: dict[str, object] = {
        "citation_finding_ids": [finding.id],
        "citation_link_ids": list(finding.citation_link_ids),
        "reference_record_ids": list(finding.reference_record_ids),
        "verification_ids": list(finding.verification_ids),
        "audit_schema_version": audit.schema_version,
        "audit_parse_version": audit.parse_version,
    }
    if artifacts:
        metadata["raw_response_artifacts"] = artifacts
    if error_codes:
        metadata["error_code"] = error_codes[0]
    return metadata

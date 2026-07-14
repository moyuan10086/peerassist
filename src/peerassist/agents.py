"""Deterministic local PeerAssist agent runner.

The first runner is intentionally local and reproducible. It emits the same
typed artifacts an LLM/MCP-backed runner will later produce, so downstream
confirmation and export code can remain stable.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from peerassist.citation_concerns import concerns_from_citation_audit
from peerassist.concerns import concern_from_agent_draft
from peerassist.review_context import build_review_context
from schemas.citation import CitationAudit
from schemas.peerassist import (
    AgentConcernDraft,
    AgentInputPacket,
    AgentReviewResult,
    AgentRunStatus,
    Concern,
    ConcernLevel,
    ConcernStatus,
    DeterministicCheck,
    DeterministicCheckStatus,
    EvidenceLedger,
    EvidenceType,
    FindingImportance,
    PaperUnderstandingArtifacts,
)

PROFESSIONAL_AGENT_IDS = (
    "structure_agent",
    "methodology_agent",
    "experiment_agent",
    "statistics_agent",
    "citation_agent",
    "ethics_agent",
    "reproducibility_agent",
)
FAST_AGENT_IDS = (*PROFESSIONAL_AGENT_IDS, "figure_table_agent", "defense_agent", "integrator_agent")
STANDARD_EXTRA_AGENT_IDS = ("novelty_agent",)
DEEP_EXTRA_AGENT_IDS = STANDARD_EXTRA_AGENT_IDS
AGENT_RESPONSIBILITIES = {
    "structure_agent": "研究问题、贡献、论证结构与结论边界",
    "methodology_agent": "方法输入输出、关键假设、适用条件与主张支撑",
    "experiment_agent": "数据、基线、指标、消融、图表与实验覆盖",
    "statistics_agent": "样本量、不确定性、显著性、效应量与统计报告",
    "citation_agent": "正文引用、参考文献字段、来源核验与主张支持关系",
    "ethics_agent": "参与者、数据来源、隐私、安全、偏差与伦理披露",
    "reproducibility_agent": "代码、数据、配置、随机性、依赖与复现实验条件",
    "figure_table_agent": "图表引用、标签与正文一致性",
    "defense_agent": "对候选问题进行善意解释和反方压力测试",
    "integrator_agent": "合并重复发现并保留代理来源",
}
ModelEnhancer = Callable[[dict[str, Any]], dict[str, Any]]


def build_agent_input_packet(
    *,
    agent_id: str,
    mode: str,
    ledger: EvidenceLedger,
    checks: list[DeterministicCheck],
    capability_names: list[str] | None = None,
) -> AgentInputPacket:
    evidence_ids = [item.id for item in ledger.items]
    check_ids = [check.id for check in checks]
    return AgentInputPacket(
        agent_id=agent_id,
        mode=mode,
        evidence_ids=evidence_ids,
        check_ids=check_ids,
        capability_names=list(capability_names or []),
        metadata={"paper_id": ledger.paper_id},
    )


def _draft_for_check(check: DeterministicCheck) -> AgentConcernDraft:
    return AgentConcernDraft(
        id=check.id,
        level=ConcernLevel.CLARIFICATION_NEEDED,
        category=_category_for_check_kind(check.kind),
        title=_title_for_check(check),
        evidence_ids=list(check.evidence_ids),
        source_check_ids=[check.id],
        impact="This may affect whether the reported result supports the paper's claim.",
        benign_explanation="; ".join(check.benign_explanations),
        author_action=(
            "Please clarify the calculation basis and provide enough detail for readers to reproduce it."
        ),
        metadata={
            "check_message": check.message,
            "producer_namespace": "deterministic_agent",
            "producer_version": "v1",
            "issue_anchor": check.id,
            "finding_check_type": check.kind,
            "finding_semantic_key": check.kind,
        },
    )


def _category_for_check_kind(kind: str) -> str:
    if any(token in kind for token in ("percentage", "stat", "mean", "significance", "p_value")):
        return "statistics"
    if "figure" in kind or "table" in kind:
        return "figure_table"
    if "reference" in kind or "citation" in kind:
        return "citation"
    return "other"


def _title_for_check(check: DeterministicCheck) -> str:
    if check.kind == "percentage_consistency":
        return "Reported percentage needs clarification"
    if check.kind == "significance_star_consistency":
        return "Significance stars need clarification"
    return check.kind.replace("_", " ").title()


def _lead_checks(checks: list[DeterministicCheck]) -> list[DeterministicCheck]:
    return [check for check in checks if check.status is DeterministicCheckStatus.LEAD]


def _lead_checks_for_category(checks: list[DeterministicCheck], category: str) -> list[DeterministicCheck]:
    return [
        check
        for check in _lead_checks(checks)
        if _category_for_check_kind(check.kind) == category
    ]


def _metadata(agent_id: str, evidence_ids: list[str]) -> dict[str, Any]:
    return {
        "responsibility": AGENT_RESPONSIBILITIES[agent_id],
        "evidence_count": len(evidence_ids),
        "evidence_ids": evidence_ids,
        "review_engine": "local_evidence_rules",
        "model_status": "not_requested",
    }


def _statistics_agent(
    checks: list[DeterministicCheck], evidence_ids: list[str] | None = None
) -> AgentReviewResult:
    drafts = [_draft_for_check(check) for check in _lead_checks_for_category(checks, "statistics")]
    return AgentReviewResult(
        agent_id="statistics_agent",
        status=AgentRunStatus.COMPLETED,
        drafts=drafts,
        metadata={**_metadata("statistics_agent", list(evidence_ids or [])), "lead_count": len(drafts)},
    )


def _figure_table_agent(
    checks: list[DeterministicCheck], evidence_ids: list[str] | None = None
) -> AgentReviewResult:
    drafts = [_draft_for_check(check) for check in _lead_checks_for_category(checks, "figure_table")]
    return AgentReviewResult(
        agent_id="figure_table_agent",
        status=AgentRunStatus.COMPLETED,
        drafts=drafts,
        warnings=[] if drafts else ["No figure/table deterministic leads found."],
        metadata={**_metadata("figure_table_agent", list(evidence_ids or [])), "lead_count": len(drafts)},
    )


def _defense_agent(checks: list[DeterministicCheck]) -> AgentReviewResult:
    pressure_tests: list[str] = []
    for check in _lead_checks(checks):
        explanations = "; ".join(check.benign_explanations) or "no benign explanation recorded"
        pressure_tests.append(f"{check.id}: pressure-test against {explanations}")
    return AgentReviewResult(
        agent_id="defense_agent",
        status=AgentRunStatus.COMPLETED,
        warnings=[] if pressure_tests else ["No deterministic leads to pressure-test."],
        metadata={**_metadata("defense_agent", []), "pressure_tests": pressure_tests},
    )


def _integrator_agent(statistics_result: AgentReviewResult) -> AgentReviewResult:
    return AgentReviewResult(
        agent_id="integrator_agent",
        status=AgentRunStatus.COMPLETED,
        drafts=[draft.model_copy(deep=True) for draft in statistics_result.drafts],
        metadata={
            **_metadata("integrator_agent", list(statistics_result.metadata.get("evidence_ids") or [])),
            "integrated_from": ["statistics_agent", "defense_agent"],
        },
    )


def _citation_agent(evidence_ids: list[str] | None = None) -> AgentReviewResult:
    return AgentReviewResult(
        agent_id="citation_agent",
        status=AgentRunStatus.COMPLETED,
        metadata={
            **_metadata("citation_agent", list(evidence_ids or [])),
            "source": "citation_audit",
            "draft_count": 0,
        },
    )


def _assignment_evidence_ids(
    understanding: PaperUnderstandingArtifacts | None,
    assignment_id: str,
) -> list[str]:
    if understanding is None:
        return []
    for assignment in understanding.review_plan.agent_assignments:
        if assignment.agent_id == assignment_id:
            return list(dict.fromkeys(assignment.evidence_ids))
    return []


def _section_evidence_ids(ledger: EvidenceLedger, *tokens: str) -> list[str]:
    return [
        item.id
        for item in ledger.items
        if any(token in item.section.lower() for token in tokens)
    ]


def _professional_evidence(
    ledger: EvidenceLedger,
    understanding: PaperUnderstandingArtifacts | None,
) -> dict[str, list[str]]:
    mapping = {
        "structure_agent": _assignment_evidence_ids(understanding, "structure"),
        "methodology_agent": _assignment_evidence_ids(understanding, "method"),
        "experiment_agent": _assignment_evidence_ids(understanding, "experiment"),
        "statistics_agent": _assignment_evidence_ids(understanding, "statistics"),
        "citation_agent": [
            item.id
            for item in ledger.items
            if item.type in {EvidenceType.CITATION, EvidenceType.REFERENCE}
        ],
        "ethics_agent": _section_evidence_ids(
            ledger, "ethic", "participant", "privacy", "safety", "data statement"
        ),
        "reproducibility_agent": _section_evidence_ids(
            ledger, "method", "implementation", "experiment", "appendix", "reproduc"
        ),
    }
    if understanding is not None:
        profile = understanding.profile
        if not mapping["structure_agent"]:
            mapping["structure_agent"] = list(
                dict.fromkeys(
                    [
                        *profile.abstract.evidence_ids,
                        *profile.research_question.evidence_ids,
                        *profile.contributions.evidence_ids,
                        *profile.conclusions.evidence_ids,
                    ]
                )
            )
        if not mapping["reproducibility_agent"]:
            mapping["reproducibility_agent"] = list(
                dict.fromkeys(
                    [
                        *profile.method_inputs.evidence_ids,
                        *profile.method_outputs.evidence_ids,
                        *profile.method_assumptions.evidence_ids,
                    ]
                )
            )
    return mapping


def _gap_draft(
    *, agent_id: str, category: str, title: str, evidence_ids: list[str], impact: str, action: str
) -> AgentConcernDraft:
    digest = hashlib.sha256(f"{agent_id}:{title}".encode()).hexdigest()[:12]
    return AgentConcernDraft(
        id=f"{agent_id}_gap_{digest}",
        level=ConcernLevel.CLARIFICATION_NEEDED,
        category=category,
        title=title,
        evidence_ids=evidence_ids,
        impact=impact,
        benign_explanation="相关信息可能分散在正文、附录或代码仓库中，解析器也可能未完整识别。",
        author_action=action,
        metadata={
            "producer_namespace": agent_id,
            "producer_version": "v2",
            "issue_anchor": title,
            "finding_check_type": f"{category}_coverage_gap",
            "finding_semantic_key": title,
        },
    )


def _local_professional_results(
    *,
    ledger: EvidenceLedger,
    understanding: PaperUnderstandingArtifacts | None,
    checks: list[DeterministicCheck],
) -> list[AgentReviewResult]:
    evidence = _professional_evidence(ledger, understanding)
    structure_drafts: list[AgentConcernDraft] = []
    methodology_drafts: list[AgentConcernDraft] = []
    experiment_drafts: list[AgentConcernDraft] = []
    if understanding is not None:
        profile = understanding.profile
        if profile.research_question.value in (None, "", [], {}) and evidence["structure_agent"]:
            structure_drafts.append(
                _gap_draft(
                    agent_id="structure_agent",
                    category="structure",
                    title="核心研究问题需要更明确地界定",
                    evidence_ids=evidence["structure_agent"][:12],
                    impact="研究问题不明确会削弱贡献、方法和实验之间的可审查对应关系。",
                    action="请在摘要或引言中明确研究问题、评价对象和结论适用边界。",
                )
            )
        if profile.method_assumptions.value in (None, "", [], {}) and evidence["methodology_agent"]:
            methodology_drafts.append(
                _gap_draft(
                    agent_id="methodology_agent",
                    category="methodology",
                    title="关键方法假设需要集中说明",
                    evidence_ids=evidence["methodology_agent"][:12],
                    impact="未明确的假设会影响方法有效性、适用范围和实验解释。",
                    action="请列出关键统计或数据假设，并说明违反假设时的影响与处理方式。",
                )
            )
        for experiment in understanding.experiment_inventory.experiments[:1]:
            missing = [
                label
                for label, field in (
                    ("基线", experiment.baselines),
                    ("评价指标", experiment.metrics),
                    ("统计报告", experiment.statistics),
                )
                if field.value in (None, "", [], {})
            ]
            experiment_ids = list(
                dict.fromkeys(
                    evidence["experiment_agent"]
                    or [
                        evidence_id
                        for field in experiment.grounded_fields()
                        for evidence_id in field.evidence_ids
                    ]
                )
            )
            if missing and experiment_ids:
                experiment_drafts.append(
                    _gap_draft(
                        agent_id="experiment_agent",
                        category="experiment",
                        title=f"实验报告尚需核对：{'、'.join(missing)}",
                        evidence_ids=experiment_ids[:12],
                        impact="缺失或分散的实验要素会降低结果比较和复核的可靠性。",
                        action="请集中补充这些实验要素；若不适用，请明确说明原因。",
                    )
                )

    statistics_result = _statistics_agent(checks, evidence["statistics_agent"])
    results = [
        statistics_result,
        AgentReviewResult(
            agent_id="structure_agent",
            status=AgentRunStatus.COMPLETED,
            drafts=structure_drafts,
            metadata=_metadata("structure_agent", evidence["structure_agent"]),
        ),
        AgentReviewResult(
            agent_id="methodology_agent",
            status=AgentRunStatus.COMPLETED,
            drafts=methodology_drafts,
            metadata=_metadata("methodology_agent", evidence["methodology_agent"]),
        ),
        AgentReviewResult(
            agent_id="experiment_agent",
            status=AgentRunStatus.COMPLETED,
            drafts=experiment_drafts,
            metadata=_metadata("experiment_agent", evidence["experiment_agent"]),
        ),
        _citation_agent(evidence["citation_agent"]),
        AgentReviewResult(
            agent_id="ethics_agent",
            status=AgentRunStatus.COMPLETED,
            metadata=_metadata("ethics_agent", evidence["ethics_agent"]),
        ),
        AgentReviewResult(
            agent_id="reproducibility_agent",
            status=AgentRunStatus.COMPLETED,
            metadata=_metadata("reproducibility_agent", evidence["reproducibility_agent"]),
        ),
    ]
    return results


def _model_draft(row: dict[str, Any], allowed_evidence_ids: set[str]) -> AgentConcernDraft | None:
    evidence_ids = [str(value) for value in row.get("evidence_ids", []) if str(value).strip()]
    if not evidence_ids or any(evidence_id not in allowed_evidence_ids for evidence_id in evidence_ids):
        return None
    title = str(row.get("title") or "").strip()
    impact = str(row.get("impact") or "").strip()
    action = str(row.get("author_action") or "").strip()
    if not title or not impact or not action:
        return None
    agent_id = str(row.get("agent_id") or "")
    if agent_id not in PROFESSIONAL_AGENT_IDS:
        return None
    try:
        level = ConcernLevel(str(row.get("level") or ""))
    except ValueError:
        level = ConcernLevel.CLARIFICATION_NEEDED
    digest = hashlib.sha256(
        f"{agent_id}:{title}:{','.join(evidence_ids)}".encode()
    ).hexdigest()[:16]
    return AgentConcernDraft(
        id=f"model_{agent_id}_{digest}",
        level=level,
        category=str(row.get("category") or agent_id.removesuffix("_agent")),
        title=title,
        evidence_ids=evidence_ids,
        impact=impact,
        benign_explanation=str(row.get("benign_explanation") or "需由审稿人结合全文复核。"),
        author_action=action,
        importance=(
            FindingImportance.CORE
            if level is ConcernLevel.MAJOR_CONCERN
            else FindingImportance.SUPPORTING
        ),
        metadata={
            "producer_namespace": agent_id,
            "producer_version": "model_batch_v1",
            "issue_anchor": title,
            "finding_check_type": "model_evidence_review",
            "finding_semantic_key": title,
            "review_engine": "batched_model_review",
        },
    )


def _apply_model_enhancement(
    *,
    results: list[AgentReviewResult],
    enhancer: ModelEnhancer,
    ledger: EvidenceLedger,
    understanding: PaperUnderstandingArtifacts,
    checks: list[DeterministicCheck],
    citation_audit: CitationAudit | None,
) -> None:
    context = build_review_context(
        ledger=ledger.model_dump(mode="json"),
        paper_profile=understanding.profile.model_dump(mode="json"),
        claim_graph=understanding.claim_graph.model_dump(mode="json"),
        experiment_inventory=understanding.experiment_inventory.model_dump(mode="json"),
        review_plan=understanding.review_plan.model_dump(mode="json"),
        deterministic_checks={"checks": [check.model_dump(mode="json") for check in checks]},
        max_evidence=40,
        max_context_chars=12_000,
        max_serialized_chars=24_000,
    )
    context["citation_audit"] = (
        citation_audit.model_dump(mode="json") if citation_audit is not None else {}
    )
    by_agent = {result.agent_id: result for result in results}
    try:
        payload = enhancer(context)
    except Exception as exc:
        for agent_id in PROFESSIONAL_AGENT_IDS:
            by_agent[agent_id].metadata["model_status"] = "failed"
            by_agent[agent_id].warnings.append(f"模型增强失败：{type(exc).__name__}")
        return
    rows = payload.get("concerns") if isinstance(payload.get("concerns"), list) else []
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    allowed = set(context["selected_evidence_ids"])
    accepted = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        draft = _model_draft(row, allowed)
        if draft is None:
            continue
        agent_id = str(row.get("agent_id") or "")
        by_agent[agent_id].drafts.append(draft)
        accepted += 1
    for agent_id in PROFESSIONAL_AGENT_IDS:
        metadata = by_agent[agent_id].metadata
        metadata["model_status"] = "completed"
        metadata["review_engine"] = "local_rules_plus_batched_model"
        metadata["model_usage"] = usage
        metadata["model_context_budget"] = context["budget"]
        metadata["model_accepted_concerns"] = accepted


def _unsupported_agent(agent_id: str, mode: str) -> AgentReviewResult:
    return AgentReviewResult(
        agent_id=agent_id,
        status=AgentRunStatus.INCOMPLETE,
        warnings=[f"{agent_id} is not implemented in the local {mode} runner."],
    )


def run_peerassist_agents(
    *,
    mode: str,
    ledger: EvidenceLedger,
    checks: list[DeterministicCheck],
    capability_names: list[str] | None = None,
    understanding: PaperUnderstandingArtifacts | None = None,
    model_enhancer: ModelEnhancer | None = None,
    citation_audit: CitationAudit | None = None,
) -> list[AgentReviewResult]:
    normalized_mode = str(mode or "fast").strip().lower()
    for agent_id in FAST_AGENT_IDS:
        build_agent_input_packet(
            agent_id=agent_id,
            mode=normalized_mode,
            ledger=ledger,
            checks=checks,
            capability_names=capability_names,
        )

    professional_results = _local_professional_results(
        ledger=ledger,
        understanding=understanding,
        checks=checks,
    )
    statistics_result = next(
        result for result in professional_results if result.agent_id == "statistics_agent"
    )
    figure_evidence_ids = [
        item.id
        for item in ledger.items
        if item.type in {EvidenceType.FIGURE, EvidenceType.FIGURE_CAPTION, EvidenceType.TABLE, EvidenceType.TABLE_CELL}
    ]
    results = [
        *professional_results,
        _figure_table_agent(checks, figure_evidence_ids),
        _defense_agent(checks),
        _integrator_agent(statistics_result),
    ]
    citation_result = next(result for result in results if result.agent_id == "citation_agent")
    if citation_audit is not None:
        citation_result.metadata.update(
            {
                "citation_records": len(citation_audit.records),
                "citation_links": len(citation_audit.links),
                "citation_findings": len(citation_audit.findings),
                "citation_verifications": len(citation_audit.verifications),
            }
        )
    if model_enhancer is not None and understanding is not None:
        _apply_model_enhancement(
            results=results,
            enhancer=model_enhancer,
            ledger=ledger,
            understanding=understanding,
            checks=checks,
            citation_audit=citation_audit,
        )
    if normalized_mode in {"standard", "deep"}:
        results.append(_unsupported_agent("novelty_agent", normalized_mode))
    return results


def integrate_agent_results(
    results: list[AgentReviewResult],
    *,
    citation_audit: CitationAudit | None = None,
    mode: str = "fast",
) -> list[Concern]:
    draft_sources: dict[str, list[str]] = defaultdict(list)
    drafts_by_id: dict[str, AgentConcernDraft] = {}
    concerns: list[Concern] = []

    for result in results:
        if result.status in {AgentRunStatus.FAILED, AgentRunStatus.INCOMPLETE}:
            concerns.append(_manual_check_concern(result))
            continue
        for draft in result.drafts:
            drafts_by_id.setdefault(draft.id, draft)
            if result.agent_id not in draft_sources[draft.id]:
                draft_sources[draft.id].append(result.agent_id)

    for draft_id, draft in drafts_by_id.items():
        concerns.append(concern_from_agent_draft(draft, source_agent_ids=draft_sources[draft_id]))
    if citation_audit is not None:
        source_agent_id = "citation_agent" if mode.strip().lower() in {"standard", "deep"} else "citation_audit"
        concerns.extend(concerns_from_citation_audit(citation_audit, source_agent_id=source_agent_id))
    if len({concern.id for concern in concerns}) != len(concerns):
        raise ValueError("agent integration produced duplicate concern identifiers")
    return concerns


def _manual_check_concern(result: AgentReviewResult) -> Concern:
    warning_text = "; ".join(result.warnings) or f"{result.agent_id} did not complete."
    return Concern(
        id=f"concern_manual_check_{result.agent_id}",
        level=ConcernLevel.EDITOR_NOTE,
        category="manual_check",
        title=f"{result.agent_id} requires manual follow-up",
        impact="A planned PeerAssist review agent did not complete its local check.",
        benign_explanation=warning_text,
        author_action="Please review this check manually before relying on the report.",
        status=ConcernStatus.PENDING_HUMAN_CONFIRMATION,
        source_agent_ids=[result.agent_id],
        metadata={
            "agent_status": result.status.value,
            "producer_namespace": result.agent_id,
            "producer_version": "v1",
            "issue_anchor": "agent_incomplete",
            "finding_check_type": "agent_incomplete",
            "finding_semantic_key": result.status.value,
        },
    )

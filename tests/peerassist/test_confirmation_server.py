from __future__ import annotations

import json
import threading
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

import peerassist.confirmation_server as confirmation_server
from common.pipeline_context import init_full_pipeline_context, peerassist_stage_dir, write_json_file
from peerassist.confirmation_server import (
    create_confirmation_server,
    render_confirmation_page,
    render_workspace_app,
)
from schemas.peerassist import Concern, ConcernLevel, ConcernStatus


def _seed_peerassist_stage(run_dir: Path) -> Path:
    init_full_pipeline_context(run_dir=run_dir)
    out_dir = peerassist_stage_dir(run_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    concern = Concern(
        id="concern_pending_001",
        level=ConcernLevel.CLARIFICATION_NEEDED,
        category="statistics",
        title="Reported percentage needs clarification",
        evidence_ids=["P01-L001"],
        impact="May affect support for the result.",
        benign_explanation="A different denominator may have been used.",
        author_action="Please clarify the calculation basis.",
        status=ConcernStatus.PENDING_HUMAN_CONFIRMATION,
        source_agent_ids=["statistics_agent"],
    )
    concern_row = concern.model_dump(mode="json")
    write_json_file(
        out_dir / "peerassist_concerns.json",
        {"schema_version": "peerassist.concerns.v1", "mode": "fast", "concerns": [concern_row]},
    )
    write_json_file(
        out_dir / "confirmation_bundle.json",
        {
            "schema_version": "peerassist.confirmation_bundle.v1",
            "counts_by_status": {"pending_human_confirmation": 1},
            "groups": {
                "pending_human_confirmation": [
                    {**concern_row, "evidence": [{"id": "P01-L001", "locator": "p.1 line 1"}]}
                ],
                "confirmed": [],
                "downgraded": [],
                "rewritten": [],
                "deleted": [],
            },
        },
    )
    write_json_file(
        out_dir / "confirmation_review_queue.json",
        {
            "schema_version": "peerassist.confirmation_review_queue.v1",
            "items": [
                {
                    **concern_row,
                    "position": 1,
                    "evidence": [{"id": "P01-L001", "locator": "p.1 line 1"}],
                    "allowed_actions": ["confirm", "rewrite", "downgrade", "delete", "mark_pending"],
                }
            ],
        },
    )
    write_json_file(
        out_dir / "human_confirmations.json",
        {"schema_version": "peerassist.human_confirmations.v1", "actions": []},
    )
    write_json_file(
        out_dir / "agent_results.json",
        {
            "schema_version": "peerassist.agent_results.v1",
            "mode": "fast",
            "results": [
                {
                    "agent_id": "statistics_agent",
                    "status": "completed",
                    "drafts": [concern_row],
                    "warnings": [],
                    "metadata": {"lead_count": 1},
                },
                {
                    "agent_id": "defense_agent",
                    "status": "completed",
                    "drafts": [],
                    "warnings": [],
                    "metadata": {"pressure_tests": ["check_001: pressure-test against denominator"]},
                },
            ],
        },
    )
    write_json_file(
        out_dir / "capability_invocations.json",
        {
            "schema_version": "peerassist.capability_invocations.v1",
            "mode": "fast",
            "results": [
                {
                    "task_id": "demo",
                    "call_id": "percentage_consistency_check",
                    "agent_id": "statistics_agent",
                    "capability_name": "percentage_consistency_check",
                    "source": "builtin",
                    "status": "completed",
                    "artifact_ids": ["deterministic_checks"],
                    "attempts": 1,
                    "duration_ms": 42,
                    "evidence_ids": ["P01-L001"],
                }
            ],
        },
    )
    write_json_file(
        out_dir / "evidence_ledger.json",
        {
            "schema_version": "peerassist.evidence_ledger.v1",
            "paper_id": "demo",
            "items": [
                {
                    "id": "P01-L001",
                    "type": "text_span",
                    "page": 1,
                    "section": "Methods",
                    "locator": "p.1 line 1",
                    "text": "The paper evaluates one dataset without an ablation study.",
                }
            ],
        },
    )
    write_json_file(
        out_dir / "deterministic_checks.json",
        {"schema_version": "peerassist.deterministic_checks.v1", "mode": "fast", "checks": []},
    )
    (out_dir / "tool_trace.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "task_id": "demo",
                        "call_id": "percentage_consistency_check",
                        "agent_id": "statistics_agent",
                        "source": "builtin",
                        "tool": "percentage_consistency_check",
                        "status": "queued",
                        "ts": "2026-07-10T00:00:00Z",
                        "input_summary": "run deterministic percentage consistency checks",
                        "evidence_ids": ["P01-L001"],
                    }
                ),
                json.dumps(
                    {
                        "task_id": "demo",
                        "call_id": "percentage_consistency_check",
                        "agent_id": "statistics_agent",
                        "source": "builtin",
                        "tool": "percentage_consistency_check",
                        "status": "completed",
                        "ts": "2026-07-10T00:00:01Z",
                        "output_summary": "1 check completed",
                        "artifact_ids": ["deterministic_checks"],
                        "duration_ms": 42,
                        "evidence_ids": ["P01-L001"],
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return out_dir


def test_render_confirmation_page_contains_evidence_and_actions(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _seed_peerassist_stage(run_dir)

    html = render_confirmation_page(run_dir=run_dir, paper_id="demo")

    assert "PeerAssist 论文审核辅助台" in html
    assert "data-peerassist-agent-console" in html
    assert 'data-peerassist-window="paper"' in html
    assert "data-peerassist-window-bar" in html
    assert "PeerAssist 分窗口工作区" in html
    assert "工作窗口" in html
    assert 'data-peerassist-window-target="paper"' in html
    assert 'data-peerassist-window-target="agent"' in html
    assert 'data-peerassist-window-target="queue"' in html
    assert 'data-peerassist-window-target="trace"' in html
    assert 'data-peerassist-window-target="confirm"' in html
    assert 'data-peerassist-window-target="artifacts"' in html
    assert "论文阅读" in html
    assert "智能审稿" in html
    assert "产物导出" in html
    assert "setPeerAssistWindow" in html
    assert "peerassistSetWindow" in html
    assert "revealWorkspacePanel" in html
    assert 'body[data-peerassist-window="paper"]' in html
    assert 'data-panel="review-queue"' in html
    assert 'data-panel="paper-viewer"' in html
    assert 'data-panel="paper-review-stage"' in html
    assert 'data-panel="tool-trace"' in html
    assert 'data-panel="human-confirmation"' in html
    assert 'data-panel="stream-console"' in html
    assert "data-agent-runtime-strip" in html
    assert "data-agent-ops-strip" in html
    assert 'data-panel="session-navigator"' in html
    assert "stream-chip" in html
    assert "运行态指挥条" in html
    assert "审稿流程" in html
    assert "人工确认完成度" in html
    assert "实时事件" in html
    assert "证据焦点" in html
    assert 'data-panel="evidence-chain-matrix"' in html
    assert "证据链矩阵" in html
    assert "data-evidence-chain-row" in html
    assert "调用状态" in html
    assert "人工状态" in html
    assert "把每条关注点的 PDF 位置、证据、代理来源、调用状态和人工状态放在同一行。" in html
    assert "产物工作区" in html
    assert "智能体审稿入口" in html
    assert "开始全篇智能审稿" in html
    assert "证据台账、确定性核查和代理结果" in html
    assert "gpt-5.4" in html
    assert "https://deepkey.top/v1" in html
    assert "下一步动作" in html
    assert "data-agent-workflow" in html
    assert "data-agent-stage-board" in html
    assert "PeerAssist 智能体审稿生命周期" in html
    assert "解析论文" in html
    assert "建立证据台账" in html
    assert "确定性核查" in html
    assert "多代理评审" in html
    assert "MCP / Skills 调用" in html
    assert "等待人工批准" in html
    assert "报告导出" in html
    assert 'data-stage-status="completed"' in html
    assert 'data-stage-status="active"' in html
    assert "data-stream-log" in html
    assert "data-review-progress" in html
    assert "data-queue-filterbar" in html
    assert 'data-queue-filter="all"' in html
    assert 'data-queue-filter="current-page"' in html
    assert 'data-queue-filter="major"' in html
    assert 'data-queue-filter="clarification"' in html
    assert 'data-queue-filter="pdf"' in html
    assert "data-queue-search" in html
    assert "data-queue-result-count" in html
    assert "data-queue-empty" in html
    assert "applyQueueFilter" in html
    assert "peerassistApplyQueueFilter" in html
    assert "peerassistCurrentPdfPage" in html
    assert "搜索队列" in html
    assert "全部" in html
    assert "当前页" in html
    assert "主要问题" in html
    assert "需澄清" in html
    assert "有 PDF 证据" in html
    assert "data-concern-level" in html
    assert "data-concern-search" in html
    assert "Evidence Preview" in html
    assert "证据账本" in html
    assert "data-trace-filter" in html
    assert "data-trace-audit-card" in html
    assert "data-capability-audit-card" in html
    assert "trace-audit-field" in html
    assert "调用 ID" in html
    assert "输入摘要" in html
    assert "输出摘要" in html
    assert "能力调用可追溯记录" in html
    assert "证据" in html
    assert "产物" in html
    assert "data-copy-path" in html
    assert "agent-toast" in html
    assert "复制路径" in html
    assert "applyTraceFilter('all')" in html
    assert "产物路径已复制" in html
    assert "data-agent-review-start" in html
    assert "data-agent-review-stream" in html
    assert "fetch('/api/agent-review'" in html
    assert "review_mode: reviewMode" in html
    assert "peerassistSetPdfAgentReviewMode" in html
    assert "peerassistSetPdfAgentPhase" in html
    assert "peerassistRunAgentReview" in html
    assert "peerassistCancelAgentReview" in html
    assert "peerassistRetryAgentReview" in html
    assert "peerassistUpdatePdfMissionControl" in html
    assert "updatePdfMissionControl(state)" in html
    assert "data-pdf-mission-control" in html
    assert "peerassistUpdatePdfAgentTimeline" in html
    assert "updatePdfAgentTimeline(state)" in html
    assert "data-pdf-agent-timeline" in html
    assert "eventMatchesNeedle" in html
    assert "peerassistUpdatePdfAgentContext" in html
    assert "peerassistUpdatePdfRecoveryCheckpoint" in html
    assert "updatePdfRecoveryCheckpoint(state)" in html
    assert "applyTraceFilter('failed')" in html
    assert "AbortController" in html
    assert "signal: controller.signal" in html
    assert "智能审稿已取消" in html
    assert "data-pdf-agent-dock" in html
    assert "data-pdf-agent-mode" in html
    assert "data-pdf-agent-action" in html
    assert "data-pdf-agent-run-controls" in html
    assert "data-pdf-agent-cancel" in html
    assert "data-pdf-agent-retry" in html
    assert "data-pdf-agent-phase-rail" in html
    assert "data-pdf-agent-phase" in html
    assert "正在组装全篇审稿上下文" in html
    assert "以${reviewModeCopy}模式调用审稿模型" in html
    assert "请先在 PDF 正文中选中一段文字" not in html
    assert 'data-panel="artifact-workspace"' in html
    assert 'data-panel="next-actions"' in html
    assert "confirmation_review_queue.json" in html
    assert "human_confirmations.json" in html
    assert "agent-timeline" in html
    assert 'data-stream-state="connecting"' in html
    assert "new EventSource('/api/events')" in html
    assert "addEventListener('heartbeat'" in html
    assert "addEventListener('done'" in html
    assert "统计核查代理" in html
    assert "报告百分比需要澄清" in html
    assert "确认" in html
    assert "改写" in html
    assert "证据审稿队列" in html
    assert "论文原文 PDF" in html
    assert "真实 PDF 阅读面" in html
    assert 'data-panel="review-inspector"' in html
    assert "页边审稿意见" in html
    assert ".paper-canvas {\n      display: grid;\n      grid-template-columns: minmax(0, 1fr);" in html
    assert ".review-inspector" in html
    assert "抽取文本预览" in html
    assert "未发现源 PDF" in html
    assert "paper-highlight" in html
    assert "data-margin-comment" in html
    assert "data-paper-concern" in html
    assert "data-pdf-page" in html
    assert "查看原文高亮" in html
    assert "定位队列" in html
    assert "focusAnnotation" in html
    assert "chainRow" in html
    assert ".queue-body .item[data-concern-id=" in html
    assert "peerassistPdfGoToPage" in html
    assert "已跳转到 PDF 第" in html
    assert "data-pdf-page-rail" in html
    assert "buildPdfPageRail" in html
    assert "runPdfSearch" in html
    assert "goToPdfSearchMatch" in html
    assert "applyPdfSearchHighlights" in html
    assert "pdf-text-match" in html
    assert "getPdfPageText" in html
    assert "collectPdfPageConcernCounts" in html
    assert "annotatePdfPageRail" in html
    assert "syncCurrentPdfPage" in html
    assert "updatePdfPageContext" in html
    assert "collectPdfReadingRouteItems" in html
    assert "renderPdfReadingRoute" in html
    assert "peerassistRenderPdfReadingRoute" in html
    assert "reviewRouteScore" in html
    assert "按重点阅读路线定位 PDF 第" in html
    assert "peerassistPdfNextConcernPage" in html
    assert "peerassistPdfNextPendingConcernPage" in html
    assert "wirePdfReviewCommands" in html
    assert "handlePdfReviewCommand" in html
    assert "updatePdfRuntimePulse" in html
    assert "syncPdfRuntimePagePulse" in html
    assert "pdf-selection-context" in html
    assert "data-pdf-selection-signal" in html
    assert "page: page > 0 ? `第 ${page} 页`" in html
    assert "chars: text ? `${text.length} 字`" in html
    assert "queue: page > 0 ? `${pendingOnPage} / ${pageQueue} 待确认`" in html
    assert "mode: `${currentMode}模式`" in html
    assert "updatePdfSelectionContext" in html
    assert "peerassistUpdatePdfSelectionContext" in html
    assert "选区审稿上下文" in html
    assert "当前页待确认" in html
    assert "fetch('/api/manual-concern'" in html
    assert "peerassistSubmitManualConcern" in html
    assert "updatePdfToolTrace" in html
    assert "renderPdfToolTraceCard" in html
    assert "pdf-tool-trace-summary" in html
    assert "pdf-tool-trace-detail-grid" in html
    assert "pdf-tool-trace-field" in html
    assert "输入摘要" in html
    assert "输出摘要" in html
    assert "duration_ms" in html
    assert "appendPdfActivityLine" in html
    assert "data-pdf-human-gate" in html
    assert "updatePdfHumanGate" in html
    assert "peerassistUpdatePdfHumanGate" in html
    assert "data-pdf-human-gate-open" in html
    assert "data-pdf-human-gate-next" in html
    assert "data-pdf-human-gate-queue" in html
    assert "已打开人工确认闸门" in html
    assert "已定位到审稿队列清单" in html
    assert "data-pdf-evidence-gate" in html
    assert "updatePdfEvidenceGate" in html
    assert "peerassistUpdatePdfEvidenceGate" in html
    assert "focusFirstUnboundEvidenceConcern" in html
    assert "peerassistFocusFirstUnboundEvidenceConcern" in html
    assert "data-pdf-evidence-gate-chain" in html
    assert "data-pdf-evidence-gate-pdf" in html
    assert "data-pdf-evidence-gate-unbound" in html
    assert "已定位到证据链矩阵" in html
    assert "已筛选有 PDF 证据的关注点" in html
    assert "data-pdf-runtime-pulse" in html
    assert "data-pdf-runtime-state" in html
    assert "data-pdf-runtime-pending" in html
    assert "data-pdf-runtime-events" in html
    assert "data-pdf-runtime-page" in html
    assert "data-pdf-annotation-list" in html
    assert "data-pdf-annotation-empty" in html
    assert "syncPdfPageAnnotations" in html
    assert "syncPdfAnnotationProgress" in html
    assert "collectPdfPagePendingCounts" in html
    assert "nextPendingConcernPage" in html
    assert "pendingCountForPage" in html
    assert "setPdfAnnotationDensity" in html
    assert "peerassistSetPdfAnnotationDensity" in html
    assert "isResolvedConcernStatus" in html
    assert "data-pdf-annotation-card" in html
    assert "pdf-annotation-actions" in html
    assert "dataset.action = 'confirm'" in html
    assert "dataset.action = 'mark_pending'" in html
    assert "dataset.pdfAnnotationEdit" in html
    assert "openConcernEditor" in html
    assert "pdfAnnotation.dataset.active = 'true'" in html
    assert "已打开该条审稿意见编辑框" in html
    assert "核对" in html
    assert "data-pdf-page-filter-current" in html
    assert "data-pdf-page-next-pending" in html
    assert "data-pdf-page-next-concern" in html
    assert "data-pdf-page-jump" in html
    assert "data-pdf-concern-count" in html
    assert "data-pdf-pending-count" in html
    assert "data-has-concern" in html
    assert "data-has-pending" in html
    assert "data-focus-review-toggle" in html
    assert "data-review-focus" in html
    assert "setReviewFocusMode" in html
    assert "peerassistPdfRenderCurrentPage" in html
    assert "专注审稿" in html
    assert "退出专注" in html
    assert "已进入专注审稿模式" in html
    assert "已退出专注审稿模式" in html
    assert "pdf-page-badge" in html
    assert "本页 ${count} 条审稿关注" in html
    assert "本页 ${pendingCount} 条未处理" in html
    assert "下一处未处理批注" in html
    assert "data-pdf-selection-tray" in html
    assert "data-pdf-selection-popover" in html
    assert "data-pdf-selection-popover-quote" in html
    assert "data-pdf-selection-popover-meta" in html
    assert "data-pdf-selection-quote" in html
    assert "data-pdf-selection-copy" in html
    assert "data-pdf-selection-use" in html
    assert "peerassistSelectedEvidence" in html
    assert "capturePdfTextSelection" in html
    assert "selection.getRangeAt(0).getBoundingClientRect()" in html
    assert "PDF 第 ${selectedEvidence.page || '未知'} 页选区" in html
    assert "正在基于 PDF 选区启动智能审稿" in html
    assert "并优先核对当前选中文字" in html
    assert "PDF 页码导航" in html
    assert "已定位到论文高亮" in html
    assert "已定位到审稿队列" in html
    assert "页边批注" in html
    assert "暂无待人工确认批注" not in html
    assert "p.1 line 1" in html
    assert "data-action=\"confirm\"" in html
    assert "data-action=\"rewrite\"" in html


def test_render_source_pdf_viewer_contains_selection_review_button() -> None:
    html = confirmation_server._render_source_pdf_viewer()

    assert "data-pdf-page-context" in html
    assert "data-pdf-page-context-title" in html
    assert "data-pdf-page-context-copy" in html
    assert "data-pdf-page-filter-current" in html
    assert "data-pdf-page-next-pending" in html
    assert "data-pdf-page-next-concern" in html
    assert "data-pdf-reading-route-list" in html
    assert "data-pdf-reading-route-empty" in html
    assert "重点阅读路线" in html
    assert "只看本页队列" in html
    assert "下一未处理" in html
    assert "下一关注页" in html
    assert "data-pdf-search-strip" in html
    assert "data-pdf-search-input" in html
    assert "data-pdf-search-status" in html
    assert "data-pdf-search-results" in html
    assert "data-pdf-search-prev" in html
    assert "data-pdf-search-next" in html
    assert "data-pdf-search-clear" in html
    assert "原文检索" in html
    assert "搜索术语、指标、图表编号" in html
    assert "上一处" in html
    assert "下一处" in html
    assert "data-pdf-review-command-strip" in html
    assert "data-pdf-mission-control" in html
    assert "PDF 审稿任务驾驶舱" in html
    assert "智能体任务驾驶舱" in html
    assert "data-pdf-mission-title" in html
    assert "data-pdf-mission-copy" in html
    assert 'data-pdf-mission-value="mode"' in html
    assert 'data-pdf-mission-value="phase"' in html
    assert 'data-pdf-mission-value="pending"' in html
    assert 'data-pdf-mission-value="binding"' in html
    assert 'data-pdf-mission-value="events"' in html
    assert 'data-pdf-mission-value="selection"' in html
    assert "data-pdf-mission-primary" in html
    assert "data-pdf-mission-next" in html
    assert "data-pdf-mission-recovery" in html
    assert "data-pdf-mission-evidence" in html
    assert "从 PDF 原文开始审稿" in html
    assert "继续审稿" in html
    assert "证据矩阵" in html
    assert "data-pdf-agent-dock" in html
    assert "PDF 智能体审稿操作坞" in html
    assert "data-pdf-agent-phase-rail" in html
    assert "PDF 智能审稿任务阶段" in html
    assert "data-pdf-agent-timeline" in html
    assert "PDF 智能体事件时间线" in html
    assert "智能体事件时间线" in html
    assert 'data-pdf-agent-timeline-step="queued"' in html
    assert 'data-pdf-agent-timeline-step="parse"' in html
    assert 'data-pdf-agent-timeline-step="ledger"' in html
    assert 'data-pdf-agent-timeline-step="checks"' in html
    assert 'data-pdf-agent-timeline-step="agents"' in html
    assert 'data-pdf-agent-timeline-step="tools"' in html
    assert 'data-pdf-agent-timeline-step="human"' in html
    assert 'data-pdf-agent-timeline-step="report"' in html
    assert "排队" in html
    assert "解析论文" in html
    assert "证据台账" in html
    assert "多代理评审" in html
    assert "MCP/Skills" in html
    assert "报告产物" in html
    assert "data-pdf-agent-timeline-trace" in html
    assert "data-pdf-agent-timeline-human" in html
    assert "data-pdf-agent-timeline-report" in html
    assert 'data-pdf-agent-phase="prepare"' in html
    assert 'data-pdf-agent-phase="evidence"' in html
    assert 'data-pdf-agent-phase="model"' in html
    assert 'data-pdf-agent-phase="queue"' in html
    assert 'data-pdf-agent-phase="human"' in html
    assert "准备上下文" in html
    assert "读取证据" in html
    assert "调用模型" in html
    assert "写回队列" in html
    assert "人工确认" in html
    assert "data-review-mode=\"fast\"" in html
    assert 'data-pdf-agent-mode="fast"' in html
    assert 'data-pdf-agent-mode="standard"' in html
    assert 'data-pdf-agent-mode="deep"' in html
    assert 'data-pdf-agent-action="agent-review"' in html
    assert 'data-pdf-agent-action="selection-review"' in html
    assert 'data-pdf-agent-action="current-page"' in html
    assert "data-pdf-agent-context-manifest" in html
    assert 'data-pdf-agent-context-item="pdf"' in html
    assert 'data-pdf-agent-context-item="evidence"' in html
    assert 'data-pdf-agent-context-item="checks"' in html
    assert 'data-pdf-agent-context-item="tools"' in html
    assert 'data-pdf-agent-context-item="selection"' in html
    assert 'data-pdf-agent-context-item="human"' in html
    assert "智能审稿上下文装载清单" in html
    assert "审稿上下文装载" in html
    assert "已导入，可浏览与选区" in html
    assert "数值/统计/引用线索" in html
    assert "可追溯调用" in html
    assert "逐条确认" in html
    assert "data-pdf-agent-run-controls" in html
    assert "data-pdf-agent-run-state" in html
    assert "data-pdf-agent-cancel" in html
    assert "data-pdf-agent-retry" in html
    assert "任务空闲，可启动审稿" in html
    assert "取消" in html
    assert "重试" in html
    assert "智能体入口" in html
    assert "证据约束审稿" in html
    assert "必绑定" in html
    assert "data-pdf-tool-trace-strip" in html
    assert "PDF 工具调用轨迹" in html
    assert "data-pdf-tool-trace-list" in html
    assert "等待工具调用事件" in html
    assert "data-pdf-recovery-strip" in html
    assert "data-pdf-recovery-value" in html
    assert "data-pdf-recovery-trace" in html
    assert "data-pdf-recovery-failed" in html
    assert "data-pdf-recovery-artifacts" in html
    assert "PDF 智能审稿检查点与恢复" in html
    assert "检查点" in html
    assert "最近调用" in html
    assert "失败事件" in html
    assert "最近产物" in html
    assert "查看追踪" in html
    assert "产物区" in html
    assert "data-pdf-human-gate" in html
    assert "PDF 人工确认闸门" in html
    assert "人工确认闸门" in html
    assert "主要待处理" in html
    assert "打开人工确认" in html
    assert "队列清单" in html
    assert "data-pdf-evidence-gate" in html
    assert "PDF 证据绑定闸门" in html
    assert "证据绑定闸门" in html
    assert "待人工核查" in html
    assert "证据链矩阵" in html
    assert "有 PDF 证据" in html
    assert "待核查项" in html
    assert 'data-pdf-review-command="agent-review"' in html
    assert 'data-pdf-review-command="selection-review"' in html
    assert 'data-pdf-review-command="current-page"' in html
    assert 'data-pdf-review-command="next-pending"' in html
    assert 'data-pdf-review-command="next-concern"' in html
    assert 'data-pdf-review-command="focus"' in html
    assert "PDF 审稿命令条" in html
    assert "data-pdf-runtime-pulse" in html
    assert "data-pdf-runtime-state" in html
    assert "data-pdf-runtime-pending" in html
    assert "data-pdf-runtime-events" in html
    assert "data-pdf-runtime-page" in html
    assert "任务流连接中" in html
    assert "data-pdf-activity-feed" in html
    assert "data-pdf-activity-empty" in html
    assert "等待审稿事件流" in html
    assert "data-pdf-page-annotations" in html
    assert "data-pdf-annotation-list" in html
    assert "data-pdf-annotation-empty" in html
    assert "data-pdf-annotation-progress" in html
    assert "data-pdf-annotation-pending" in html
    assert "data-pdf-annotation-done" in html
    assert "data-pdf-annotation-total" in html
    assert "data-pdf-annotation-meter" in html
    assert "data-pdf-annotation-density-toggle" in html
    assert "PDF 本页批注处理进度" in html
    assert "本页总计" in html
    assert "PDF 本页审稿批注" in html
    assert "data-focus-review-toggle" in html
    assert "专注审稿" in html
    assert "data-pdf-selection-review" in html
    assert "data-pdf-selection-use" in html
    assert "data-pdf-selection-popover" in html
    assert "PDF 选区审稿浮层" in html
    assert "选区证据" in html
    assert "基于选区审稿" in html
    assert "data-pdf-selection-note" in html
    assert "data-pdf-selection-manual" in html
    assert "人工批注" in html
    assert "加入队列" in html
    assert "inline-button primary" in html


def test_render_workspace_app_uses_react_frontend_bootstrap(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _seed_peerassist_stage(run_dir)

    html = render_workspace_app(run_dir=run_dir, paper_id="demo")

    assert "PeerAssist 智能审稿工作台" in html
    assert "peerassist-server-bootstrap" in html
    assert '"paper_id": "demo"' in html
    assert '"api_base": "/api"' in html
    assert '"pdf_url": "/paper.pdf"' in html
    assert '"legacy_url": "/legacy"' in html
    assert '<div id="root"></div>' in html
    assert 'type="module"' in html
    assert "React" not in html


def test_workspace_frontend_contains_pdfjs_review_reader() -> None:
    workspace_dir = Path(__file__).parents[2] / "web" / "peerassist-workspace"
    package_json = json.loads((workspace_dir / "package.json").read_text(encoding="utf-8"))
    source = (workspace_dir / "src" / "main.tsx").read_text(encoding="utf-8")

    assert package_json["dependencies"]["react"].startswith("^18")
    assert "pdfjs-dist" in package_json["dependencies"]
    assert "function PdfReviewReader" in source
    assert "pdfjsLib.getDocument" in source
    assert "pdf-text-layer" in source
    assert "onSelection({" in source
    assert "text," in source
    assert "page: pageNumber" in source
    assert "请在论文中拖选文字" in source
    assert "rangeChunkSize: 64 * 1024" in source
    assert "pdf.worker.min.mjs" in source
    assert "function PdfPageView" in source
    assert 'title="单页阅读"' in source
    assert 'title="双页阅读"' in source
    assert 'data-view-mode={effectiveViewMode}' in source
    assert "pdf-annotation-pin" in source
    assert "打开关注：" in source
    assert "selected={item.id === activeConcernId}" in source
    assert "job-timeline" in source
    assert "运行时间线" in source


def test_confirmation_server_state_and_decision_endpoints(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    out_dir = _seed_peerassist_stage(run_dir)
    server = create_confirmation_server(
        run_dir=run_dir,
        paper_id="demo",
        host="127.0.0.1",
        port=0,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(f"{base_url}/paper", timeout=5) as response:
            workspace_html = response.read().decode("utf-8")
            workspace_content_type = response.headers["Content-Type"]
        assert workspace_content_type.startswith("text/html")
        assert "PeerAssist 智能审稿工作台" in workspace_html
        assert "peerassist-server-bootstrap" in workspace_html

        with urllib.request.urlopen(f"{base_url}/legacy", timeout=5) as response:
            legacy_html = response.read().decode("utf-8")
            legacy_target = response.geturl()
        assert legacy_target.endswith("/paper")
        assert "PeerAssist 智能审稿工作台" in legacy_html

        with urllib.request.urlopen(f"{base_url}/api/bootstrap", timeout=5) as response:
            bootstrap = json.loads(response.read().decode("utf-8"))
        assert bootstrap["schema_version"] == "peerassist.workspace_bootstrap.v1"
        assert bootstrap["paper_id"] == "demo"
        assert bootstrap["assets"]["legacy_url"] == "/legacy"
        assert bootstrap["windows"][0]["id"] == "paper"
        assert bootstrap["state"]["pending_count"] == 1
        assert bootstrap["model_config"]["model"] == "gpt-5.4"

        with urllib.request.urlopen(f"{base_url}/api/state", timeout=5) as response:
            state = json.loads(response.read().decode("utf-8"))
        assert state["pending_count"] == 1
        assert state["runtime"]["mode"] == "fast"
        assert state["agent_runs"][0]["agent_id"] == "statistics_agent"
        assert state["agent_runs"][0]["draft_count"] == 1
        assert state["tool_trace"]["counts_by_status"]["completed"] == 1
        assert state["tool_trace"]["latest_status_by_call"]["percentage_consistency_check"] == "completed"
        assert state["capability_invocations"][0]["capability_name"] == "percentage_consistency_check"

        with urllib.request.urlopen(f"{base_url}/api/events", timeout=5) as response:
            event_text = response.read().decode("utf-8")
            content_type = response.headers["Content-Type"]
        assert content_type.startswith("text/event-stream")
        assert "retry: 15000" in event_text
        assert "event: state" in event_text
        assert "event: heartbeat" in event_text
        assert "event: done" in event_text
        assert '"schema_version": "peerassist.confirmation_state.v1"' in event_text

        payload = json.dumps(
            {
                "concern_id": "concern_pending_001",
                "action": "confirm",
                "reviewer_id": "reviewer-1",
                "timestamp": "2026-07-10T00:00:00Z",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{base_url}/api/decision",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            result = json.loads(response.read().decode("utf-8"))

        assert result["actions_count"] == 1
        report = json.loads((out_dir / "peerassist_report.json").read_text(encoding="utf-8"))
        assert report["confirmed_count"] == 1

        request = urllib.request.Request(
            f"{base_url}/api/agent-review",
            data=json.dumps({"selected_text": ""}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as exc:
            error_payload = json.loads(exc.read().decode("utf-8"))
        else:  # pragma: no cover
            raise AssertionError("agent review should require a configured model key")
        assert "模型 API Key 未配置" in error_payload["error"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_manual_pdf_selection_concern_endpoint_writes_queue(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    out_dir = _seed_peerassist_stage(run_dir)
    server = create_confirmation_server(
        run_dir=run_dir,
        paper_id="demo",
        host="127.0.0.1",
        port=0,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        payload = json.dumps(
            {
                "selected_text": "The paper claims a statistically significant reduction on page one.",
                "page": 1,
                "note": "请作者说明统计显著性检验、阈值和适用假设。",
                "reviewer_id": "reviewer-1",
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{base_url}/api/manual-concern",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            result = json.loads(response.read().decode("utf-8"))

        assert result["schema_version"] == "peerassist.manual_concern_result.v1"
        assert result["concern_id"] == "concern_manual_pdf_001"
        assert result["evidence_id"] == "MANUAL-PDF-001"
        assert result["queue_items"] == 2
        ledger = json.loads((out_dir / "evidence_ledger.json").read_text(encoding="utf-8"))
        manual_evidence = next(row for row in ledger["items"] if row["id"] == "MANUAL-PDF-001")
        assert manual_evidence["page"] == 1
        assert manual_evidence["metadata"]["source"] == "manual_pdf_selection"
        assert "statistically significant" in manual_evidence["text"]
        queue = json.loads((out_dir / "confirmation_review_queue.json").read_text(encoding="utf-8"))
        manual_items = [row for row in queue["items"] if row["id"] == "concern_manual_pdf_001"]
        assert len(manual_items) == 1
        manual_item = manual_items[0]
        assert manual_item["status"] == "pending_human_confirmation"
        assert manual_item["category"] == "manual_annotation"
        assert manual_item["evidence"][0]["id"] == "MANUAL-PDF-001"
        assert manual_item["evidence"][0]["locator"] == "PDF 第 1 页人工选区"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_confirmation_server_serves_source_pdf_when_available(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _seed_peerassist_stage(run_dir)
    (run_dir / "paper.pdf").write_bytes(b"%PDF-1.4\n% PeerAssist test PDF\n%%EOF\n")

    html = render_confirmation_page(run_dir=run_dir, paper_id="paper")

    assert "原始 PDF 已导入" in html
    assert 'data-source-pdf-viewer' in html
    assert 'data-pdf-reader' in html
    assert 'data-pdf-canvas' in html
    assert 'data-pdf-text-layer' in html
    assert 'data-pdf-action="next"' in html
    assert 'data-pdf-action="fit"' in html
    assert "pdf.min.mjs" in html
    assert "pdf.worker.min.mjs" in html
    assert "new pdfjsLib.TextLayer" in html
    assert "pdfjsLib.getDocument('/paper.pdf')" in html
    assert "PDF 原文检索" in html
    assert "runPdfSearch" in html
    assert "pdfSearchMatches" in html
    assert "PDF 选区审稿浮层" in html
    assert "PDF 工具调用轨迹" in html
    assert "percentage_consistency_check" in html
    assert "1 check completed" in html
    assert "打开 PDF" in html
    assert "原始 PDF" in html
    assert "paper.pdf" in html

    server = create_confirmation_server(
        run_dir=run_dir,
        paper_id="paper",
        host="127.0.0.1",
        port=0,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(f"{base_url}/paper.pdf", timeout=5) as response:
            body = response.read()
            content_type = response.headers["Content-Type"]
            accept_ranges = response.headers["Accept-Ranges"]
        assert content_type == "application/pdf"
        assert accept_ranges == "bytes"
        assert body.startswith(b"%PDF-1.4")

        range_request = urllib.request.Request(
            f"{base_url}/paper.pdf",
            headers={"Range": "bytes=0-9"},
        )
        with urllib.request.urlopen(range_request, timeout=5) as response:
            range_body = response.read()
            content_range = response.headers["Content-Range"]
            range_status = response.status
        assert range_status == 206
        assert content_range.startswith("bytes 0-9/")
        assert range_body == body[:10]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_agent_review_writes_structured_concerns_to_confirmation_queue(
    tmp_path: Path, monkeypatch
) -> None:
    run_dir = tmp_path / "run"
    out_dir = _seed_peerassist_stage(run_dir)
    monkeypatch.setenv("PEERASSIST_OPENAI_API_KEY", "test-key")
    observed: dict[str, str] = {}

    def fake_chat_completion(**kwargs) -> str:
        messages = kwargs.get("messages") or []
        observed["prompt"] = str(messages[-1]["content"]) if messages else ""
        return json.dumps(
            {
                "report_markdown": "# 审稿辅助报告\n\n## 三、主要意见\n- Ablation evidence is missing.",
                "concerns": [
                    {
                        "level": "major_concern",
                        "category": "methodology",
                        "title": "Ablation evidence is missing",
                        "evidence_ids": ["P01-L001"],
                        "impact": "The current evidence does not isolate the proposed component.",
                        "benign_explanation": "The ablation may be available in supplementary material.",
                        "author_action": "Please add an ablation study or explain where it is reported.",
                    }
                ]
            }
        )

    monkeypatch.setattr(confirmation_server, "_chat_completion", fake_chat_completion)
    server = create_confirmation_server(
        run_dir=run_dir,
        paper_id="demo",
        host="127.0.0.1",
        port=0,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        request = urllib.request.Request(
            f"{base_url}/api/agent-review",
            data=json.dumps({"selected_text": "", "review_mode": "deep"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            result = json.loads(response.read().decode("utf-8"))

        assert result["structured_concern_count"] == 1
        assert result["review_mode"] == "deep"
        assert '"review_mode": "deep"' in observed["prompt"]
        assert "补充材料、复现线索、反方解释" in observed["prompt"]
        assert result["suggestion"].startswith("# 审稿辅助报告")
        assert result["queue_items"] == 2
        concerns = json.loads((out_dir / "peerassist_concerns.json").read_text(encoding="utf-8"))
        queue = json.loads((out_dir / "confirmation_review_queue.json").read_text(encoding="utf-8"))
        generated = [row for row in concerns["concerns"] if row["id"].startswith("concern_llm_review_")]
        assert generated[0]["title"] == "Ablation evidence is missing"
        assert generated[0]["evidence_ids"] == ["P01-L001"]
        assert generated[0]["status"] == "pending_human_confirmation"
        assert any(item["id"] == generated[0]["id"] for item in queue["items"])
        assert (out_dir / "agent_review_draft.md").read_text(encoding="utf-8").startswith(
            "# 审稿辅助报告"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_chat_completion_uses_configurable_review_timeout(monkeypatch) -> None:
    observed: dict[str, float] = {}

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *_args) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(
                {"choices": [{"message": {"content": "审稿建议"}}]},
                ensure_ascii=False,
            ).encode("utf-8")

    def fake_urlopen(_request, *, timeout: float):
        observed["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setenv("PEERASSIST_OPENAI_TIMEOUT_SECONDS", "180")
    monkeypatch.setattr(confirmation_server, "urlopen", fake_urlopen)

    result = confirmation_server._chat_completion(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="gpt-test",
        messages=[{"role": "user", "content": "review"}],
    )

    assert result == "审稿建议"
    assert observed["timeout"] == 180


def test_peerassist_confirm_server_console_script_is_registered() -> None:
    payload = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert (
        payload["project"]["scripts"]["peerassist-confirm-server"]
        == "peerassist.confirmation_server:main"
    )


def test_confirmation_server_module_has_python_m_entrypoint() -> None:
    source = Path("src/peerassist/confirmation_server.py").read_text(encoding="utf-8")

    assert 'if __name__ == "__main__"' in source
    assert "raise SystemExit(main())" in source

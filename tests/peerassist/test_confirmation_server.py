from __future__ import annotations

import json
import threading
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

import peerassist.confirmation_server as confirmation_server
from common.pipeline_context import init_full_pipeline_context, peerassist_stage_dir, write_json_file
from peerassist.confirmation_server import create_confirmation_server, render_confirmation_page
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
    assert "peerassistPdfGoToPage" in html
    assert "已跳转到 PDF 第" in html
    assert "data-pdf-page-rail" in html
    assert "buildPdfPageRail" in html
    assert "collectPdfPageConcernCounts" in html
    assert "annotatePdfPageRail" in html
    assert "syncCurrentPdfPage" in html
    assert "data-pdf-page-jump" in html
    assert "data-pdf-concern-count" in html
    assert "data-has-concern" in html
    assert "pdf-page-badge" in html
    assert "本页 ${count} 条审稿关注" in html
    assert "data-pdf-selection-tray" in html
    assert "data-pdf-selection-quote" in html
    assert "data-pdf-selection-copy" in html
    assert "data-pdf-selection-use" in html
    assert "peerassistSelectedEvidence" in html
    assert "capturePdfTextSelection" in html
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

    assert "data-pdf-selection-review" in html
    assert "data-pdf-selection-use" in html
    assert "基于选区审稿" in html
    assert "inline-button primary" in html


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


def test_confirmation_server_serves_source_pdf_when_available(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _seed_peerassist_stage(run_dir)
    (run_dir / "paper.pdf").write_bytes(b"%PDF-1.4\n% PeerAssist test PDF\n%%EOF\n")

    html = render_confirmation_page(run_dir=run_dir, paper_id="demo")

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
    assert "打开 PDF" in html
    assert "原始 PDF" in html
    assert "paper.pdf" in html

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
        with urllib.request.urlopen(f"{base_url}/paper.pdf", timeout=5) as response:
            body = response.read()
            content_type = response.headers["Content-Type"]
        assert content_type == "application/pdf"
        assert body.startswith(b"%PDF-1.4")
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

    def fake_chat_completion(**_kwargs) -> str:
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
            data=json.dumps({"selected_text": ""}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            result = json.loads(response.read().decode("utf-8"))

        assert result["structured_concern_count"] == 1
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

from __future__ import annotations

import json
from pathlib import Path

from common.pipeline_context import init_full_pipeline_context, parse_stage_dir
from common.pipeline_context import write_json_file
from peerassist.stage_runner import run_peerassist_stage


def test_run_peerassist_stage_fast_writes_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    init_full_pipeline_context(run_dir=run_dir)
    source_pdf = tmp_path / "paper.pdf"
    source_pdf.write_bytes(b"%PDF demo")
    markdown = tmp_path / "mineru_full.md"
    markdown.write_text("The success rate was 30/100 (40%).\n", encoding="utf-8")
    write_json_file(
        parse_stage_dir(run_dir) / "paper.json",
        {
            "source_pdf": str(source_pdf),
            "mineru_markdown_path": str(markdown),
            "mineru_content_list_path": "",
        },
    )

    result = run_peerassist_stage(
        repo_root=Path.cwd(),
        run_dir=run_dir,
        paper_key="demo",
        paper_pdf=source_pdf,
        mode="fast",
    )

    assert result.status == "ok"
    for key in (
        "evidence_ledger",
        "deterministic_checks",
        "capability_invocations",
        "agent_results",
        "concerns",
        "confirmation_bundle",
        "confirmation_review_queue",
        "human_confirmations",
        "tool_trace",
        "report_md",
        "report_en_md",
        "report_zh_md",
        "report_json",
    ):
        assert key in result.outputs
        assert Path(result.outputs[key]).exists()

    report_payload = json.loads(Path(result.outputs["report_json"]).read_text(encoding="utf-8"))
    assert Path(result.outputs["report_en_md"]).read_text(encoding="utf-8").startswith(
        "# PeerAssist Review Aid Report"
    )
    assert Path(result.outputs["report_zh_md"]).read_text(encoding="utf-8").startswith(
        "# PeerAssist 论文审核辅助报告"
    )
    assert report_payload["localized_report_paths"] == {
        "en": result.outputs["report_en_md"],
        "zh": result.outputs["report_zh_md"],
    }
    assert report_payload["parse_provider"]["provider_name"] == "mineru"
    assert report_payload["confirmation_bundle_path"] == result.outputs["confirmation_bundle"]
    assert report_payload["confirmation_review_queue_path"] == result.outputs[
        "confirmation_review_queue"
    ]
    assert report_payload["agent_results_path"] == result.outputs["agent_results"]
    assert report_payload["capability_invocations_path"] == result.outputs["capability_invocations"]
    assert report_payload["concerns"][0]["source_agent_ids"] == [
        "statistics_agent",
        "integrator_agent",
    ]
    invocations = json.loads(Path(result.outputs["capability_invocations"]).read_text(encoding="utf-8"))
    assert [row["capability_name"] for row in invocations["results"]] == [
        "percentage_consistency_check",
        "peerassist_local_agents",
    ]
    assert all(row["status"] == "completed" for row in invocations["results"])
    bundle = json.loads(Path(result.outputs["confirmation_bundle"]).read_text(encoding="utf-8"))
    assert bundle["groups"]["pending_human_confirmation"][0]["evidence"][0]["locator"]
    queue = json.loads(Path(result.outputs["confirmation_review_queue"]).read_text(encoding="utf-8"))
    assert queue["items"][0]["allowed_actions"] == [
        "confirm",
        "rewrite",
        "downgrade",
        "delete",
        "mark_pending",
    ]
    trace_text = Path(result.outputs["tool_trace"]).read_text(encoding="utf-8")
    assert "resolve_parse_provider" in trace_text
    assert "percentage_consistency_check" in trace_text


def test_run_peerassist_stage_off_is_skipped(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    init_full_pipeline_context(run_dir=run_dir)
    source_pdf = tmp_path / "paper.pdf"
    source_pdf.write_bytes(b"%PDF demo")

    result = run_peerassist_stage(
        repo_root=Path.cwd(),
        run_dir=run_dir,
        paper_key="demo",
        paper_pdf=source_pdf,
        mode="off",
    )

    assert result.status == "skipped"
    assert result.outputs == {}


def test_run_peerassist_stage_standard_warns_when_external_ocr_disabled(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    init_full_pipeline_context(run_dir=run_dir)
    source_pdf = tmp_path / "paper.pdf"
    source_pdf.write_bytes(b"%PDF demo")
    markdown = tmp_path / "mineru_full.md"
    markdown.write_text("The success rate was 30/100 (40%).\n", encoding="utf-8")
    write_json_file(
        parse_stage_dir(run_dir) / "paper.json",
        {
            "source_pdf": str(source_pdf),
            "mineru_markdown_path": str(markdown),
            "mineru_content_list_path": "",
        },
    )

    result = run_peerassist_stage(
        repo_root=Path.cwd(),
        run_dir=run_dir,
        paper_key="demo",
        paper_pdf=source_pdf,
        mode="standard",
    )

    payload = json.loads(Path(result.outputs["report_json"]).read_text(encoding="utf-8"))
    assert any("External OCR capabilities are not enabled" in warning for warning in payload["warnings"])

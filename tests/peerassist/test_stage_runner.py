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
        "agent_results",
        "concerns",
        "confirmation_bundle",
        "human_confirmations",
        "tool_trace",
        "report_md",
        "report_json",
    ):
        assert key in result.outputs
        assert Path(result.outputs[key]).exists()

    report_payload = json.loads(Path(result.outputs["report_json"]).read_text(encoding="utf-8"))
    assert report_payload["parse_provider"]["provider_name"] == "mineru"
    assert report_payload["confirmation_bundle_path"] == result.outputs["confirmation_bundle"]
    assert report_payload["agent_results_path"] == result.outputs["agent_results"]
    assert report_payload["concerns"][0]["source_agent_ids"] == [
        "statistics_agent",
        "integrator_agent",
    ]
    bundle = json.loads(Path(result.outputs["confirmation_bundle"]).read_text(encoding="utf-8"))
    assert bundle["groups"]["pending_human_confirmation"][0]["evidence"][0]["locator"]
    trace_text = Path(result.outputs["tool_trace"]).read_text(encoding="utf-8")
    assert "resolve_parse_provider" in trace_text


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

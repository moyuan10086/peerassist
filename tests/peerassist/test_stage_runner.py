from __future__ import annotations

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
        "concerns",
        "human_confirmations",
        "tool_trace",
        "report_md",
        "report_json",
    ):
        assert key in result.outputs
        assert Path(result.outputs[key]).exists()


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

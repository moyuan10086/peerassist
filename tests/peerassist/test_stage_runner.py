from __future__ import annotations

import json
import sys
from pathlib import Path

from common.pipeline_context import (
    init_full_pipeline_context,
    parse_stage_dir,
    refcheck_stage_dir,
    write_json_file,
)
from peerassist.stage_runner import run_peerassist_stage


def _stage_fixture(tmp_path: Path, markdown_text: str) -> tuple[Path, Path, Path]:
    run_dir = tmp_path / "run"
    init_full_pipeline_context(run_dir=run_dir)
    source_pdf = tmp_path / "paper.pdf"
    source_pdf.write_bytes(b"%PDF demo")
    markdown = tmp_path / "mineru_full.md"
    markdown.write_text(markdown_text, encoding="utf-8")
    write_json_file(
        parse_stage_dir(run_dir) / "paper.json",
        {
            "source_pdf": str(source_pdf),
            "mineru_markdown_path": str(markdown),
            "mineru_content_list_path": "",
        },
    )
    return run_dir, source_pdf, markdown


def test_run_peerassist_stage_writes_traceable_citation_audit(tmp_path: Path) -> None:
    run_dir, source_pdf, _markdown = _stage_fixture(
        tmp_path,
        "Prior work supports this finding [1].\n\n## References\n[1] A Study. 2024. doi:10.1000/a.\n",
    )
    write_json_file(
        refcheck_stage_dir(run_dir) / "reference_check.json",
        {
            "ok": True,
            "total_refs": 1,
            "errors": 0,
            "warnings": 1,
            "unverified": 0,
            "error_message": "",
            "issues": [
                {
                    "severity": "warning",
                    "type": "incomplete::missing_doi",
                    "reference_title": "A Study",
                    "reference_year": "2024",
                    "cited_url": "",
                    "verified_url": "https://records.example/a",
                    "details": "metadata correction",
                    "raw_reference": "[1] A Study. 2024. doi:10.1000/a.",
                    "corrected_bibtex": "@article{a, title = {Different Study}, year = {2024}, doi = {10.1000/a}}",
                }
            ],
            "error_details": [],
            "warning_details": [],
            "unverified_details": [],
            "report_file": "",
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
    assert Path(result.outputs["citation_audit"]).exists()
    ledger = json.loads(Path(result.outputs["evidence_ledger"]).read_text(encoding="utf-8"))
    assert any(item["type"] == "citation" and item["text"] == "[1]" for item in ledger["items"])
    audit = json.loads(Path(result.outputs["citation_audit"]).read_text(encoding="utf-8"))
    assert audit["findings"]
    assert audit["verifications"][0]["raw_response_artifact"] is not None
    artifact = Path(result.outputs["citation_audit"]).parent / audit["verifications"][0]["raw_response_artifact"]["path"]
    assert artifact.exists()
    concerns = json.loads(Path(result.outputs["concerns"]).read_text(encoding="utf-8"))["concerns"]
    citation_concerns = [concern for concern in concerns if concern["category"] == "citation"]
    assert citation_concerns
    assert len({concern["id"] for concern in concerns}) == len(concerns)
    trace = [json.loads(line) for line in Path(result.outputs["tool_trace"]).read_text(encoding="utf-8").splitlines()]
    assert {"citation_extraction", "citation_linking", "citation_verification", "citation_audit"} <= {
        row["call_id"] for row in trace
    }
    assert any(row["status"] == "artifact_created" for row in trace)
    report_payload = json.loads(Path(result.outputs["report_json"]).read_text(encoding="utf-8"))
    assert report_payload["citation_audit_path"] == result.outputs["citation_audit"]
    assert report_payload["citation_audit_summary"]["record_count"] == len(audit["records"])
    assert report_payload["citation_audit_summary"]["link_count"] == len(audit["links"])
    assert "## Citation Audit" in Path(result.outputs["report_en_md"]).read_text(encoding="utf-8")
    assert "## 引用核查与溯源" in Path(result.outputs["report_zh_md"]).read_text(encoding="utf-8")


def test_run_peerassist_stage_missing_refcheck_remains_ok_with_insufficient_evidence(tmp_path: Path) -> None:
    run_dir, source_pdf, _markdown = _stage_fixture(
        tmp_path,
        "Prior work supports this finding [1].\n\n## References\n[1] A Study. 2024.\n",
    )

    result = run_peerassist_stage(
        repo_root=Path.cwd(), run_dir=run_dir, paper_key="demo", paper_pdf=source_pdf, mode="fast"
    )

    assert result.status == "ok"
    audit = json.loads(Path(result.outputs["citation_audit"]).read_text(encoding="utf-8"))
    assert any(verification["status"] == "unavailable" for verification in audit["verifications"])
    assert any(finding["status"] == "insufficient_evidence" for finding in audit["findings"])


def test_run_peerassist_stage_malformed_refcheck_remains_ok_with_pending_failure(tmp_path: Path) -> None:
    run_dir, source_pdf, _markdown = _stage_fixture(
        tmp_path,
        "Prior work supports this finding [1].\n\n## References\n[1] A Study. 2024.\n",
    )
    refcheck_path = refcheck_stage_dir(run_dir) / "reference_check.json"
    refcheck_path.parent.mkdir(parents=True)
    refcheck_path.write_text('{"ok": true, broken', encoding="utf-8")

    result = run_peerassist_stage(
        repo_root=Path.cwd(), run_dir=run_dir, paper_key="demo", paper_pdf=source_pdf, mode="fast"
    )

    assert result.status == "ok"
    audit = json.loads(Path(result.outputs["citation_audit"]).read_text(encoding="utf-8"))
    assert any(verification["error_code"] == "adapter_schema_error" for verification in audit["verifications"])
    assert any(finding["status"] == "verification_failed" for finding in audit["findings"])
    concerns = json.loads(Path(result.outputs["concerns"]).read_text(encoding="utf-8"))["concerns"]
    assert any(
        concern["category"] == "citation" and concern["status"] == "pending_human_confirmation"
        for concern in concerns
    )


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
        "peerassist_eval_runtime",
        "report_md",
        "report_en_md",
        "report_zh_md",
        "report_json",
    ):
        assert key in result.outputs
        assert Path(result.outputs[key]).exists()

    report_payload = json.loads(Path(result.outputs["report_json"]).read_text(encoding="utf-8"))
    runtime_payload = json.loads(Path(result.outputs["peerassist_eval_runtime"]).read_text(encoding="utf-8"))
    assert runtime_payload["schema_version"] == "peerassist.eval_runtime.v1"
    assert runtime_payload["mode"] == "fast"
    assert runtime_payload["parse_success"] is True
    assert runtime_payload["latency_seconds"] >= 0
    assert runtime_payload["status"] == "ok"
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
        "deterministic_consistency_checks",
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
    assert "deterministic_consistency_checks" in trace_text


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


def test_run_peerassist_stage_registers_manifest_mcp_capabilities(tmp_path: Path) -> None:
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
    server_script = tmp_path / "fake_mcp_server.py"
    server_script.write_text("print('{}', flush=True)\n", encoding="utf-8")
    manifest_path = tmp_path / "mcp_servers.json"
    write_json_file(
        manifest_path,
        {
            "schema_version": "peerassist.mcp_servers.v1",
            "servers": [
                {
                    "name": "local_ref",
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [str(server_script)],
                    "tools": [
                        {
                            "name": "lookup_reference",
                            "description": "Look up local reference metadata.",
                            "permissions": ["read_artifact"],
                        }
                    ],
                }
            ],
        },
    )

    result = run_peerassist_stage(
        repo_root=Path.cwd(),
        run_dir=run_dir,
        paper_key="demo",
        paper_pdf=source_pdf,
        mode="fast",
        mcp_manifest_path=manifest_path,
    )

    payload = json.loads(Path(result.outputs["report_json"]).read_text(encoding="utf-8"))
    capabilities = {row["name"]: row for row in payload["capabilities"]}
    assert capabilities["mcp_local_ref_lookup_reference"]["source"] == "mcp"
    assert capabilities["mcp_local_ref_lookup_reference"]["metadata"]["transport"] == "stdio"


def test_run_peerassist_stage_registers_skill_root_capabilities(tmp_path: Path) -> None:
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
    skill_dir = tmp_path / "skills" / "method-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: method-skill
description: Methodology helper.
permissions:
  - read_artifact
---
FULL METHOD SKILL BODY
""",
        encoding="utf-8",
    )

    result = run_peerassist_stage(
        repo_root=Path.cwd(),
        run_dir=run_dir,
        paper_key="demo",
        paper_pdf=source_pdf,
        mode="fast",
        skill_roots=[tmp_path / "skills"],
    )

    payload = json.loads(Path(result.outputs["report_json"]).read_text(encoding="utf-8"))
    capabilities = {row["name"]: row for row in payload["capabilities"]}
    assert capabilities["method-skill"]["source"] == "skill"
    assert "FULL METHOD SKILL BODY" not in json.dumps(capabilities["method-skill"])

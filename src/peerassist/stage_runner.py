"""PeerAssist stage runner."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from common.pipeline_context import (
    ensure_full_pipeline_context,
    parse_stage_dir,
    peerassist_stage_dir,
    read_json_file,
    resolve_artifact_path,
    write_json_file,
)
from peerassist.confirmations import apply_confirmations
from peerassist.concerns import concerns_from_checks
from peerassist.deterministic_checks import run_deterministic_checks
from peerassist.evidence_ledger import build_evidence_ledger
from peerassist.report_export import export_peerassist_report
from peerassist.tool_trace import ToolTraceRecorder
from schemas.peerassist import HumanConfirmationAction, ToolTraceStatus
from schemas.stage import StageResult


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _default_confirmations_path(out_dir: Path) -> Path:
    return out_dir / "human_confirmations.json"


def _load_confirmations(path: Path) -> list[HumanConfirmationAction]:
    payload = read_json_file(path)
    rows = payload.get("actions") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []
    actions: list[HumanConfirmationAction] = []
    for row in rows:
        if isinstance(row, dict):
            actions.append(HumanConfirmationAction.model_validate(row))
    return actions


def _evidence_lookup(ledger_items: list[dict[str, Any]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for item in ledger_items:
        item_id = str(item.get("id") or "").strip()
        if not item_id:
            continue
        lookup[item_id] = str(item.get("locator") or item_id)
    return lookup


def run_peerassist_stage(
    *,
    repo_root: Path,
    run_dir: Path,
    paper_key: str,
    paper_pdf: Path,
    mode: str = "off",
) -> StageResult:
    normalized_mode = str(mode or "off").strip().lower()
    if normalized_mode == "off":
        return StageResult(status="skipped")
    if normalized_mode not in {"fast", "standard", "deep"}:
        return StageResult(status="failed", error=f"unknown PeerAssist mode: {mode}")

    ensure_full_pipeline_context(run_dir=run_dir, allow_standalone=True, stage="peerassist")
    out_dir = peerassist_stage_dir(run_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    trace = ToolTraceRecorder(out_dir / "tool_trace.jsonl")

    parse_payload = read_json_file(parse_stage_dir(run_dir) / "paper.json")
    mineru_markdown = resolve_artifact_path(repo_root, parse_payload.get("mineru_markdown_path"))
    mineru_content = resolve_artifact_path(repo_root, parse_payload.get("mineru_content_list_path"))

    trace.record(
        task_id=paper_key,
        call_id="build_evidence_ledger",
        agent_id="peerassist_stage",
        source="builtin",
        tool="build_evidence_ledger",
        status=ToolTraceStatus.STARTED,
        input_summary=f"mode={normalized_mode}",
    )
    ledger = build_evidence_ledger(
        paper_id=paper_key,
        source_pdf=paper_pdf,
        mineru_markdown_path=mineru_markdown,
        mineru_content_list_path=mineru_content,
    )
    ledger_path = out_dir / "evidence_ledger.json"
    write_json_file(ledger_path, ledger.model_dump(mode="json"))
    trace.record(
        task_id=paper_key,
        call_id="build_evidence_ledger",
        agent_id="peerassist_stage",
        source="builtin",
        tool="build_evidence_ledger",
        status=ToolTraceStatus.COMPLETED,
        output_summary=f"{len(ledger.items)} evidence items",
        artifact_ids=["evidence_ledger"],
    )

    checks = run_deterministic_checks(ledger)
    checks_path = out_dir / "deterministic_checks.json"
    write_json_file(
        checks_path,
        {
            "schema_version": "peerassist.deterministic_checks.v1",
            "mode": normalized_mode,
            "checks": [check.model_dump(mode="json") for check in checks],
        },
    )

    concerns = concerns_from_checks(checks)
    concerns_path = out_dir / "peerassist_concerns.json"
    write_json_file(
        concerns_path,
        {
            "schema_version": "peerassist.concerns.v1",
            "mode": normalized_mode,
            "concerns": [concern.model_dump(mode="json") for concern in concerns],
        },
    )

    confirmations_path = _default_confirmations_path(out_dir)
    if not confirmations_path.exists():
        write_json_file(
            confirmations_path,
            {"schema_version": "peerassist.human_confirmations.v1", "actions": []},
        )
    confirmed_concerns = apply_confirmations(concerns, _load_confirmations(confirmations_path))
    report_md, report_payload = export_peerassist_report(
        paper_id=paper_key,
        concerns=confirmed_concerns,
        evidence_lookup=_evidence_lookup([item.model_dump(mode="json") for item in ledger.items]),
    )
    report_md_path = out_dir / "peerassist_report.md"
    report_json_path = out_dir / "peerassist_report.json"
    _write_text(report_md_path, report_md)
    write_json_file(report_json_path, report_payload)

    if normalized_mode in {"standard", "deep"}:
        # Keep the mode explicit without pretending optional checks are complete.
        report_payload.setdefault("warnings", []).append(
            f"{normalized_mode} mode currently runs the fast local backbone; optional external checks are pending."
        )
        write_json_file(report_json_path, report_payload)

    return StageResult(
        status="ok",
        outputs={
            "evidence_ledger": str(ledger_path),
            "deterministic_checks": str(checks_path),
            "concerns": str(concerns_path),
            "human_confirmations": str(confirmations_path),
            "tool_trace": str(out_dir / "tool_trace.jsonl"),
            "report_md": str(report_md_path),
            "report_json": str(report_json_path),
        },
        extra={"mode": normalized_mode, "evidence_items": len(ledger.items), "checks": len(checks)},
    )

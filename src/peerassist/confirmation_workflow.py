"""File-backed human confirmation workflow for PeerAssist runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from common.pipeline_context import peerassist_stage_dir, read_json_file, write_json_file
from peerassist.confirmations import (
    apply_confirmations,
    confirmation_action_from_queue_decision,
)
from peerassist.report_export import export_peerassist_report
from schemas.peerassist import Concern, HumanConfirmationAction


def load_confirmation_state(*, run_dir: Path) -> dict[str, Any]:
    out_dir = peerassist_stage_dir(run_dir)
    queue = read_json_file(out_dir / "confirmation_review_queue.json")
    confirmations = read_json_file(out_dir / "human_confirmations.json")
    concerns_payload = read_json_file(out_dir / "peerassist_concerns.json")
    agent_results_payload = read_json_file(out_dir / "agent_results.json")
    invocations_payload = read_json_file(out_dir / "capability_invocations.json")
    actions = confirmations.get("actions") if isinstance(confirmations.get("actions"), list) else []
    items = queue.get("items") if isinstance(queue.get("items"), list) else []
    pending_count = sum(1 for item in items if str(item.get("status") or "").lower() == "pending_human_confirmation")
    if pending_count == 0 and items:
        pending_count = len(items)
    agent_runs = _agent_runs(agent_results_payload)
    capability_invocations = _capability_invocations(invocations_payload)
    tool_trace = _tool_trace_summary(out_dir / "tool_trace.jsonl")
    mode = str(
        queue.get("mode")
        or concerns_payload.get("mode")
        or agent_results_payload.get("mode")
        or invocations_payload.get("mode")
        or ""
    )
    return {
        "schema_version": "peerassist.confirmation_state.v1",
        "runtime": {
            "mode": mode,
            "stage_dir": str(out_dir),
            "queue_items": len(items),
            "agent_count": len(agent_runs),
            "tool_event_count": len(tool_trace["events"]),
            "capability_invocation_count": len(capability_invocations),
        },
        "queue": queue,
        "actions": actions,
        "actions_count": len(actions),
        "pending_count": pending_count,
        "agent_runs": agent_runs,
        "capability_invocations": capability_invocations,
        "tool_trace": tool_trace,
        "paths": {
            "queue": str(out_dir / "confirmation_review_queue.json"),
            "confirmations": str(out_dir / "human_confirmations.json"),
            "agent_results": str(out_dir / "agent_results.json"),
            "capability_invocations": str(out_dir / "capability_invocations.json"),
            "tool_trace": str(out_dir / "tool_trace.jsonl"),
        },
    }


def apply_confirmation_decision(
    *,
    run_dir: Path,
    paper_id: str,
    concern_id: str,
    action: str,
    reviewer_id: str,
    timestamp: str,
    previous_text: str = "",
    new_text: str = "",
    reason: str = "",
) -> dict[str, Any]:
    out_dir = peerassist_stage_dir(run_dir)
    confirmations_path = out_dir / "human_confirmations.json"
    confirmations = read_json_file(confirmations_path)
    rows = confirmations.get("actions") if isinstance(confirmations.get("actions"), list) else []

    confirmation_action = confirmation_action_from_queue_decision(
        concern_id=concern_id,
        action=action,
        reviewer_id=reviewer_id,
        timestamp=timestamp,
        previous_text=previous_text,
        new_text=new_text,
        reason=reason,
        metadata={"source": "peerassist_confirmation_workflow"},
    )
    candidate_rows = [*rows, confirmation_action.model_dump(mode="json")]
    _validate_confirmation_actions(out_dir=out_dir, paper_id=paper_id, rows=candidate_rows)
    rows = candidate_rows
    write_json_file(
        confirmations_path,
        {"schema_version": "peerassist.human_confirmations.v1", "actions": rows},
    )

    report_paths = refresh_confirmation_reports(run_dir=run_dir, paper_id=paper_id)
    return {
        "schema_version": "peerassist.confirmation_decision_result.v1",
        "action": confirmation_action.model_dump(mode="json"),
        "actions_count": len(rows),
        **report_paths,
    }


def _validate_confirmation_actions(*, out_dir: Path, paper_id: str, rows: list[dict[str, Any]]) -> None:
    concerns = _load_concerns(out_dir / "peerassist_concerns.json")
    evidence_lookup = _evidence_lookup_from_bundle(out_dir / "confirmation_bundle.json")
    actions = [HumanConfirmationAction.model_validate(row) for row in rows if isinstance(row, dict)]
    confirmed_concerns = apply_confirmations(concerns, actions)
    export_peerassist_report(
        paper_id=paper_id,
        concerns=confirmed_concerns,
        evidence_lookup=evidence_lookup,
        language="en",
    )


def refresh_confirmation_reports(*, run_dir: Path, paper_id: str) -> dict[str, str]:
    out_dir = peerassist_stage_dir(run_dir)
    concerns = _load_concerns(out_dir / "peerassist_concerns.json")
    actions = _load_actions(out_dir / "human_confirmations.json")
    evidence_lookup = _evidence_lookup_from_bundle(out_dir / "confirmation_bundle.json")
    confirmed_concerns = apply_confirmations(concerns, actions)

    report_md, report_payload = export_peerassist_report(
        paper_id=paper_id,
        concerns=confirmed_concerns,
        evidence_lookup=evidence_lookup,
        language="en",
    )
    report_zh_md, _ = export_peerassist_report(
        paper_id=paper_id,
        concerns=confirmed_concerns,
        evidence_lookup=evidence_lookup,
        language="zh",
    )
    report_md_path = out_dir / "peerassist_report.md"
    report_en_md_path = out_dir / "peerassist_report.en.md"
    report_zh_md_path = out_dir / "peerassist_report.zh.md"
    report_json_path = out_dir / "peerassist_report.json"

    report_md_path.write_text(report_md, encoding="utf-8")
    report_en_md_path.write_text(report_md, encoding="utf-8")
    report_zh_md_path.write_text(report_zh_md, encoding="utf-8")
    report_payload["localized_report_paths"] = {
        "en": str(report_en_md_path),
        "zh": str(report_zh_md_path),
    }
    report_payload["confirmation_workflow_refreshed"] = True
    write_json_file(report_json_path, report_payload)
    return {
        "report_md": str(report_md_path),
        "report_en_md": str(report_en_md_path),
        "report_zh_md": str(report_zh_md_path),
        "report_json": str(report_json_path),
    }


def _load_concerns(path: Path) -> list[Concern]:
    payload = read_json_file(path)
    rows = payload.get("concerns") if isinstance(payload.get("concerns"), list) else []
    return [Concern.model_validate(row) for row in rows if isinstance(row, dict)]


def _load_actions(path: Path) -> list[HumanConfirmationAction]:
    payload = read_json_file(path)
    rows = payload.get("actions") if isinstance(payload.get("actions"), list) else []
    return [HumanConfirmationAction.model_validate(row) for row in rows if isinstance(row, dict)]


def _agent_runs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("results") if isinstance(payload.get("results"), list) else []
    runs: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        drafts = row.get("drafts") if isinstance(row.get("drafts"), list) else []
        warnings = row.get("warnings") if isinstance(row.get("warnings"), list) else []
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        runs.append(
            {
                "agent_id": str(row.get("agent_id") or ""),
                "status": str(row.get("status") or ""),
                "draft_count": len(drafts),
                "warning_count": len(warnings),
                "warnings": [str(item) for item in warnings],
                "metadata": metadata,
            }
        )
    return runs


def _capability_invocations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("results") if isinstance(payload.get("results"), list) else []
    invocations: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        invocations.append(
            {
                "task_id": str(row.get("task_id") or ""),
                "call_id": str(row.get("call_id") or ""),
                "agent_id": str(row.get("agent_id") or ""),
                "capability_name": str(row.get("capability_name") or ""),
                "source": str(row.get("source") or ""),
                "status": str(row.get("status") or ""),
                "attempts": int(row.get("attempts") or 0),
                "duration_ms": row.get("duration_ms"),
                "artifact_ids": _string_list(row.get("artifact_ids")),
                "evidence_ids": _string_list(row.get("evidence_ids")),
                "error_code": str(row.get("error_code") or ""),
                "error_message": str(row.get("error_message") or ""),
            }
        )
    return invocations


def _tool_trace_summary(path: Path) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    counts_by_status: dict[str, int] = {}
    latest_status_by_call: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except FileNotFoundError:
        lines = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        event = {
            "task_id": str(row.get("task_id") or ""),
            "call_id": str(row.get("call_id") or ""),
            "agent_id": str(row.get("agent_id") or ""),
            "source": str(row.get("source") or ""),
            "tool": str(row.get("tool") or ""),
            "status": str(row.get("status") or ""),
            "ts": str(row.get("ts") or ""),
            "input_summary": str(row.get("input_summary") or ""),
            "output_summary": str(row.get("output_summary") or ""),
            "artifact_ids": _string_list(row.get("artifact_ids")),
            "duration_ms": row.get("duration_ms"),
            "error_code": str(row.get("error_code") or ""),
            "evidence_ids": _string_list(row.get("evidence_ids")),
        }
        events.append(event)
        status = event["status"]
        call_id = event["call_id"]
        if status:
            counts_by_status[status] = counts_by_status.get(status, 0) + 1
        if call_id:
            latest_status_by_call[call_id] = status
    return {
        "events": events,
        "counts_by_status": counts_by_status,
        "latest_status_by_call": latest_status_by_call,
    }


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _evidence_lookup_from_bundle(path: Path) -> dict[str, str]:
    payload = read_json_file(path)
    groups = payload.get("groups") if isinstance(payload.get("groups"), dict) else {}
    lookup: dict[str, str] = {}
    for rows in groups.values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            evidence = row.get("evidence") if isinstance(row.get("evidence"), list) else []
            for item in evidence:
                if isinstance(item, dict) and item.get("id"):
                    lookup[str(item["id"])] = str(item.get("locator") or item["id"])
    return lookup

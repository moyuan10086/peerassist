"""File-backed human confirmation workflow for PeerAssist runs."""

from __future__ import annotations

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
    actions = confirmations.get("actions") if isinstance(confirmations.get("actions"), list) else []
    items = queue.get("items") if isinstance(queue.get("items"), list) else []
    pending_count = sum(1 for item in items if str(item.get("status") or "").lower() == "pending_human_confirmation")
    if pending_count == 0 and items:
        pending_count = len(items)
    return {
        "schema_version": "peerassist.confirmation_state.v1",
        "queue": queue,
        "actions": actions,
        "actions_count": len(actions),
        "pending_count": pending_count,
        "paths": {
            "queue": str(out_dir / "confirmation_review_queue.json"),
            "confirmations": str(out_dir / "human_confirmations.json"),
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
    rows.append(confirmation_action.model_dump(mode="json"))
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

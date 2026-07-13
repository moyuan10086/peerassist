"""File-backed human confirmation workflow for PeerAssist runs."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from common.pipeline_context import peerassist_stage_dir, read_json_file, write_json_file
from common.storage import exclusive_file_lock, write_json_atomic, write_text_atomic
from peerassist.confirmations import (
    apply_confirmations,
    confirmation_action_from_queue_decision,
)
from peerassist.report_export import export_peerassist_report
from schemas.citation import CitationAudit
from schemas.peerassist import (
    Concern,
    ConcernLevel,
    ConcernStatus,
    FindingImportance,
    HumanConfirmationAction,
)


def _workflow_lock(out_dir: Path) -> Path:
    return out_dir / ".confirmation-finalize.lock"


def _confirmation_revision(payload: dict[str, Any]) -> int:
    try:
        return max(0, int(payload.get("revision") or 0))
    except (TypeError, ValueError):
        return 0


def _mutation_revision(payload: dict[str, Any]) -> int:
    try:
        return max(0, int(payload.get("mutation_revision", payload.get("revision") or 0)))
    except (TypeError, ValueError):
        return 0


def load_confirmation_state(*, run_dir: Path) -> dict[str, Any]:
    out_dir = peerassist_stage_dir(run_dir)
    queue = read_json_file(out_dir / "confirmation_review_queue.json")
    confirmations = read_json_file(out_dir / "human_confirmations.json")
    concerns_payload = read_json_file(out_dir / "peerassist_concerns.json")
    evidence_ledger_payload = read_json_file(out_dir / "evidence_ledger.json")
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
        "confirmation_revision": _confirmation_revision(confirmations),
        "pending_count": pending_count,
        "agent_runs": agent_runs,
        "capability_invocations": capability_invocations,
        "tool_trace": tool_trace,
        "evidence_preview": _evidence_preview(evidence_ledger_payload),
        "paths": {
            "evidence_ledger": str(out_dir / "evidence_ledger.json"),
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
    expected_revision: int | None = None,
    expected_finding_lineage_id: str = "",
    expected_finding_id: str = "",
    expected_finding_revision: int | None = None,
    before_commit: Callable[[], None] | None = None,
) -> dict[str, Any]:
    out_dir = peerassist_stage_dir(run_dir)
    confirmations_path = out_dir / "human_confirmations.json"
    with exclusive_file_lock(_workflow_lock(out_dir)):
        confirmations = read_json_file(confirmations_path)
    observed_revision = _confirmation_revision(confirmations)
    observed_mutation_revision = _mutation_revision(confirmations)
    if expected_revision is not None and observed_revision != expected_revision:
        return _revision_conflict(expected_revision, observed_revision)
    rows = confirmations.get("actions") if isinstance(confirmations.get("actions"), list) else []
    concern = _find_concern(out_dir / "peerassist_concerns.json", concern_id)
    expected_binding = (
        expected_finding_lineage_id,
        expected_finding_id,
        expected_finding_revision,
    )
    if any(value not in ("", None) for value in expected_binding) and expected_binding != (
        concern.finding_lineage_id,
        concern.finding_id,
        concern.revision,
    ):
        return _finding_binding_conflict(concern)

    confirmation_action = confirmation_action_from_queue_decision(
        concern_id=concern_id,
        action=action,
        reviewer_id=reviewer_id,
        timestamp=timestamp,
        previous_text=previous_text,
        new_text=new_text,
        reason=reason,
        finding_lineage_id=concern.finding_lineage_id,
        finding_id=concern.finding_id,
        revision=concern.revision,
        metadata={"source": "peerassist_confirmation_workflow"},
    )
    candidate_rows = [*rows, confirmation_action.model_dump(mode="json")]
    _validate_confirmation_actions(out_dir=out_dir, paper_id=paper_id, rows=candidate_rows)
    if before_commit is not None:
        before_commit()
    with exclusive_file_lock(_workflow_lock(out_dir)):
        current = read_json_file(confirmations_path)
        current_revision = _confirmation_revision(current)
        current_mutation_revision = _mutation_revision(current)
        if (
            current_revision != observed_revision
            or current_mutation_revision != observed_mutation_revision
        ):
            return _revision_conflict(observed_revision, current_revision)
        current_concern = _find_concern(out_dir / "peerassist_concerns.json", concern_id)
        if (
            current_concern.finding_lineage_id,
            current_concern.finding_id,
            current_concern.revision,
        ) != (
            concern.finding_lineage_id,
            concern.finding_id,
            concern.revision,
        ):
            return _finding_binding_conflict(current_concern)
        rows = candidate_rows
        committed_revision = observed_revision + 1
        write_json_atomic(
            confirmations_path,
            {
                **current,
                "schema_version": "peerassist.human_confirmations.v2",
                "revision": committed_revision,
                "mutation_revision": observed_mutation_revision + 1,
                "actions": rows,
            },
        )

    report_paths = refresh_confirmation_reports(run_dir=run_dir, paper_id=paper_id)
    return {
        "schema_version": "peerassist.confirmation_decision_result.v1",
        "status": "ok",
        "action": confirmation_action.model_dump(mode="json"),
        "actions_count": len(rows),
        "confirmation_revision": committed_revision,
        **report_paths,
    }


def finalize_confirmed_report(
    *,
    run_dir: Path,
    paper_id: str,
    expected_confirmation_revision: int,
    override_reason: str = "",
    before_pointer_commit: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Freeze one confirmation revision and publish an immutable localized report."""

    out_dir = peerassist_stage_dir(run_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    confirmations_path = out_dir / "human_confirmations.json"
    pointer_path = out_dir / "current_final_report.json"
    with exclusive_file_lock(_workflow_lock(out_dir)):
        pointer = read_json_file(pointer_path)
        confirmations = read_json_file(confirmations_path)
        observed_revision = _confirmation_revision(confirmations)
        observed_mutation_revision = _mutation_revision(confirmations)
    if observed_revision != expected_confirmation_revision:
        return _revision_conflict(expected_confirmation_revision, observed_revision)

    concerns = _load_concerns(out_dir / "peerassist_concerns.json")
    actions = _actions_from_payload(confirmations)
    confirmed_concerns = apply_confirmations(concerns, actions)
    finding_revisions, finding_snapshot_sha256 = _finding_snapshot(confirmed_concerns)
    confirmation_actions = [action.model_dump(mode="json") for action in actions]
    confirmation_actions_sha256 = _canonical_sha256(confirmation_actions)
    unresolved_core = [
        concern
        for concern in confirmed_concerns
        if concern.status is ConcernStatus.PENDING_HUMAN_CONFIRMATION and _is_core(concern)
    ]
    normalized_override = str(override_reason or "").strip()
    if unresolved_core and not normalized_override:
        return {
            "schema_version": "peerassist.finalize_result.v1",
            "status": "blocked",
            "error_code": "unresolved_core_findings",
            "unresolved_core_count": len(unresolved_core),
            "unresolved_finding_ids": [concern.finding_id for concern in unresolved_core],
            "confirmation_revision": observed_revision,
        }
    if pointer.get("confirmation_revision") == expected_confirmation_revision:
        current_manifest_path = out_dir / str(pointer.get("manifest_path") or "")
        current_manifest = read_json_file(current_manifest_path)
        if (
            current_manifest.get("finding_snapshot_sha256") == finding_snapshot_sha256
            and current_manifest.get("confirmation_actions_sha256")
            == confirmation_actions_sha256
        ):
            return _finalize_success(current_manifest_path, idempotent=True)
        return _finding_snapshot_conflict(observed_revision)

    report_version = f"r{observed_revision:06d}"
    reports_dir = out_dir / "reports"
    temporary_dir = reports_dir / f".{report_version}.{uuid4().hex}.tmp"
    final_dir = reports_dir / report_version
    try:
        temporary_dir.mkdir(parents=True, exist_ok=False)
        evidence_lookup = _evidence_lookup_from_bundle(out_dir / "confirmation_bundle.json")
        citation_audit, citation_audit_path = _load_citation_audit(out_dir / "citation_audit.json")
        en_md, en_payload = export_peerassist_report(
            paper_id=paper_id,
            concerns=confirmed_concerns,
            evidence_lookup=evidence_lookup,
            language="en",
            citation_audit=citation_audit,
            citation_audit_path=citation_audit_path,
            report_status="final",
            confirmation_revision=observed_revision,
        )
        zh_md, zh_payload = export_peerassist_report(
            paper_id=paper_id,
            concerns=confirmed_concerns,
            evidence_lookup=evidence_lookup,
            language="zh",
            citation_audit=citation_audit,
            citation_audit_path=citation_audit_path,
            report_status="final",
            confirmation_revision=observed_revision,
        )
        artifacts = {
            "report_en_md": f"reports/{report_version}/report.en.md",
            "report_zh_md": f"reports/{report_version}/report.zh.md",
            "report_en_json": f"reports/{report_version}/report.en.json",
            "report_zh_json": f"reports/{report_version}/report.zh.json",
        }
        write_text_atomic(temporary_dir / "report.en.md", en_md)
        write_text_atomic(temporary_dir / "report.zh.md", zh_md)
        write_json_atomic(temporary_dir / "report.en.json", en_payload)
        write_json_atomic(temporary_dir / "report.zh.json", zh_payload)
        artifact_sha256 = {
            name: hashlib.sha256((temporary_dir / Path(path).name).read_bytes()).hexdigest()
            for name, path in artifacts.items()
        }
        manifest = {
            "schema_version": "peerassist.final_report_manifest.v1",
            "paper_id": paper_id,
            "report_version": report_version,
            "confirmation_revision": observed_revision,
            "created_at": datetime.now(UTC).isoformat(),
            "override_reason": normalized_override or None,
            "confirmation_actions": confirmation_actions,
            "confirmation_actions_sha256": confirmation_actions_sha256,
            "finding_revisions": finding_revisions,
            "finding_snapshot_sha256": finding_snapshot_sha256,
            "artifacts": artifacts,
            "artifact_sha256": artifact_sha256,
        }
        write_json_atomic(temporary_dir / "manifest.json", manifest)
    except Exception as exc:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        return {
            "schema_version": "peerassist.finalize_result.v1",
            "status": "failed",
            "error_code": "report_export_failed",
            "error": str(exc),
            "confirmation_revision": observed_revision,
        }

    if before_pointer_commit is not None:
        before_pointer_commit()
    with exclusive_file_lock(_workflow_lock(out_dir)):
        current = read_json_file(confirmations_path)
        current_revision = _confirmation_revision(current)
        current_mutation_revision = _mutation_revision(current)
        if (
            current_revision != observed_revision
            or current_mutation_revision != observed_mutation_revision
        ):
            shutil.rmtree(temporary_dir, ignore_errors=True)
            return _revision_conflict(observed_revision, current_revision)
        current_concerns = _load_concerns(out_dir / "peerassist_concerns.json")
        current_actions = _actions_from_payload(current)
        current_confirmed = apply_confirmations(current_concerns, current_actions)
        _, current_finding_snapshot_sha256 = _finding_snapshot(current_confirmed)
        current_confirmation_actions_sha256 = _canonical_sha256(
            [action.model_dump(mode="json") for action in current_actions]
        )
        if (
            current_finding_snapshot_sha256 != finding_snapshot_sha256
            or current_confirmation_actions_sha256 != confirmation_actions_sha256
        ):
            shutil.rmtree(temporary_dir, ignore_errors=True)
            return _finding_snapshot_conflict(observed_revision)
        pointer_before = read_json_file(pointer_path)
        published_new_dir = False
        if final_dir.exists():
            shutil.rmtree(temporary_dir, ignore_errors=True)
            existing_manifest = read_json_file(final_dir / "manifest.json")
            if (
                existing_manifest.get("confirmation_revision") != observed_revision
                or existing_manifest.get("finding_snapshot_sha256")
                != finding_snapshot_sha256
                or existing_manifest.get("confirmation_actions_sha256")
                != confirmation_actions_sha256
            ):
                return {
                    "schema_version": "peerassist.finalize_result.v1",
                    "status": "failed",
                    "error_code": "report_version_conflict",
                    "confirmation_revision": observed_revision,
                }
        else:
            reports_dir.mkdir(parents=True, exist_ok=True)
            temporary_dir.replace(final_dir)
            published_new_dir = True
        manifest_path = final_dir / "manifest.json"
        pointer_payload = {
            "schema_version": "peerassist.current_final_report.v1",
            "report_version": report_version,
            "confirmation_revision": observed_revision,
            "manifest_path": str(manifest_path.relative_to(out_dir)),
        }
        try:
            write_json_atomic(pointer_path, pointer_payload)
            write_json_atomic(
                confirmations_path,
                {
                    **current,
                    "schema_version": "peerassist.human_confirmations.v2",
                    "revision": observed_revision,
                    "mutation_revision": observed_mutation_revision + 1,
                    "last_finalized_confirmation_revision": observed_revision,
                },
            )
        except Exception as exc:
            if pointer_before:
                write_json_atomic(pointer_path, pointer_before)
            else:
                pointer_path.unlink(missing_ok=True)
            if published_new_dir:
                shutil.rmtree(final_dir, ignore_errors=True)
            return {
                "schema_version": "peerassist.finalize_result.v1",
                "status": "failed",
                "error_code": "report_commit_failed",
                "error": str(exc),
                "confirmation_revision": observed_revision,
            }
    return _finalize_success(manifest_path, idempotent=False)


def _finalize_success(manifest_path: Path, *, idempotent: bool) -> dict[str, Any]:
    manifest = read_json_file(manifest_path)
    return {
        "schema_version": "peerassist.finalize_result.v1",
        "status": "ok",
        "idempotent": idempotent,
        "report_version": str(manifest.get("report_version") or ""),
        "confirmation_revision": int(manifest.get("confirmation_revision") or 0),
        "manifest_path": str(manifest_path),
        "artifacts": dict(manifest.get("artifacts") or {}),
    }


def _revision_conflict(expected: int, current: int) -> dict[str, Any]:
    return {
        "schema_version": "peerassist.revision_conflict.v1",
        "status": "revision_conflict",
        "error_code": "revision_conflict",
        "expected_revision": expected,
        "current_revision": current,
    }


def _finding_snapshot_conflict(confirmation_revision: int) -> dict[str, Any]:
    return {
        "schema_version": "peerassist.revision_conflict.v1",
        "status": "revision_conflict",
        "error_code": "finding_snapshot_conflict",
        "expected_revision": confirmation_revision,
        "current_revision": confirmation_revision,
    }


def _finding_binding_conflict(concern: Concern) -> dict[str, Any]:
    return {
        "schema_version": "peerassist.revision_conflict.v1",
        "status": "revision_conflict",
        "error_code": "finding_binding_conflict",
        "current_finding_lineage_id": concern.finding_lineage_id,
        "current_finding_id": concern.finding_id,
        "current_finding_revision": concern.revision,
    }


def _canonical_sha256(payload: Any) -> str:
    content = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _finding_snapshot(concerns: list[Concern]) -> tuple[list[dict[str, Any]], str]:
    rows = [
        {
            "concern_id": concern.id,
            "finding_lineage_id": concern.finding_lineage_id,
            "finding_id": concern.finding_id,
            "revision": concern.revision,
            "display_revision": concern.display_revision,
            "supersedes": list(concern.supersedes),
            "reconciles": list(concern.reconciles),
            "status": concern.status.value,
            "importance": concern.importance.value,
            "level": concern.level.value,
            "category": concern.category,
            "title": concern.title,
            "evidence_ids": list(concern.evidence_ids),
            "impact": concern.impact,
            "benign_explanation": concern.benign_explanation,
            "author_action": concern.author_action,
            "source_agent_ids": list(concern.source_agent_ids),
            "source_check_ids": list(concern.source_check_ids),
        }
        for concern in concerns
    ]
    rows.sort(
        key=lambda row: (
            str(row["finding_lineage_id"]),
            int(row["revision"]),
            str(row["finding_id"]),
            str(row["concern_id"]),
        )
    )
    return rows, _canonical_sha256(rows)


def _find_concern(path: Path, concern_id: str) -> Concern:
    for concern in _load_concerns(path):
        if concern.id == concern_id:
            return concern
    raise ValueError(f"unknown concern: {concern_id}")


def _actions_from_payload(payload: dict[str, Any]) -> list[HumanConfirmationAction]:
    rows = payload.get("actions") if isinstance(payload.get("actions"), list) else []
    return [HumanConfirmationAction.model_validate(row) for row in rows if isinstance(row, dict)]


def _is_core(concern: Concern) -> bool:
    return (
        concern.importance is FindingImportance.CORE
        or concern.level is ConcernLevel.MAJOR_CONCERN
        or str(concern.metadata.get("importance") or "").lower() == "core"
    )


def _load_citation_audit(path: Path) -> tuple[CitationAudit | None, str]:
    payload = read_json_file(path)
    if not payload:
        return None, ""
    return CitationAudit.model_validate(payload), str(path)


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
    report_payload["report_status"] = "draft"
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


def _evidence_preview(payload: dict[str, Any]) -> list[dict[str, str]]:
    rows = payload.get("items") if isinstance(payload.get("items"), list) else []
    preview: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        item_type = str(row.get("type") or "")
        if item_type not in {"section", "text_span", "figure_caption", "table"}:
            continue
        preview.append(
            {
                "id": str(row.get("id") or ""),
                "type": item_type,
                "locator": str(row.get("locator") or ""),
                "text": text,
                "section": str(row.get("section") or ""),
            }
        )
        if len(preview) >= 8:
            break
    return preview


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

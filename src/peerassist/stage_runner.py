"""PeerAssist stage runner."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from common.pipeline_context import (
    ensure_full_pipeline_context,
    parse_stage_dir,
    peerassist_stage_dir,
    read_json_file,
    write_json_file,
)
from peerassist.agents import integrate_agent_results, run_peerassist_agents
from peerassist.capabilities import default_capability_registry
from peerassist.confirmations import apply_confirmations, build_confirmation_bundle
from peerassist.deterministic_checks import run_deterministic_checks
from peerassist.evidence_ledger import build_evidence_ledger
from peerassist.ocr_providers import MinerUParseProvider
from peerassist.report_export import export_peerassist_report
from peerassist.tool_invocations import CapabilityInvocationRequest, CapabilityInvoker
from peerassist.tool_trace import ToolTraceRecorder
from schemas.peerassist import (
    AgentReviewResult,
    DeterministicCheck,
    HumanConfirmationAction,
    ToolTraceStatus,
)
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
    trace.record(
        task_id=paper_key,
        call_id="resolve_parse_provider",
        agent_id="peerassist_stage",
        source="builtin",
        tool="mineru_parse_artifacts",
        status=ToolTraceStatus.STARTED,
        input_summary="resolve FactReview parse artifacts",
    )
    parse_provider = MinerUParseProvider()
    parse_result = parse_provider.from_parse_payload(parse_payload, repo_root=repo_root)
    trace.record(
        task_id=paper_key,
        call_id="resolve_parse_provider",
        agent_id="peerassist_stage",
        source="builtin",
        tool="mineru_parse_artifacts",
        status=ToolTraceStatus.COMPLETED,
        output_summary=f"provider={parse_result.provider_name}; warnings={len(parse_result.warnings)}",
    )

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
        mineru_markdown_path=parse_result.markdown_path,
        mineru_content_list_path=parse_result.content_list_path,
        provider_name=parse_result.provider_name,
        provider_metadata=parse_result.metadata,
        provider_warnings=parse_result.warnings,
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

    registry = default_capability_registry()
    exposed = registry.expose(mode=normalized_mode)
    capability_names = [capability.name for capability in exposed]
    invocation_results = []
    invoker = CapabilityInvoker(
        registry=registry,
        trace=trace,
        handlers={
            "percentage_consistency_check": lambda _payload: {
                "checks": [
                    check.model_dump(mode="json") for check in run_deterministic_checks(ledger)
                ],
                "artifact_ids": ["deterministic_checks"],
            }
        },
    )

    deterministic_invocation = invoker.invoke(
        CapabilityInvocationRequest(
            task_id=paper_key,
            call_id="percentage_consistency_check",
            agent_id="statistics_agent",
            capability_name="percentage_consistency_check",
            input_summary="run deterministic percentage consistency checks",
            payload={"evidence_items": len(ledger.items)},
            approved=True,
        )
    )
    invocation_results.append(deterministic_invocation)
    if deterministic_invocation.status is not ToolTraceStatus.COMPLETED:
        return StageResult(status="failed", error=deterministic_invocation.error_message)
    checks = [
        DeterministicCheck.model_validate(row)
        for row in deterministic_invocation.output.get("checks", [])
        if isinstance(row, dict)
    ]
    checks_path = out_dir / "deterministic_checks.json"
    write_json_file(
        checks_path,
        {
            "schema_version": "peerassist.deterministic_checks.v1",
            "mode": normalized_mode,
            "checks": [check.model_dump(mode="json") for check in checks],
        },
    )

    invoker.handlers["peerassist_local_agents"] = lambda _payload: {
        "results": [
            result.model_dump(mode="json")
            for result in run_peerassist_agents(
                mode=normalized_mode,
                ledger=ledger,
                checks=checks,
                capability_names=capability_names,
            )
        ],
        "artifact_ids": ["agent_results"],
    }
    agents_invocation = invoker.invoke(
        CapabilityInvocationRequest(
            task_id=paper_key,
            call_id="peerassist_local_agents",
            agent_id="peerassist_stage",
            capability_name="peerassist_local_agents",
            input_summary=f"run local PeerAssist agents in {normalized_mode} mode",
            payload={"check_count": len(checks), "capability_names": capability_names},
            approved=True,
        )
    )
    invocation_results.append(agents_invocation)
    if agents_invocation.status is not ToolTraceStatus.COMPLETED:
        return StageResult(status="failed", error=agents_invocation.error_message)
    agent_results = [
        AgentReviewResult.model_validate(row)
        for row in agents_invocation.output.get("results", [])
        if isinstance(row, dict)
    ]
    capability_invocations_path = out_dir / "capability_invocations.json"
    write_json_file(
        capability_invocations_path,
        {
            "schema_version": "peerassist.capability_invocations.v1",
            "mode": normalized_mode,
            "results": [result.model_dump(mode="json") for result in invocation_results],
        },
    )
    agent_results_path = out_dir / "agent_results.json"
    write_json_file(
        agent_results_path,
        {
            "schema_version": "peerassist.agent_results.v1",
            "mode": normalized_mode,
            "results": [result.model_dump(mode="json") for result in agent_results],
        },
    )

    concerns = integrate_agent_results(agent_results)
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
    evidence_lookup = _evidence_lookup([item.model_dump(mode="json") for item in ledger.items])
    confirmation_bundle_path = out_dir / "confirmation_bundle.json"
    write_json_file(
        confirmation_bundle_path,
        build_confirmation_bundle(concerns=concerns, evidence_lookup=evidence_lookup),
    )

    confirmed_concerns = apply_confirmations(concerns, _load_confirmations(confirmations_path))
    report_md, report_payload = export_peerassist_report(
        paper_id=paper_key,
        concerns=confirmed_concerns,
        evidence_lookup=evidence_lookup,
        language="en",
    )
    report_zh_md, _report_zh_payload = export_peerassist_report(
        paper_id=paper_key,
        concerns=confirmed_concerns,
        evidence_lookup=evidence_lookup,
        language="zh",
    )
    report_payload["agent_results_path"] = str(agent_results_path)
    report_payload["capability_invocations_path"] = str(capability_invocations_path)
    report_payload["confirmation_bundle_path"] = str(confirmation_bundle_path)
    report_payload["parse_provider"] = {
        "provider_name": parse_result.provider_name,
        "kind": parse_result.kind.value,
        "external_upload_required": parse_result.external_upload_required,
        "enabled": parse_result.enabled,
        "warnings": list(parse_result.warnings),
        "metadata": dict(parse_result.metadata),
    }
    report_payload["capabilities"] = [capability.to_exposed_schema() for capability in exposed]
    if normalized_mode in {"standard", "deep"}:
        report_payload.setdefault("warnings", []).append(
            "External OCR capabilities are not enabled; using local FactReview/MinerU parse artifacts."
        )
    report_md_path = out_dir / "peerassist_report.md"
    report_en_md_path = out_dir / "peerassist_report.en.md"
    report_zh_md_path = out_dir / "peerassist_report.zh.md"
    report_json_path = out_dir / "peerassist_report.json"
    _write_text(report_md_path, report_md)
    _write_text(report_en_md_path, report_md)
    _write_text(report_zh_md_path, report_zh_md)
    report_payload["localized_report_paths"] = {
        "en": str(report_en_md_path),
        "zh": str(report_zh_md_path),
    }
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
            "capability_invocations": str(capability_invocations_path),
            "agent_results": str(agent_results_path),
            "concerns": str(concerns_path),
            "confirmation_bundle": str(confirmation_bundle_path),
            "human_confirmations": str(confirmations_path),
            "tool_trace": str(out_dir / "tool_trace.jsonl"),
            "report_md": str(report_md_path),
            "report_en_md": str(report_en_md_path),
            "report_zh_md": str(report_zh_md_path),
            "report_json": str(report_json_path),
        },
        extra={"mode": normalized_mode, "evidence_items": len(ledger.items), "checks": len(checks)},
    )

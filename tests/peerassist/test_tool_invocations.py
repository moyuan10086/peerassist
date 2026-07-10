from __future__ import annotations

import json

from peerassist.capabilities import (
    CapabilityRegistry,
    CapabilitySource,
    CapabilitySpec,
    PermissionClass,
)
from peerassist.tool_invocations import CapabilityInvocationRequest, CapabilityInvoker
from peerassist.tool_trace import ToolTraceRecorder
from schemas.peerassist import ToolTraceStatus


def _skill_registry() -> CapabilityRegistry:
    return CapabilityRegistry(
        [
            CapabilitySpec(
                name="local_skill_check",
                description="Run a local skill against already extracted evidence.",
                source=CapabilitySource.SKILL,
                permissions=[PermissionClass.READ_ARTIFACT],
                input_summary="evidence ids",
                output_summary="structured check result",
            ),
            CapabilitySpec(
                name="external_mcp_lookup",
                description="Query an external MCP server.",
                source=CapabilitySource.MCP,
                permissions=[PermissionClass.EXTERNAL_REQUEST],
                input_summary="citation DOI",
                output_summary="citation metadata",
            ),
        ]
    )


def _rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_invoker_records_approval_required_without_calling_handler(tmp_path) -> None:
    called = False

    def handler(_payload):
        nonlocal called
        called = True
        return {"ok": True}

    trace_path = tmp_path / "tool_trace.jsonl"
    invoker = CapabilityInvoker(
        registry=_skill_registry(),
        trace=ToolTraceRecorder(trace_path),
        handlers={"external_mcp_lookup": handler},
    )

    result = invoker.invoke(
        CapabilityInvocationRequest(
            task_id="paper-1",
            call_id="call-external",
            agent_id="citation_agent",
            capability_name="external_mcp_lookup",
            input_summary="lookup DOI",
            payload={"doi": "10.0000/demo"},
        )
    )

    assert called is False
    assert result.status is ToolTraceStatus.APPROVAL_REQUIRED
    assert result.error_code == "approval_required"
    rows = _rows(trace_path)
    assert [row["status"] for row in rows] == ["queued", "approval_required"]
    assert rows[-1]["source"] == "mcp"


def test_invoker_records_successful_skill_call_and_validates_dict_output(tmp_path) -> None:
    trace_path = tmp_path / "tool_trace.jsonl"
    invoker = CapabilityInvoker(
        registry=_skill_registry(),
        trace=ToolTraceRecorder(trace_path),
        handlers={"local_skill_check": lambda payload: {"accepted": True, "echo": payload}},
    )

    result = invoker.invoke(
        CapabilityInvocationRequest(
            task_id="paper-1",
            call_id="call-skill",
            agent_id="method_agent",
            capability_name="local_skill_check",
            input_summary="check evidence",
            payload={"evidence_ids": ["P01-L001"]},
            evidence_ids=["P01-L001"],
            approved=True,
        )
    )

    assert result.status is ToolTraceStatus.COMPLETED
    assert result.output == {"accepted": True, "echo": {"evidence_ids": ["P01-L001"]}}
    rows = _rows(trace_path)
    assert [row["status"] for row in rows] == ["queued", "started", "completed"]
    assert rows[-1]["evidence_ids"] == ["P01-L001"]
    assert rows[-1]["tool"] == "local_skill_check"


def test_invoker_fails_closed_when_output_schema_is_invalid(tmp_path) -> None:
    trace_path = tmp_path / "tool_trace.jsonl"
    invoker = CapabilityInvoker(
        registry=_skill_registry(),
        trace=ToolTraceRecorder(trace_path),
        handlers={"local_skill_check": lambda _payload: "not structured"},
    )

    result = invoker.invoke(
        CapabilityInvocationRequest(
            task_id="paper-1",
            call_id="call-invalid",
            agent_id="method_agent",
            capability_name="local_skill_check",
            input_summary="check evidence",
            payload={},
            approved=True,
        )
    )

    assert result.status is ToolTraceStatus.FAILED
    assert result.error_code == "schema_validation_failed"
    assert _rows(trace_path)[-1]["status"] == "failed"

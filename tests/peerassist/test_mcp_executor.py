from __future__ import annotations

import json
import sys
from pathlib import Path

from common.pipeline_context import write_json_file
from peerassist.capabilities import CapabilityRegistry
from peerassist.mcp_executor import build_mcp_handlers
from peerassist.mcp_registry import MCPServerCatalog, register_mcp_capabilities
from peerassist.tool_invocations import CapabilityInvocationRequest, CapabilityInvoker
from peerassist.tool_trace import ToolTraceRecorder
from schemas.peerassist import ToolTraceStatus


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _write_server(path: Path, *, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def _write_manifest(
    path: Path,
    server_script: Path,
    *,
    timeout: int = 5,
    env: dict[str, str] | None = None,
) -> None:
    server = {
        "name": "local_ref",
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(server_script)],
        "connect_timeout_seconds": 1,
        "tool_timeout_seconds": timeout,
        "tools": [
            {
                "name": "lookup_reference",
                "description": "Look up local reference metadata.",
                "permissions": ["read_artifact"],
                "input_summary": "reference title",
                "output_summary": "reference metadata JSON",
            }
        ],
    }
    if env is not None:
        server["env"] = env
    write_json_file(
        path,
        {
            "schema_version": "peerassist.mcp_servers.v1",
            "servers": [server],
        },
    )


def test_stdio_mcp_executor_invokes_registered_capability_and_records_trace(tmp_path: Path) -> None:
    server_script = tmp_path / "fake_mcp_server.py"
    _write_server(
        server_script,
        body="""
import json
import sys

request = json.loads(sys.stdin.readline())
assert request["method"] == "tools/call"
arguments = request["params"]["arguments"]
response = {
    "jsonrpc": "2.0",
    "id": request["id"],
    "result": {
        "matched_title": arguments["title"],
        "artifact_ids": ["ref-001"],
    },
}
print(json.dumps(response), flush=True)
""".lstrip(),
    )
    manifest_path = tmp_path / "mcp_servers.json"
    _write_manifest(manifest_path, server_script)
    catalog = MCPServerCatalog(manifest_path)
    registry = CapabilityRegistry()
    specs = register_mcp_capabilities(registry, catalog)
    trace_path = tmp_path / "tool_trace.jsonl"
    invoker = CapabilityInvoker(
        registry=registry,
        trace=ToolTraceRecorder(trace_path),
        handlers=build_mcp_handlers(catalog, specs),
    )

    result = invoker.invoke(
        CapabilityInvocationRequest(
            task_id="paper-1",
            call_id="call-mcp-ref",
            agent_id="citation_agent",
            capability_name="mcp_local_ref_lookup_reference",
            input_summary="lookup reference title",
            payload={"title": "PeerAssist Systems"},
            evidence_ids=["P01-L001"],
        )
    )

    assert result.status is ToolTraceStatus.COMPLETED
    assert result.output["matched_title"] == "PeerAssist Systems"
    assert result.artifact_ids == ["ref-001"]
    rows = _rows(trace_path)
    assert [row["status"] for row in rows] == ["queued", "started", "completed"]
    assert rows[-1]["source"] == "mcp"
    assert rows[-1]["tool"] == "mcp_local_ref_lookup_reference"
    assert rows[-1]["artifact_ids"] == ["ref-001"]


def test_stdio_mcp_executor_times_out_fail_closed(tmp_path: Path) -> None:
    server_script = tmp_path / "slow_mcp_server.py"
    _write_server(
        server_script,
        body="""
import time

time.sleep(1)
""".lstrip(),
    )
    manifest_path = tmp_path / "mcp_servers.json"
    _write_manifest(manifest_path, server_script, timeout=0)
    catalog = MCPServerCatalog(manifest_path)
    registry = CapabilityRegistry()
    specs = register_mcp_capabilities(registry, catalog)
    trace_path = tmp_path / "tool_trace.jsonl"
    invoker = CapabilityInvoker(
        registry=registry,
        trace=ToolTraceRecorder(trace_path),
        handlers=build_mcp_handlers(catalog, specs),
    )

    result = invoker.invoke(
        CapabilityInvocationRequest(
            task_id="paper-1",
            call_id="call-mcp-timeout",
            agent_id="citation_agent",
            capability_name="mcp_local_ref_lookup_reference",
            input_summary="lookup reference title",
            payload={"title": "PeerAssist Systems"},
            evidence_ids=["P01-L001"],
        )
    )

    assert result.status is ToolTraceStatus.FAILED
    assert result.error_code == "timeout"
    assert "timed out" in result.error_message
    rows = _rows(trace_path)
    assert [row["status"] for row in rows] == ["queued", "started", "failed"]


def test_stdio_mcp_executor_rejects_malformed_json_response(tmp_path: Path) -> None:
    server_script = tmp_path / "bad_mcp_server.py"
    _write_server(
        server_script,
        body="""
print("not-json", flush=True)
""".lstrip(),
    )
    manifest_path = tmp_path / "mcp_servers.json"
    _write_manifest(manifest_path, server_script)
    catalog = MCPServerCatalog(manifest_path)
    registry = CapabilityRegistry()
    specs = register_mcp_capabilities(registry, catalog)
    trace_path = tmp_path / "tool_trace.jsonl"
    invoker = CapabilityInvoker(
        registry=registry,
        trace=ToolTraceRecorder(trace_path),
        handlers=build_mcp_handlers(catalog, specs),
    )

    result = invoker.invoke(
        CapabilityInvocationRequest(
            task_id="paper-1",
            call_id="call-mcp-malformed",
            agent_id="citation_agent",
            capability_name="mcp_local_ref_lookup_reference",
            input_summary="lookup reference title",
            payload={"title": "PeerAssist Systems"},
            evidence_ids=["P01-L001"],
        )
    )

    assert result.status is ToolTraceStatus.FAILED
    assert result.error_code == "executor_failed"
    assert "invalid MCP JSON response" in result.error_message


def test_stdio_mcp_executor_passes_only_explicit_env_to_server(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PEERASSIST_SHOULD_NOT_LEAK", "secret-token")
    server_script = tmp_path / "env_mcp_server.py"
    _write_server(
        server_script,
        body="""
import json
import os
import sys

request = json.loads(sys.stdin.readline())
response = {
    "jsonrpc": "2.0",
    "id": request["id"],
    "result": {
        "allowed": os.environ.get("PEERASSIST_ALLOWED_KEY", ""),
        "leaked": os.environ.get("PEERASSIST_SHOULD_NOT_LEAK", ""),
    },
}
print(json.dumps(response), flush=True)
""".lstrip(),
    )
    manifest_path = tmp_path / "mcp_servers.json"
    _write_manifest(manifest_path, server_script, env={"PEERASSIST_ALLOWED_KEY": "visible"})
    catalog = MCPServerCatalog(manifest_path)
    registry = CapabilityRegistry()
    specs = register_mcp_capabilities(registry, catalog)
    invoker = CapabilityInvoker(
        registry=registry,
        trace=ToolTraceRecorder(tmp_path / "tool_trace.jsonl"),
        handlers=build_mcp_handlers(catalog, specs),
    )

    result = invoker.invoke(
        CapabilityInvocationRequest(
            task_id="paper-1",
            call_id="call-mcp-env",
            agent_id="citation_agent",
            capability_name="mcp_local_ref_lookup_reference",
            payload={},
        )
    )

    assert result.status is ToolTraceStatus.COMPLETED
    assert result.output["allowed"] == "visible"
    assert result.output["leaked"] == ""

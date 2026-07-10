"""Minimal audited stdio MCP executor for PeerAssist capabilities."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from collections.abc import Mapping
from typing import Any

from peerassist.capabilities import CapabilitySpec
from peerassist.mcp_registry import MCPServerCatalog, MCPServerSpec
from peerassist.tool_invocations import CapabilityHandler


class MCPStdioExecutor:
    """Execute manifest-declared stdio MCP tools through JSON-RPC line exchange."""

    def __init__(self, catalog: MCPServerCatalog) -> None:
        self._servers = {server.name: server for server in catalog.discover_servers()}

    def handler_for(self, capability: CapabilitySpec):
        server_name = str(capability.metadata.get("server_name", ""))
        tool_name = str(capability.metadata.get("tool_name", ""))
        transport = str(capability.metadata.get("transport", ""))
        if transport != "stdio":
            raise ValueError(f"MCP capability is not stdio transport: {capability.name}")
        server = self._servers.get(server_name)
        if server is None:
            raise ValueError(f"MCP server not found: {server_name}")

        def handler(payload: dict[str, Any]) -> Mapping[str, Any]:
            return self.invoke(server=server, tool_name=tool_name, payload=payload)

        return handler

    def invoke(self, *, server: MCPServerSpec, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if server.transport != "stdio":
            raise ValueError(f"MCP server is not stdio transport: {server.name}")
        if not server.command:
            raise ValueError(f"MCP stdio server has no command: {server.name}")
        request_id = str(uuid.uuid4())
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": dict(payload),
            },
        }
        timeout_seconds = max(0, int(server.tool_timeout_seconds))
        command = [server.command, *server.args]
        try:
            completed = subprocess.run(
                command,
                input=json.dumps(request) + "\n",
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                env=_filtered_env(getattr(server, "env", {})),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(
                f"MCP stdio tool {server.name}.{tool_name} timed out after "
                f"{timeout_seconds} seconds."
            ) from exc

        if completed.returncode != 0:
            stderr = _compact(completed.stderr) or "no stderr"
            raise RuntimeError(
                f"MCP stdio server {server.name} exited with code {completed.returncode}: {stderr}"
            )

        response_line = _first_response_line(completed.stdout)
        if not response_line:
            raise RuntimeError(f"MCP stdio server {server.name} returned no JSON response.")
        try:
            response = json.loads(response_line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid MCP JSON response from {server.name}: {response_line[:160]}") from exc

        if not isinstance(response, dict):
            raise RuntimeError(f"invalid MCP JSON response from {server.name}: expected object.")
        if response.get("id") not in (None, request_id):
            raise RuntimeError(f"invalid MCP JSON response from {server.name}: mismatched id.")
        if "error" in response:
            raise RuntimeError(f"MCP tool {server.name}.{tool_name} failed: {_compact(response['error'])}")
        result = response.get("result", response)
        if not isinstance(result, dict):
            raise RuntimeError(f"MCP tool {server.name}.{tool_name} returned non-object result.")
        return result


def build_mcp_handlers(
    catalog: MCPServerCatalog, capabilities: list[CapabilitySpec]
) -> dict[str, CapabilityHandler]:
    executor = MCPStdioExecutor(catalog)
    handlers: dict[str, CapabilityHandler] = {}
    for capability in capabilities:
        if capability.metadata.get("transport") == "stdio":
            handlers[capability.name] = executor.handler_for(capability)
    return handlers


def _first_response_line(stdout: str) -> str:
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _filtered_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    allowed_names = {"PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "SHELL", "TMPDIR"}
    env = {
        key: value
        for key, value in os.environ.items()
        if key in allowed_names or key.startswith("XDG_")
    }
    for key, value in dict(extra or {}).items():
        env[str(key)] = str(value)
    return env


def _compact(value: object) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=True)
    return " ".join(text.split())[:500]

"""MCP server manifest discovery for PeerAssist capability exposure."""

from __future__ import annotations

import json
import re
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from peerassist.capabilities import (
    CapabilityRegistry,
    CapabilitySource,
    CapabilitySpec,
    PermissionClass,
)


class MCPHealthStatus(StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


class MCPHealth(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: MCPHealthStatus = MCPHealthStatus.UNKNOWN
    checked_at: str = ""
    error_code: str = ""
    message: str = ""


class MCPToolSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    description: str = ""
    permissions: list[PermissionClass] = Field(default_factory=list)
    input_summary: str = ""
    output_summary: str = ""


class MCPServerSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    transport: str
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    connect_timeout_seconds: int = 60
    tool_timeout_seconds: int = 120
    concurrency_limit: int = 1
    health: MCPHealth = Field(default_factory=MCPHealth)
    tools: list[MCPToolSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_transport_shape(self) -> MCPServerSpec:
        transport = self.transport.strip().lower()
        healthy = self.health.model_copy(deep=True)
        if transport == "stdio":
            if not self.command or self.url:
                healthy.status = MCPHealthStatus.UNHEALTHY
                healthy.error_code = "invalid_stdio_config"
                healthy.message = "stdio MCP servers require command and must not set url"
        elif transport == "http":
            if not self.url or self.command:
                healthy.status = MCPHealthStatus.UNHEALTHY
                healthy.error_code = "invalid_http_config"
                healthy.message = "http MCP servers require url and must not set command"
        else:
            healthy.status = MCPHealthStatus.UNHEALTHY
            healthy.error_code = "unsupported_transport"
            healthy.message = f"unsupported MCP transport: {self.transport}"
        self.transport = transport
        self.health = healthy
        return self


class MCPServerCatalog:
    def __init__(self, manifest_path: Path) -> None:
        self.manifest_path = Path(manifest_path)

    def discover_servers(self) -> list[MCPServerSpec]:
        payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != "peerassist.mcp_servers.v1":
            raise ValueError(f"unsupported MCP manifest schema: {payload.get('schema_version')}")
        rows = payload.get("servers") if isinstance(payload.get("servers"), list) else []
        return [MCPServerSpec.model_validate(row) for row in rows if isinstance(row, dict)]

    def discover_capabilities(self) -> list[CapabilitySpec]:
        capabilities: list[CapabilitySpec] = []
        for server in self.discover_servers():
            if server.health.status is MCPHealthStatus.UNHEALTHY:
                continue
            for tool in server.tools:
                capabilities.append(
                    CapabilitySpec(
                        name=f"mcp_{_safe_name(server.name)}_{_safe_name(tool.name)}",
                        description=tool.description,
                        source=CapabilitySource.MCP,
                        permissions=list(tool.permissions),
                        input_summary=tool.input_summary,
                        output_summary=tool.output_summary,
                        metadata={
                            "server_name": server.name,
                            "tool_name": tool.name,
                            "transport": server.transport,
                            "health_status": server.health.status.value,
                            "connect_timeout_seconds": server.connect_timeout_seconds,
                            "tool_timeout_seconds": server.tool_timeout_seconds,
                            "concurrency_limit": server.concurrency_limit,
                        },
                    )
                )
        return capabilities


def register_mcp_capabilities(
    registry: CapabilityRegistry, catalog: MCPServerCatalog
) -> list[CapabilitySpec]:
    specs = catalog.discover_capabilities()
    for spec in specs:
        registry.register(spec)
    return specs


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z_]+", "_", value.strip())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized.lower() or "unnamed"

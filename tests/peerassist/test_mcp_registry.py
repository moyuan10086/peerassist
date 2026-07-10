from __future__ import annotations

from common.pipeline_context import write_json_file
from peerassist.capabilities import CapabilityRegistry, CapabilitySource, PermissionClass
from peerassist.mcp_registry import MCPHealthStatus, MCPServerCatalog, register_mcp_capabilities


def test_mcp_catalog_discovers_servers_tools_and_health_metadata(tmp_path) -> None:
    manifest_path = tmp_path / "mcp_servers.json"
    write_json_file(
        manifest_path,
        {
            "schema_version": "peerassist.mcp_servers.v1",
            "servers": [
                {
                    "name": "local_ref",
                    "transport": "stdio",
                    "command": "uvx",
                    "args": ["mcp-server-demo"],
                    "connect_timeout_seconds": 10,
                    "tool_timeout_seconds": 20,
                    "concurrency_limit": 2,
                    "health": {"status": "healthy", "checked_at": "2026-07-10T00:00:00Z"},
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
            ],
        },
    )

    catalog = MCPServerCatalog(manifest_path)
    servers = catalog.discover_servers()
    specs = catalog.discover_capabilities()

    assert servers[0].name == "local_ref"
    assert servers[0].transport == "stdio"
    assert servers[0].health.status is MCPHealthStatus.HEALTHY
    assert servers[0].connect_timeout_seconds == 10
    assert servers[0].tool_timeout_seconds == 20
    assert servers[0].concurrency_limit == 2
    assert specs[0].name == "mcp_local_ref_lookup_reference"
    assert specs[0].source is CapabilitySource.MCP
    assert specs[0].permissions == [PermissionClass.READ_ARTIFACT]
    assert specs[0].metadata["server_name"] == "local_ref"
    assert specs[0].metadata["transport"] == "stdio"
    assert specs[0].metadata["health_status"] == "healthy"


def test_mcp_capabilities_respect_external_permission_filtering(tmp_path) -> None:
    manifest_path = tmp_path / "mcp_servers.json"
    write_json_file(
        manifest_path,
        {
            "schema_version": "peerassist.mcp_servers.v1",
            "servers": [
                {
                    "name": "remote_citation",
                    "transport": "http",
                    "url": "https://mcp.example.test/mcp",
                    "health": {"status": "unknown"},
                    "tools": [
                        {
                            "name": "doi_lookup",
                            "description": "Lookup citation metadata over HTTP.",
                            "permissions": ["external_request"],
                        }
                    ],
                }
            ],
        },
    )
    registry = CapabilityRegistry()

    register_mcp_capabilities(registry, MCPServerCatalog(manifest_path))

    assert registry.expose(mode="fast") == []
    exposed = registry.expose(mode="fast", allow_external=True)
    assert len(exposed) == 1
    assert exposed[0].name == "mcp_remote_citation_doi_lookup"
    assert exposed[0].approval_required is True


def test_mcp_catalog_marks_invalid_server_unhealthy_without_registering_tools(tmp_path) -> None:
    manifest_path = tmp_path / "mcp_servers.json"
    write_json_file(
        manifest_path,
        {
            "schema_version": "peerassist.mcp_servers.v1",
            "servers": [
                {
                    "name": "bad_server",
                    "transport": "stdio",
                    "url": "https://should-not-have-url-with-stdio.test",
                    "health": {"status": "healthy"},
                    "tools": [{"name": "unsafe_tool", "description": "Should not register."}],
                }
            ],
        },
    )

    catalog = MCPServerCatalog(manifest_path)

    assert catalog.discover_servers()[0].health.status is MCPHealthStatus.UNHEALTHY
    assert catalog.discover_capabilities() == []

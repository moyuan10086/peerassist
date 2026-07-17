from __future__ import annotations

import re
from ipaddress import ip_address
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]

GUIDANCE_PATHS = (
    "AGENTS.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "docs/product/peerassist-platform-prd.md",
    "docs/api/conventions.md",
    "docs/adr/0001-progressive-modular-monolith.md",
    "docs/adr/0002-provider-ports.md",
    "docs/README.md",
)


def _read(path: str) -> str:
    return (REPOSITORY_ROOT / path).read_text(encoding="utf-8")


def _assert_markers(source: str, markers: tuple[str, ...]) -> None:
    for marker in markers:
        assert marker in source, f"missing repository guidance marker: {marker}"


def test_repository_guidance_files_exist() -> None:
    missing = [path for path in GUIDANCE_PATHS if not (REPOSITORY_ROOT / path).is_file()]

    assert not missing, f"missing repository guidance files: {missing}"


def test_agents_guidance_defines_delivery_and_safety_contracts() -> None:
    source = _read("AGENTS.md")

    _assert_markers(
        source,
        (
            "## Repository Map",
            "## Supported Commands",
            "## Verification",
            "## Architecture Boundaries",
            "## Security Boundaries",
            "## Feishu Synchronization",
            "## Definition Of Done",
            "python scripts/verify_repository.py all",
            "Protect dirty changes",
            "test-driven development",
            "Domain code must not depend on provider implementations",
            "Do not append date-stamped logs",
            "10 stable chapters",
        ),
    )
    for approval_boundary in (
        "external transmission",
        "overwriting human-authored content",
        "publishing a final report",
        "sharing",
    ):
        assert approval_boundary in source
    assert len(source.splitlines()) <= 150


def test_contributing_and_security_define_supported_boundaries() -> None:
    contributing = _read("CONTRIBUTING.md")
    security = _read("SECURITY.md")

    _assert_markers(contributing, ("## Development Setup", "## Pull Request Checks"))
    _assert_markers(
        security,
        (
            "## Supported Deployment Boundary",
            "## Confidential Manuscripts",
            "no built-in authentication",
            "loopback",
            "trusted reverse proxy",
            "Do not process confidential manuscripts in production",
        ),
    )


def test_product_and_architecture_docs_capture_the_approved_direction() -> None:
    prd = _read("docs/product/peerassist-platform-prd.md")
    modular_monolith = _read("docs/adr/0001-progressive-modular-monolith.md")
    provider_ports = _read("docs/adr/0002-provider-ports.md")

    _assert_markers(
        prd,
        (
            "## Product Goals",
            "## Users",
            "## Constraints",
            "## Milestones",
            "M0",
            "M1",
            "M2",
            "M3",
            "M4",
            "M5",
            "## Non-Goals",
            "PDF",
            "canvas relationship view",
        ),
    )
    _assert_markers(
        modular_monolith,
        ("Status: Accepted", "progressive modular monolith", "Next.js", "FastAPI"),
    )
    _assert_markers(
        provider_ports,
        ("Status: Accepted", "OIDC", "PostgreSQL", "S3", "Supabase", "optional"),
    )


def test_api_conventions_define_multitenant_and_compatibility_contracts() -> None:
    source = _read("docs/api/conventions.md")

    _assert_markers(
        source,
        (
            "/api/v1",
            "403",
            "404",
            "error envelope",
            "Idempotency-Key",
            "aggregate version",
            "event cursor",
            "SSE",
            "backward compatibility",
        ),
    )


def test_documentation_index_links_the_engineering_sources_of_truth() -> None:
    source = _read("docs/README.md")

    for path in GUIDANCE_PATHS[:-1]:
        relative = path.removeprefix("docs/") if path.startswith("docs/") else f"../{path}"
        assert relative in source
    assert "superpowers/specs/2026-07-17-peerassist-platform-canvas-agent-design.md" in source
    assert "superpowers/plans/2026-07-17-peerassist-m0-engineering-baseline.md" in source


def test_guidance_does_not_publish_private_paths_public_ips_or_secret_material() -> None:
    combined = "\n".join(_read(path) for path in GUIDANCE_PATHS)

    assert "/root/" not in combined
    assert "/home/" not in combined
    assert not re.search(r"(?:sk|ghp|github_pat)_[A-Za-z0-9_-]{16,}", combined)
    for candidate in re.findall(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])", combined):
        address = ip_address(candidate)
        assert not address.is_global, f"public IP address in repository guidance: {candidate}"

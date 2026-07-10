from __future__ import annotations

from peerassist.capabilities import (
    CapabilityRegistry,
    CapabilitySource,
    PermissionClass,
    default_capability_registry,
)


def test_builtin_capabilities_are_visible_in_fast_mode() -> None:
    registry = default_capability_registry()

    exposed = registry.expose(mode="fast")
    names = {cap.name for cap in exposed}

    assert "build_evidence_ledger" in names
    assert "deterministic_consistency_checks" in names
    assert "peerassist_local_agents" in names
    assert "mineru_parse_artifacts" in names


def test_external_upload_capabilities_are_hidden_without_permission() -> None:
    registry = default_capability_registry()

    exposed = registry.expose(
        mode="standard",
        allow_external=False,
        allow_manuscript_upload=False,
    )
    names = {cap.name for cap in exposed}

    assert "baidu_doc_parser" not in names
    assert "baidu_paddleocr_vl" not in names
    assert "baidu_unlimited_ocr" not in names


def test_external_upload_capabilities_require_approval_when_exposed() -> None:
    registry = default_capability_registry()

    exposed = registry.expose(
        mode="deep",
        allow_external=True,
        allow_manuscript_upload=True,
    )
    baidu = next(cap for cap in exposed if cap.name == "baidu_doc_parser")

    assert baidu.source is CapabilitySource.BUILTIN
    assert PermissionClass.MANUSCRIPT_UPLOAD in baidu.permissions
    assert baidu.approval_required is True


def test_capability_schema_exposes_summary_not_full_instructions() -> None:
    registry = default_capability_registry()
    exposed = registry.expose(mode="fast")
    schema = next(cap for cap in exposed if cap.name == "build_evidence_ledger").to_exposed_schema()

    assert schema["name"] == "build_evidence_ledger"
    assert "description" in schema
    assert "input_summary" in schema
    assert "full_instructions" not in schema

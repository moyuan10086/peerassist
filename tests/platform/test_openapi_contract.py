from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts" / "openapi" / "peerassist-v1.json"


def test_openapi_has_versioned_paths_operation_ids_and_no_provider_schemas() -> None:
    from services.api.app import create_openapi_app

    schema = create_openapi_app().openapi()

    assert schema["info"]["version"] == "1.0.0"
    assert set(schema["paths"]) == {
        "/api/v1/auth/callback",
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/auth/register",
        "/api/v1/auth/session",
        "/api/v1/health",
        "/api/v1/me",
        "/api/v1/model-settings",
        "/api/v1/model-settings/discover",
        "/api/v1/organizations",
        "/api/v1/organizations/{organization_id}/audit-events",
        "/api/v1/organizations/{organization_id}/members",
        "/api/v1/organizations/{organization_id}/members/{membership_id}",
        "/api/v1/organizations/{organization_id}/projects",
        "/api/v1/projects/{project_id}",
        "/api/v1/projects/{project_id}/members",
        "/api/v1/projects/{project_id}/members/{membership_id}",
        "/api/v1/projects/{project_id}/papers",
        "/api/v1/projects/{project_id}/papers/{paper_id}/source",
        "/api/v1/projects/{project_id}/review-jobs",
        "/api/v1/projects/{project_id}/review-jobs/{job_id}",
        "/api/v1/projects/{project_id}/review-jobs/{job_id}/artifacts",
        "/api/v1/projects/{project_id}/review-jobs/{job_id}/artifacts/{artifact_id}",
        "/api/v1/projects/{project_id}/review-jobs/{job_id}/cancel",
        "/api/v1/projects/{project_id}/review-jobs/{job_id}/decisions",
        "/api/v1/projects/{project_id}/review-jobs/{job_id}/draft",
        "/api/v1/projects/{project_id}/review-jobs/{job_id}/event-stream",
        "/api/v1/projects/{project_id}/review-jobs/{job_id}/events",
        "/api/v1/projects/{project_id}/review-jobs/{job_id}/finalize",
        "/api/v1/projects/{project_id}/review-jobs/{job_id}/retry",
        "/api/v1/projects/{project_id}/compat/{registration_id}",
        "/api/v1/projects/{project_id}/compat/{registration_id}/migrate",
        "/api/v1/ready",
        "/api/v1/workspace",
        "/api/v1/workspace/papers",
        "/api/v1/workspace/project",
        "/api/v1/workspace/reviews",
        "/api/v1/workspace/reviews/{job_id}",
        "/api/v1/workspace/reviews/{job_id}/cancel",
        "/api/v1/workspace/reviews/{job_id}/consents/{service}",
        "/api/v1/workspace/reviews/{job_id}/document",
        "/api/v1/workspace/reviews/{job_id}/evidence",
        "/api/v1/workspace/reviews/{job_id}/exports",
        "/api/v1/workspace/reviews/{job_id}/exports/{report_version_id}",
        "/api/v1/workspace/reviews/{job_id}/findings/{finding_lineage_id}/decision",
        "/api/v1/workspace/reviews/{job_id}/retry",
    }
    operation_ids = {
        operation["operationId"]
        for methods in schema["paths"].values()
        for operation in methods.values()
    }
    assert len(operation_ids) == 59
    assert all(operation_id.startswith("v1_") for operation_id in operation_ids)
    serialized = json.dumps(schema).casefold()
    assert "postgres" not in serialized
    assert "keycloak" not in serialized
    assert "minio" not in serialized
    assert "oidc" not in serialized
    assert "s3" not in serialized


def test_committed_openapi_is_canonical_and_export_check_is_clean() -> None:
    raw = CONTRACT.read_bytes()
    parsed = json.loads(raw)
    canonical = (json.dumps(parsed, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()

    assert raw == canonical
    completed = subprocess.run(
        [sys.executable, "scripts/export_openapi.py", "--check"],
        cwd=ROOT,
        env={"PYTHONPATH": "src:."},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_atomic_export_replaces_the_existing_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.export_openapi as exporter

    contract = tmp_path / "peerassist-v1.json"
    contract.write_bytes(b"old-contract\n")
    monkeypatch.setattr(exporter, "CONTRACT", contract)

    exporter.export_contract(b"new-contract\n")

    assert contract.read_bytes() == b"new-contract\n"
    assert list(tmp_path.iterdir()) == [contract]


def test_failed_atomic_replace_preserves_existing_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.export_openapi as exporter

    contract = tmp_path / "peerassist-v1.json"
    contract.write_bytes(b"existing-contract\n")
    monkeypatch.setattr(exporter, "CONTRACT", contract)

    def fail_replace(source: Path, destination: Path) -> None:
        del source, destination
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError):
        exporter.export_contract(b"new-contract\n")

    assert contract.read_bytes() == b"existing-contract\n"
    assert list(tmp_path.iterdir()) == [contract]


def test_openapi_check_never_writes_the_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.export_openapi as exporter

    monkeypatch.setattr(exporter, "canonical_openapi", lambda: exporter.CONTRACT.read_bytes())
    monkeypatch.setattr(
        exporter,
        "export_contract",
        lambda content: pytest.fail(f"check attempted write: {content!r}"),
    )
    monkeypatch.setattr(sys, "argv", ["export_openapi.py", "--check"])

    assert exporter.main() == 0

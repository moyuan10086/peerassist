from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

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
        "/api/v1/auth/session",
        "/api/v1/health",
        "/api/v1/me",
        "/api/v1/ready",
    }
    operation_ids = {
        operation["operationId"]
        for methods in schema["paths"].values()
        for operation in methods.values()
    }
    assert len(operation_ids) == 7
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

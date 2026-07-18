from __future__ import annotations

from pathlib import Path

import yaml


def test_m1_compose_has_authoritative_api_worker_and_private_providers() -> None:
    path = Path("infrastructure/compose/compose.m1.yml")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    services = payload["services"]

    assert {
        "api", "worker", "migrate", "platform-bootstrap", "postgres",
        "keycloak", "minio", "minio-bootstrap",
    } <= set(services)
    assert services["api"]["ports"] == ["127.0.0.1:${PEERASSIST_M1_PORT:-8080}:8080"]
    assert all("ports" not in value for name, value in services.items() if name != "api")
    assert services["worker"]["read_only"] is True
    assert services["worker"]["volumes"][0].endswith(":/var/lib/peerassist/scratch")
    assert services["migrate"]["restart"] == "no"
    assert services["platform-bootstrap"]["restart"] == "no"
    assert services["api"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert services["worker"]["depends_on"]["platform-bootstrap"]["condition"] == "service_completed_successfully"
    serialized = path.read_text(encoding="utf-8").casefold()
    assert "minioadmin" not in serialized
    assert "change-me" not in serialized
    assert "postgres:postgres" not in serialized

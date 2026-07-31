from __future__ import annotations

from pathlib import Path

import yaml


def test_m1_compose_has_authoritative_api_worker_and_private_providers() -> None:
    path = Path("infrastructure/compose/compose.m1.yml")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    services = payload["services"]

    assert {
        "api", "worker", "migrate", "platform-bootstrap", "postgres",
        "keycloak", "minio-init", "minio", "minio-bootstrap", "worker-scratch-init",
        "model-settings-init",
    } <= set(services)
    assert services["api"]["ports"] == [
        "${PEERASSIST_M1_PORT:?}:${PEERASSIST_M1_PORT:?}"
    ]
    assert services["api"]["tmpfs"] == [
        "/tmp:uid=10001,gid=10001,mode=0700,size=256m"
    ]
    assert any(
        value.endswith(":/var/lib/peerassist/legacy:ro")
        for value in services["api"]["volumes"]
    )
    assert all("ports" not in value for name, value in services.items() if name != "api")
    assert services["worker"]["read_only"] is True
    assert services["worker"]["volumes"][0].endswith(":/var/lib/peerassist/scratch")
    assert services["worker-scratch-init"]["restart"] == "no"
    assert services["worker-scratch-init"]["command"] == [
        "chown -R 10002:10002 /var/lib/peerassist/scratch"
    ]
    assert services["worker"]["depends_on"]["worker-scratch-init"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["model-settings-init"]["command"] == [
        "chown -R 10001:10001 /var/lib/peerassist/model-settings"
    ]
    assert services["api"]["environment"]["PEERASSIST_MODEL_SETTINGS_PATH"] == (
        "/var/lib/peerassist/model-settings/model-settings.json"
    )
    assert services["worker"]["environment"]["PEERASSIST_MODEL_SETTINGS_PATH"] == (
        "/var/lib/peerassist/model-settings/model-settings.json"
    )
    assert "model-settings-data:/var/lib/peerassist/model-settings" in services["api"]["volumes"]
    assert "model-settings-data:/var/lib/peerassist/model-settings:ro" in services["worker"]["volumes"]
    assert services["api"]["depends_on"]["model-settings-init"]["condition"] == (
        "service_completed_successfully"
    )
    assert any(value.endswith(":/opt/keycloak/data") for value in services["keycloak"]["volumes"])
    assert services["migrate"]["restart"] == "no"
    assert services["migrate"]["environment"]["PEERASSIST_TEST_DATABASE_URL"] == (
        "postgresql+psycopg://${PEERASSIST_DB_USER:?}:${PEERASSIST_DB_PASSWORD:?}"
        "@postgres:5432/${PEERASSIST_DB_NAME:?}"
    )
    assert services["minio-init"]["restart"] == "no"
    assert services["minio-init"]["command"] == ["chown -R 1000:1000 /data"]
    assert services["minio"]["depends_on"]["minio-init"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["keycloak"]["build"]["dockerfile"] == (
        "infrastructure/compose/Dockerfile.keycloak"
    )
    assert services["minio-bootstrap"]["environment"]["HOME"] == "/home/mc"
    assert services["minio-bootstrap"]["environment"]["MC_CONFIG_DIR"] == (
        "/home/mc/.mc"
    )
    assert services["platform-bootstrap"]["restart"] == "no"
    assert services["platform-bootstrap"]["command"] == [
        "python", "-m", "services.bootstrap.main",
    ]
    assert services["api"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert services["platform-bootstrap"]["depends_on"]["api"]["condition"] == "service_healthy"
    assert services["worker"]["depends_on"]["platform-bootstrap"]["condition"] == "service_completed_successfully"
    assert "PEERASSIST_WORKER_SCOPE_FILE" not in services["worker"]["environment"]
    assert all("/run/peerassist" not in value for value in services["worker"]["volumes"])
    assert services["worker"]["environment"]["DATA_DIR"] == "/tmp/peerassist-data"
    assert services["api"]["environment"]["PEERASSIST_PLATFORM_IDENTITY_GATEWAY_URL"] == (
        "http://keycloak:8080/identity"
    )
    assert services["keycloak"]["environment"]["M1_TEST_IDENTITY_PATH_PREFIX"] == "/identity"
    serialized = path.read_text(encoding="utf-8").casefold()
    assert "minioadmin" not in serialized
    assert "change-me" not in serialized
    assert "postgres:postgres" not in serialized

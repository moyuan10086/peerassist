from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = ROOT / "infrastructure/compose/compose.yml"
DOCKERFILE_PATH = ROOT / "infrastructure/compose/Dockerfile"

REVIEW_COMMAND = (
    "python -m peerassist.review_job_api --data-dir /var/lib/peerassist "
    "--host 0.0.0.0 --port 8767"
)
WORKSPACE_COMMAND = (
    "python -m peerassist.confirmation_server --run-dir /app/demos/Text/bert "
    "--paper-id bert --host 0.0.0.0 --port 8766"
)


def _compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def test_compose_exposes_only_workspace_on_loopback() -> None:
    services = _compose()["services"]

    assert set(services) == {"review-api", "workspace"}
    assert "ports" not in services["review-api"]
    assert services["workspace"]["ports"] == [
        "127.0.0.1:${PEERASSIST_WORKSPACE_BIND_PORT:-8766}:8766"
    ]
    assert services["workspace"]["environment"]["PEERASSIST_REVIEW_API_URL"] == (
        "http://review-api:8767"
    )
    assert services["workspace"]["depends_on"]["review-api"]["condition"] == (
        "service_healthy"
    )


def test_compose_services_share_a_hardened_image_contract() -> None:
    services = _compose()["services"]

    for service in services.values():
        assert service["build"] == {
            "context": "../..",
            "dockerfile": "infrastructure/compose/Dockerfile",
        }
        assert service["restart"] == "unless-stopped"
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["healthcheck"]["test"][:2] == ["CMD", "python"]

    assert services["review-api"]["command"] == REVIEW_COMMAND
    assert services["workspace"]["command"] == WORKSPACE_COMMAND
    assert services["review-api"]["volumes"] == ["peerassist-data:/var/lib/peerassist"]
    assert _compose()["volumes"] == {"peerassist-data": {}}


def test_dockerfile_runs_as_fixed_non_root_user_without_secret_defaults() -> None:
    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")

    assert dockerfile.startswith("FROM python:3.12-slim")
    assert "groupadd --gid 10001 peerassist" in dockerfile
    assert "useradd --uid 10001" in dockerfile
    assert "PYTHONPATH=/app/src" in dockerfile
    assert "COPY --chown=peerassist:peerassist . /app" in dockerfile
    assert "USER peerassist" in dockerfile
    assert "%PDF-" in dockerfile
    assert "git lfs pull" in dockerfile
    assert "https://pypi.org/simple" in dockerfile
    assert "OPENAI_API_KEY" not in dockerfile
    assert "ARG " not in dockerfile

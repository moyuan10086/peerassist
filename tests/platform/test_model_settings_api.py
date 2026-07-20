from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from common.config import PlatformSettings
from peerassist.platform.models import Actor, ActorKind
from services.api.app import create_app
from services.api.composition import PlatformDependencies
from services.api.dependencies import require_request_actor


def _settings() -> PlatformSettings:
    return PlatformSettings(
        environment="test",
        database_url="postgresql+psycopg://test:test@db/peerassist",
        oidc_issuer="http://identity.test/realms/peerassist",
        oidc_audience="peerassist-api",
        s3_endpoint="http://objects.test",
        s3_bucket="peerassist-test",
        public_base_url="http://api.test",
        allowed_origins=["http://api.test"],
        internal_legacy_audience="peerassist-legacy",
    )


def test_model_settings_routes_require_login_instead_of_falling_through_to_404() -> None:
    app = create_app(_settings(), PlatformDependencies.for_test())

    with TestClient(app) as client:
        response = client.get("/api/model-settings")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_model_settings_can_discover_save_and_reload_without_exposing_secret(
    tmp_path, monkeypatch
) -> None:
    settings_path = tmp_path / "model-settings.json"
    monkeypatch.setenv("PEERASSIST_MODEL_SETTINGS_PATH", str(settings_path))
    monkeypatch.setattr(
        "services.api.routes.model_settings.discover_models",
        lambda value: ["gpt-5.5", "gpt-5.6-sol"],
    )
    app = create_app(_settings(), PlatformDependencies.for_test())
    app.dependency_overrides[require_request_actor] = lambda: Actor(uuid4(), ActorKind.USER)

    with TestClient(app) as client:
        empty = client.get("/api/model-settings")
        discovered = client.post(
            "/api/model-settings/discover",
            json={
                "provider": "openai-compatible",
                "base_url": "https://provider.example/v1",
                "model": "",
                "api_key": "sk-synthetic-private",
            },
        )
        saved = client.post(
            "/api/model-settings",
            json={
                "provider": "openai-compatible",
                "base_url": "https://provider.example/v1",
                "model": "gpt-5.6-sol",
                "api_key": "sk-synthetic-private",
            },
        )
        reloaded = client.get("/api/model-settings")

    assert empty.status_code == 200
    assert empty.json()["settings"]["api_key_configured"] is False
    assert discovered.status_code == 200
    assert discovered.json() == {"models": ["gpt-5.5", "gpt-5.6-sol"]}
    assert saved.status_code == 200
    assert saved.json()["settings"]["model"] == "gpt-5.6-sol"
    assert reloaded.status_code == 200
    assert reloaded.json()["settings"]["api_key_configured"] is True
    assert "sk-synthetic-private" not in str(saved.json())
    assert "sk-synthetic-private" not in str(reloaded.json())


def test_model_settings_validation_returns_a_readable_safe_message(monkeypatch) -> None:
    app = create_app(_settings(), PlatformDependencies.for_test())
    app.dependency_overrides[require_request_actor] = lambda: Actor(uuid4(), ActorKind.USER)

    with TestClient(app) as client:
        response = client.post(
            "/api/model-settings/discover",
            json={"provider": "openai-compatible", "base_url": "not-a-url"},
        )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "Base URL 必须是完整的 HTTP(S) 地址。"

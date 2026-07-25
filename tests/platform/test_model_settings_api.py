from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from services.api.app import create_app
from services.api.composition import PlatformDependencies
from services.api.dependencies import require_request_actor

from common.config import PlatformSettings
from peerassist.platform.models import (
    Actor,
    ActorKind,
    Organization,
    OrganizationMembership,
    Role,
    TenantScope,
    User,
)


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
    dependencies = PlatformDependencies.for_test()
    admin = Actor(uuid4(), ActorKind.USER)
    now = datetime(2026, 7, 24, 1, 0, tzinfo=UTC)
    organization = Organization(uuid4(), "model-org", "Model Org", "active", 1, now, now)
    with dependencies.uow_factory(admin) as uow:
        uow.users.add(User(admin.actor_id, "active", "Admin", now, now))
        uow.organizations.add(TenantScope(organization.id), organization)
        uow.organizations.save_membership(
            TenantScope(organization.id),
            OrganizationMembership(
                uuid4(), organization.id, admin.actor_id, Role.ORGANIZATION_ADMIN,
                "active", 1, now, now,
            ),
            None,
        )
        uow.commit()
    app = create_app(_settings(), dependencies)
    app.dependency_overrides[require_request_actor] = lambda: admin

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
    assert saved.json()["settings"]["revision"] == 1
    assert saved.json()["settings"]["enabled"] is True
    assert saved.json()["settings"]["policy_version"]
    assert saved.json()["settings"]["configuration_id"]
    assert reloaded.status_code == 200
    assert reloaded.json()["settings"]["api_key_configured"] is True
    assert "sk-synthetic-private" not in str(saved.json())
    assert "sk-synthetic-private" not in str(reloaded.json())


def test_model_settings_validation_returns_a_readable_safe_message(monkeypatch) -> None:
    dependencies = PlatformDependencies.for_test()
    admin = Actor(uuid4(), ActorKind.USER)
    now = datetime(2026, 7, 24, 1, 0, tzinfo=UTC)
    organization = Organization(uuid4(), "validation-org", "Validation Org", "active", 1, now, now)
    with dependencies.uow_factory(admin) as uow:
        uow.users.add(User(admin.actor_id, "active", "Admin", now, now))
        uow.organizations.add(TenantScope(organization.id), organization)
        uow.organizations.save_membership(
            TenantScope(organization.id),
            OrganizationMembership(
                uuid4(), organization.id, admin.actor_id, Role.ORGANIZATION_ADMIN,
                "active", 1, now, now,
            ),
            None,
        )
        uow.commit()
    app = create_app(_settings(), dependencies)
    app.dependency_overrides[require_request_actor] = lambda: admin

    with TestClient(app) as client:
        response = client.post(
            "/api/model-settings/discover",
            json={"provider": "openai-compatible", "base_url": "not-a-url"},
        )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "Base URL 必须是完整的 HTTP(S) 地址。"


def test_model_settings_write_and_discovery_are_hidden_from_non_admins(tmp_path, monkeypatch) -> None:
    from peerassist.model_settings import ModelSettingsInput, save_model_settings

    settings_path = tmp_path / "settings.json"
    monkeypatch.setenv("PEERASSIST_MODEL_SETTINGS_PATH", str(settings_path))
    save_model_settings(
        ModelSettingsInput(
            "openai-compatible",
            "https://provider.example/v1",
            "teacher-review-model",
            api_key="sk-synthetic-private",
        ),
        path=settings_path,
    )
    dependencies = PlatformDependencies.for_test()
    teacher = Actor(uuid4(), ActorKind.USER)
    now = datetime(2026, 7, 24, 1, 0, tzinfo=UTC)
    with dependencies.uow_factory(teacher) as uow:
        uow.users.add(User(teacher.actor_id, "active", "Teacher", now, now))
        uow.commit()
    app = create_app(_settings(), dependencies)
    app.dependency_overrides[require_request_actor] = lambda: teacher
    with TestClient(app) as client:
        get_response = client.get("/api/model-settings")
        response = client.post(
            "/api/model-settings",
            json={"provider": "openai-compatible", "base_url": "https://provider.example/v1", "model": "m"},
        )
    assert get_response.status_code == 200
    teacher_settings = get_response.json()["settings"]
    assert teacher_settings == {
        "provider": "openai-compatible",
        "model": "teacher-review-model",
        "api_key_configured": True,
        "enabled": True,
    }
    assert "base_url" not in teacher_settings
    assert "revision" not in teacher_settings
    assert "policy_version" not in teacher_settings
    assert "configuration_id" not in teacher_settings
    assert "api_key_hint" not in teacher_settings
    assert response.status_code == 404


def test_model_settings_revision_uses_compare_and_swap(tmp_path) -> None:
    from peerassist.model_settings import ModelSettingsError, ModelSettingsInput, save_model_settings

    path = tmp_path / "settings.json"
    first = save_model_settings(
        ModelSettingsInput("openai-compatible", "https://provider.example/v1", "model-a"),
        path=path,
    )
    assert first.revision == 1
    try:
        save_model_settings(
            ModelSettingsInput("openai-compatible", "https://provider.example/v1", "model-b", expected_revision=0),
            path=path,
        )
    except ModelSettingsError as exc:
        assert "revision" in str(exc).lower()
    else:
        raise AssertionError("stale model settings write must fail")


def test_model_settings_concurrent_compare_and_swap_has_one_winner(tmp_path) -> None:
    from peerassist.model_settings import ModelSettingsInput, save_model_settings

    path = tmp_path / "settings.json"
    save_model_settings(
        ModelSettingsInput("openai-compatible", "https://provider.example/v1", "model-a"),
        path=path,
    )

    def update(model: str):
        try:
            return save_model_settings(
                ModelSettingsInput(
                    "openai-compatible", "https://provider.example/v1", model,
                    expected_revision=1,
                ),
                path=path,
            )
        except Exception as exc:  # one stable conflict is the expected loser
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(update, ("model-b", "model-c")))
    assert sum(hasattr(result, "revision") for result in results) == 1
    assert sum(type(result).__name__ == "ModelSettingsConflict" for result in results) == 1

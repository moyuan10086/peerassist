from __future__ import annotations

from pathlib import Path

import pytest

from common.config import PlatformSettings
from peerassist.platform.adapters.memory import (
    FakeIdentityProvider,
    MemoryObjectStore,
    MemoryUnitOfWorkFactory,
)


def test_test_and_development_composition_select_memory_providers_explicitly() -> None:
    from services.api.composition import build_dependencies

    for environment in ("test", "development"):
        dependencies = build_dependencies(_settings(environment=environment))

        assert isinstance(dependencies.uow_factory, MemoryUnitOfWorkFactory)
        assert isinstance(dependencies.identity_provider, FakeIdentityProvider)
        assert isinstance(dependencies.object_store, MemoryObjectStore)
        assert {check.name for check in dependencies.readiness_checks} == {
            "database",
            "identity",
            "object_store",
        }


def test_production_composition_selects_postgres_without_memory_fallback() -> None:
    from services.api.composition import build_dependencies

    from peerassist.platform.adapters.oidc import OidcIdentityProvider
    from peerassist.platform.adapters.postgres import PostgresUnitOfWorkFactory

    settings = _settings(
        environment="production",
        database_url="postgresql+psycopg://peerassist:private-db-secret@db/peerassist",
        oidc_issuer="https://identity.example.test/realms/peerassist",
        s3_endpoint="https://objects.example.test",
        s3_access_key_id="private-access-key",
        s3_secret_access_key="private-object-secret",
        public_base_url="https://peerassist.example.test",
        allowed_origins=["https://peerassist.example.test"],
        session_key_ring=[
            "key-2026-07=9f4c7b0d5e3a1862c8f1d4a7b0e3956c2f8a1d4e7b0c3965a2f8d1e4b7c09365"
        ],
        scratch_root=Path("/var/lib/peerassist/scratch"),
    )

    dependencies = build_dependencies(settings)

    assert isinstance(dependencies.uow_factory, PostgresUnitOfWorkFactory)
    assert not isinstance(dependencies.uow_factory, MemoryUnitOfWorkFactory)
    assert not isinstance(dependencies.identity_provider, FakeIdentityProvider)
    assert isinstance(dependencies.identity_provider, OidcIdentityProvider)
    assert not isinstance(dependencies.object_store, MemoryObjectStore)
    assert {check.name for check in dependencies.readiness_checks} == {
        "database",
        "identity",
        "object_store",
    }


def test_production_composition_fails_closed_when_identity_provider_cannot_be_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.api.composition import CompositionError, build_dependencies

    import peerassist.platform.adapters.oidc as oidc

    settings = _settings(
        environment="production",
        database_url="postgresql+psycopg://peerassist:private-db-secret@db/peerassist",
        oidc_issuer="https://identity.example.test/realms/peerassist",
        s3_endpoint="https://objects.example.test",
        s3_access_key_id="private-access-key",
        s3_secret_access_key="private-object-secret",
        public_base_url="https://peerassist.example.test",
        allowed_origins=["https://peerassist.example.test"],
        session_key_ring=[
            "key-2026-07=9f4c7b0d5e3a1862c8f1d4a7b0e3956c2f8a1d4e7b0c3965a2f8d1e4b7c09365"
        ],
        scratch_root=Path("/var/lib/peerassist/scratch"),
    )
    monkeypatch.setattr(
        oidc,
        "OidcIdentityProvider",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("private-token")),
    )

    with pytest.raises(CompositionError) as captured:
        build_dependencies(settings)

    assert str(captured.value) == "OIDC identity provider is unavailable."
    assert "private-token" not in str(captured.value)


def test_production_composition_fails_closed_without_database_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.api.composition import CompositionError, build_dependencies

    import peerassist.platform.adapters.postgres as postgres

    settings = _settings(
        environment="production",
        database_url="postgresql+psycopg://peerassist:private-db-secret@db/peerassist",
        oidc_issuer="https://identity.example.test/realms/peerassist",
        s3_endpoint="https://objects.example.test",
        s3_access_key_id="private-access-key",
        s3_secret_access_key="private-object-secret",
        public_base_url="https://peerassist.example.test",
        allowed_origins=["https://peerassist.example.test"],
        session_key_ring=[
            "key-2026-07=9f4c7b0d5e3a1862c8f1d4a7b0e3956c2f8a1d4e7b0c3965a2f8d1e4b7c09365"
        ],
        scratch_root=Path("/var/lib/peerassist/scratch"),
    )
    monkeypatch.setattr(
        postgres,
        "create_engine",
        lambda *args, **kwargs: (_ for _ in ()).throw(ModuleNotFoundError("private-db-secret")),
    )

    with pytest.raises(CompositionError) as captured:
        build_dependencies(settings)

    assert str(captured.value) == "PostgreSQL provider is unavailable."
    assert "private" not in str(captured.value)


def test_configured_application_loads_settings_only_when_factory_is_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import services.api.app as app_module

    settings = _settings()
    captured: list[PlatformSettings] = []
    monkeypatch.setattr(app_module, "PlatformSettings", lambda: settings)
    monkeypatch.setattr(
        app_module,
        "build_dependencies",
        lambda loaded: captured.append(loaded) or app_module.PlatformDependencies.for_test(),
    )

    app = app_module.create_configured_app()

    assert captured == [settings]
    assert app.state.settings is settings


def _settings(**overrides: object) -> PlatformSettings:
    values: dict[str, object] = {
        "environment": "test",
        "database_url": "postgresql+psycopg://test:test@db/peerassist",
        "oidc_issuer": "http://identity.test/realms/peerassist",
        "oidc_audience": "peerassist-api",
        "s3_endpoint": "http://objects.test",
        "s3_bucket": "peerassist-test",
        "public_base_url": "http://api.test",
        "allowed_origins": ["http://api.test"],
        "trusted_proxy_cidrs": [],
        "internal_legacy_audience": "peerassist-legacy",
    }
    values.update(overrides)
    return PlatformSettings(**values)

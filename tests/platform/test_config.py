from pathlib import Path

import pytest
from pydantic import ValidationError

from common.config import PlatformSettings, Settings


def valid_production_settings(**overrides: object) -> PlatformSettings:
    values: dict[str, object] = {
        "environment": "production",
        "database_url": "postgresql+psycopg://peerassist:generated-secret@db/peerassist",
        "oidc_issuer": "https://identity.example.test/realms/peerassist",
        "oidc_audience": "peerassist-api",
        "oidc_algorithms": ["RS256"],
        "s3_endpoint": "https://objects.example.test",
        "s3_bucket": "peerassist",
        "s3_path_style": False,
        "s3_access_key_id": "generated-access-key",
        "s3_secret_access_key": "generated-secret-key",
        "public_base_url": "https://peerassist.example.test",
        "allowed_origins": ["https://peerassist.example.test"],
        "trusted_proxy_cidrs": ["10.0.0.0/8"],
        "session_key_ring": ["key-2026-07=generated-session-secret"],
        "scratch_root": Path("/var/lib/peerassist/scratch"),
        "scratch_ttl_seconds": 3600,
        "internal_legacy_audience": "peerassist-legacy",
        "legacy_bind_host": "127.0.0.1",
    }
    values.update(overrides)
    return PlatformSettings(**values)


def test_platform_settings_are_separate_from_legacy_settings() -> None:
    assert not issubclass(PlatformSettings, Settings)
    settings = valid_production_settings()

    assert settings.environment == "production"
    assert settings.s3_path_style is False
    assert settings.scratch_ttl_seconds == 3600


@pytest.mark.parametrize(
    ("override", "value"),
    [
        ("oidc_issuer", "http://identity.example.test/realms/peerassist"),
        ("public_base_url", "http://peerassist.example.test"),
        ("allowed_origins", ["*"]),
        ("allowed_origins", ["https://peerassist.example.test", "*"]),
        ("session_key_ring", []),
        ("legacy_bind_host", "0.0.0.0"),
        ("legacy_bind_host", "::"),
        ("legacy_bind_host", "192.0.2.10"),
        ("database_url", "postgresql+psycopg://postgres:postgres@db/peerassist"),
        ("s3_access_key_id", "minioadmin"),
        ("s3_secret_access_key", "minioadmin"),
    ],
)
def test_production_platform_settings_reject_unsafe_defaults(override: str, value: object) -> None:
    with pytest.raises(ValidationError):
        valid_production_settings(**{override: value})


def test_platform_settings_validate_urls_cidrs_and_required_values() -> None:
    with pytest.raises(ValidationError):
        valid_production_settings(database_url="sqlite:///peerassist.db")
    with pytest.raises(ValidationError):
        valid_production_settings(trusted_proxy_cidrs=["not-a-network"])
    with pytest.raises(ValidationError):
        valid_production_settings(oidc_algorithms=[])
    with pytest.raises(ValidationError):
        valid_production_settings(scratch_ttl_seconds=0)


def test_platform_settings_redact_secrets() -> None:
    settings = valid_production_settings()
    rendered = f"{settings!r}\n{settings.model_dump_json()}"

    assert "generated-secret" not in rendered
    assert "generated-access-key" not in rendered
    assert "generated-session-secret" not in rendered
    assert "**********" in rendered

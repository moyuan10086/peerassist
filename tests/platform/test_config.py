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
        "session_key_ring": [
            "key-2026-07=9f4c7b0d5e3a1862c8f1d4a7b0e3956c2f8a1d4e7b0c3965a2f8d1e4b7c09365"
        ],
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
        ("database_url", "postgresql+psycopg://peerassist:change-me@db/peerassist"),
        ("s3_access_key_id", "minioadmin"),
        ("s3_secret_access_key", "minioadmin"),
        ("s3_access_key_id", "change-me"),
        ("s3_secret_access_key", "change-me"),
        ("session_key_ring", ["local-key-id=change-me"]),
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


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://peerassist:private-password@db/peerassist",
        "postgresql+psycopg:///peerassist",
        "postgresql+psycopg://peerassist:private-password@/peerassist",
        "postgresql+psycopg://peerassist:private-password@db",
        "postgresql+psycopg://peerassist:private-password@db/",
    ],
)
def test_platform_database_url_requires_psycopg_host_and_database(database_url: str) -> None:
    with pytest.raises(ValidationError) as captured:
        valid_production_settings(database_url=database_url)

    rendered = f"{captured.value}\n{captured.value.errors()!r}\n{captured.value.json()}"
    assert "private-password" not in rendered


@pytest.mark.parametrize(
    "session_key_ring",
    [
        [""],
        ["kid="],
        ["=9f4c7b0d5e3a1862c8f1d4a7b0e3956c2f8a1d4e7b0c3965a2f8d1e4b7c09365"],
        ["kid=changeme"],
        ["kid=change-me"],
        ["kid=abcdef"],
        ["kid=0000000000000000000000000000000000000000000000000000000000000000"],
        ["kid=not+base64/material"],
        [
            "duplicate=9f4c7b0d5e3a1862c8f1d4a7b0e3956c2f8a1d4e7b0c3965a2f8d1e4b7c09365",
            "duplicate=8e3b6a9c4d2f0751b7e0c3a6d9f2845b1e7c0a3d6f9b8254e1c7a0d3b6f98254",
        ],
    ],
)
def test_session_key_ring_requires_unique_ids_and_strong_key_material(
    session_key_ring: list[str],
) -> None:
    with pytest.raises(ValidationError) as captured:
        valid_production_settings(session_key_ring=session_key_ring)

    rendered = f"{captured.value}\n{captured.value.errors()!r}\n{captured.value.json()}"
    assert all(raw not in rendered for raw in session_key_ring if raw)


def test_session_key_format_is_validated_outside_production() -> None:
    with pytest.raises(ValidationError):
        valid_production_settings(environment="development", session_key_ring=["kid=too-short"])


@pytest.mark.parametrize("algorithm", ["none", "HS256", "PS256", "arbitrary", "rs256"])
def test_oidc_algorithms_reject_unsupported_or_symmetric_values(algorithm: str) -> None:
    with pytest.raises(ValidationError):
        valid_production_settings(oidc_algorithms=[algorithm])


def test_oidc_algorithms_accept_supported_unique_asymmetric_values() -> None:
    supported = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"]

    assert valid_production_settings(oidc_algorithms=supported).oidc_algorithms == supported
    with pytest.raises(ValidationError):
        valid_production_settings(oidc_algorithms=["RS256", "RS256"])


@pytest.mark.parametrize(
    "origin",
    [
        "http://peerassist.example.test",
        "https://peerassist.example.test/compat",
        "https://peerassist.example.test?mode=compat",
        "https://peerassist.example.test#compat",
        "https://user:password@peerassist.example.test",
    ],
)
def test_production_allowed_origins_are_https_origin_only(origin: str) -> None:
    with pytest.raises(ValidationError):
        valid_production_settings(allowed_origins=[origin])


def test_allowed_origin_accepts_explicit_https_port() -> None:
    settings = valid_production_settings(allowed_origins=["https://peerassist.example.test:8443"])

    assert settings.allowed_origins == ["https://peerassist.example.test:8443"]


def test_platform_settings_redact_secrets() -> None:
    settings = valid_production_settings()
    rendered = f"{settings!r}\n{settings.model_dump_json()}"

    assert "generated-secret" not in rendered
    assert "generated-access-key" not in rendered
    assert "9f4c7b0d5e3a1862c8f1d4a7b0e3956c2f8a1d4e7b0c3965a2f8d1e4b7c09365" not in rendered
    assert "**********" in rendered


def test_platform_validation_errors_never_retain_secret_inputs() -> None:
    secrets = {
        "database_url": "postgresql+psycopg://private-user:private-db-password@db/peerassist",
        "s3_access_key_id": "private-s3-access-key",
        "s3_secret_access_key": "private-s3-secret-key",
        "session_key_ring": ["private-key-id=private-session-key"],
    }

    with pytest.raises(ValidationError) as captured:
        valid_production_settings(public_base_url="http://peerassist.example.test", **secrets)

    rendered = "\n".join(
        [
            str(captured.value),
            repr(captured.value.errors()),
            captured.value.json(),
        ]
    )
    for secret in (
        "private-user",
        "private-db-password",
        "private-s3-access-key",
        "private-s3-secret-key",
        "private-key-id",
        "private-session-key",
    ):
        assert secret not in rendered

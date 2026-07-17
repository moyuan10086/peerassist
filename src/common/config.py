from __future__ import annotations

import base64
import binascii
import re
from functools import lru_cache
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

OIDC_ASYMMETRIC_ALGORITHMS = frozenset({"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"})
SESSION_KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SESSION_KEY_MATERIAL_PATTERN = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")


def _http_url(value: str, *, field_name: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{field_name} must use HTTP(S) and include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{field_name} must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError(f"{field_name} must not contain a query or fragment")
    return normalized


def _origin_url(value: str) -> str:
    normalized = value.strip()
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("allowed origin must use HTTP(S) and include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("allowed origin must not contain credentials")
    if parsed.path or parsed.query or parsed.fragment:
        raise ValueError("allowed origin must contain only scheme, host, and optional port")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("allowed origin contains an invalid port") from exc
    del port
    return normalized


def _decode_session_key_material(value: str) -> bytes:
    if re.fullmatch(r"[0-9A-Fa-f]{64}", value):
        return bytes.fromhex(value)
    if not SESSION_KEY_MATERIAL_PATTERN.fullmatch(value):
        raise ValueError("session key material must be hex or base64url")
    try:
        return base64.urlsafe_b64decode(value.rstrip("=") + "=" * (-len(value.rstrip("=")) % 4))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("session key material must be valid base64url") from exc


class PlatformSettings(BaseSettings):
    """Configuration for the M1 platform boundary, isolated from legacy runtime settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="PEERASSIST_PLATFORM_",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    database_url: SecretStr
    oidc_issuer: str
    oidc_audience: str
    oidc_algorithms: list[str] = Field(default_factory=lambda: ["RS256"], min_length=1)
    s3_endpoint: str
    s3_bucket: str = Field(min_length=1)
    s3_path_style: bool = True
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None
    public_base_url: str
    allowed_origins: list[str] = Field(default_factory=list)
    trusted_proxy_cidrs: list[str] = Field(default_factory=list)
    session_key_ring: list[SecretStr] = Field(default_factory=list)
    scratch_root: Path = Path("./data/platform-scratch")
    scratch_ttl_seconds: int = Field(default=86400, gt=0)
    internal_legacy_audience: str = Field(min_length=1)
    legacy_bind_host: str = "127.0.0.1"

    @model_validator(mode="before")
    @classmethod
    def wrap_secret_inputs(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        wrapped = dict(values)
        for field_name in ("database_url", "s3_access_key_id", "s3_secret_access_key"):
            value = wrapped.get(field_name)
            if value is not None and not isinstance(value, SecretStr):
                wrapped[field_name] = SecretStr(str(value))
        key_ring = wrapped.get("session_key_ring")
        if isinstance(key_ring, (list, tuple)):
            wrapped["session_key_ring"] = [
                value if isinstance(value, SecretStr) else SecretStr(str(value)) for value in key_ring
            ]
        return wrapped

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr, info: ValidationInfo) -> SecretStr:
        database_url = value.get_secret_value()
        parsed = urlsplit(database_url)
        if parsed.scheme != "postgresql+psycopg":
            raise ValueError("platform database URL must use PostgreSQL with Psycopg")
        if not parsed.hostname:
            raise ValueError("platform database URL must include a hostname")
        if not parsed.path.lstrip("/"):
            raise ValueError("platform database URL must include a database name")
        if info.data.get("environment") == "production" and any(
            default in database_url.lower() for default in ("postgres:postgres", "change-me")
        ):
            raise ValueError("production database credentials must not use defaults")
        return value

    @field_validator("oidc_algorithms")
    @classmethod
    def validate_oidc_algorithms(cls, values: list[str]) -> list[str]:
        if any(value not in OIDC_ASYMMETRIC_ALGORITHMS for value in values):
            raise ValueError("OIDC algorithms must use the supported asymmetric allowlist")
        if len(values) != len(set(values)):
            raise ValueError("OIDC algorithms must not contain duplicates")
        return values

    @field_validator("oidc_issuer", "s3_endpoint", "public_base_url")
    @classmethod
    def validate_platform_url(cls, value: str, info: ValidationInfo) -> str:
        normalized = _http_url(value, field_name=info.field_name)
        if (
            info.data.get("environment") == "production"
            and info.field_name in {"oidc_issuer", "public_base_url"}
            and urlsplit(normalized).scheme != "https"
        ):
            raise ValueError(f"production {info.field_name} must use HTTPS")
        return normalized

    @field_validator("s3_access_key_id", "s3_secret_access_key")
    @classmethod
    def validate_s3_credentials(cls, value: SecretStr | None, info: ValidationInfo) -> SecretStr | None:
        if value is None or info.data.get("environment") != "production":
            return value
        if value.get_secret_value().lower() in {"minioadmin", "change-me"}:
            raise ValueError("production object-store credentials must not use defaults")
        return value

    @field_validator("allowed_origins")
    @classmethod
    def validate_origins(cls, values: list[str], info: ValidationInfo) -> list[str]:
        normalized = [value if value == "*" else _origin_url(value) for value in values]
        if info.data.get("environment") == "production" and "*" in normalized:
            raise ValueError("production allowed origins must not contain wildcards")
        if info.data.get("environment") == "production" and any(
            urlsplit(value).scheme != "https" for value in normalized
        ):
            raise ValueError("production allowed origins must use HTTPS")
        return normalized

    @field_validator("trusted_proxy_cidrs")
    @classmethod
    def validate_proxy_cidrs(cls, values: list[str]) -> list[str]:
        for value in values:
            ip_network(value, strict=False)
        return values

    @field_validator("session_key_ring")
    @classmethod
    def validate_session_key_ring(
        cls, values: list[SecretStr], info: ValidationInfo
    ) -> list[SecretStr]:
        production = info.data.get("environment") == "production"
        if production and not values:
            raise ValueError("production session key ring must not be empty")
        key_ids: set[str] = set()
        for secret in values:
            serialized = secret.get_secret_value()
            key_id, separator, material = serialized.partition("=")
            if not separator or not SESSION_KEY_ID_PATTERN.fullmatch(key_id):
                raise ValueError("session key must use kid=key-material format")
            if key_id in key_ids:
                raise ValueError("session key IDs must be unique")
            key_ids.add(key_id)
            if production and material.lower() in {"change-me", "changeme", "placeholder", "secret"}:
                raise ValueError("production session keys must not use placeholders")
            decoded_material = _decode_session_key_material(material)
            if len(decoded_material) != 32:
                raise ValueError("session key material must decode to exactly 32 bytes")
            if len(set(decoded_material)) < 16:
                raise ValueError("session key material has insufficient byte diversity")
        return values

    @field_validator("legacy_bind_host")
    @classmethod
    def validate_legacy_bind_host(cls, value: str, info: ValidationInfo) -> str:
        if info.data.get("environment") != "production":
            return value
        try:
            legacy_address = ip_address(value.strip("[]"))
        except ValueError:
            legacy_address = None
        if value != "localhost" and not (legacy_address and legacy_address.is_loopback):
            raise ValueError("production legacy service must not bind publicly")
        return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "FactReview"

    data_dir: Path = Field(default=Path("./data"))

    # OpenAI Agent SDK runtime
    model_provider: str = Field(
        default="openai-codex",
        validation_alias=AliasChoices("MODEL_PROVIDER", "AGENT_MODEL_PROVIDER", "FACTREVIEW_MODEL_PROVIDER"),
    )
    openai_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_KEY", "API_KEY", "LLM_API_KEY"),
    )
    openai_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("BASE_URL", "OPENAI_BASE_URL", "LLM_BASE_URL"),
    )
    openai_use_responses_api: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "OPENAI_USE_RESPONSES_API",
            "USE_RESPONSES_API",
            "LLM_USE_RESPONSES_API",
        ),
    )
    agent_model: str = "gpt-5.5"
    openai_codex_model: str = Field(
        default="gpt-5.5",
        validation_alias=AliasChoices("OPENAI_CODEX_MODEL", "CODEX_MODEL"),
    )
    openai_codex_base_url: str = Field(
        default="https://chatgpt.com/backend-api/codex",
        validation_alias=AliasChoices("OPENAI_CODEX_BASE_URL", "CODEX_BASE_URL"),
    )
    agent_temperature: float = 0.2
    agent_max_tokens: int = 4096
    agent_max_turns: int = 1000
    agent_resume_attempts: int = 2

    # PeerAssist workspace model and internal review service.
    peerassist_openai_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "PEERASSIST_OPENAI_API_KEY", "EXECUTION_OPENAI_API_KEY", "OPENAI_API_KEY"
        ),
    )
    peerassist_openai_base_url: str = Field(
        default="https://api.openai.com/v1",
        validation_alias=AliasChoices(
            "PEERASSIST_OPENAI_BASE_URL", "EXECUTION_OPENAI_BASE_URL", "OPENAI_BASE_URL"
        ),
    )
    peerassist_openai_model: str = Field(
        default="gpt-5",
        validation_alias=AliasChoices(
            "PEERASSIST_OPENAI_MODEL", "EXECUTION_OPENAI_MODEL", "OPENAI_MODEL"
        ),
    )
    peerassist_openai_timeout_seconds: float = Field(
        default=240.0,
        gt=0,
        validation_alias=AliasChoices("PEERASSIST_OPENAI_TIMEOUT_SECONDS"),
    )
    peerassist_review_max_tokens: int = Field(
        default=900,
        validation_alias=AliasChoices("PEERASSIST_REVIEW_MAX_TOKENS"),
    )
    peerassist_review_api_url: str = Field(
        default="http://127.0.0.1:8767",
        validation_alias=AliasChoices("PEERASSIST_REVIEW_API_URL"),
    )

    @field_validator("peerassist_openai_base_url", "peerassist_review_api_url")
    @classmethod
    def validate_http_base_url(cls, value: str) -> str:
        return _http_url(value, field_name="URL")

    max_pdf_bytes: int = 50 * 1024 * 1024

    # MinerU v4 upload + parse
    mineru_base_url: str = "https://mineru.net/api/v4"
    mineru_api_token: str | None = None
    mineru_model_version: str = "vlm"
    mineru_upload_endpoint: str = "/file-urls/batch"
    # Comma-separated endpoint templates. Must include {batch_id}
    mineru_poll_endpoint_templates: str = (
        "/extract-results/batch/{batch_id},/extract-results/{batch_id},/extract/task/{batch_id}"
    )
    mineru_poll_interval_seconds: float = 3.0
    mineru_poll_timeout_seconds: int = 900
    # Default strict mode: keep MinerU parity and fail loudly if unavailable.
    mineru_allow_local_fallback: bool = False

    # Optional external paper search/read service
    paper_search_enabled: bool = True
    paper_search_provider: str = "arxiv"
    paper_search_base_url: str | None = None
    paper_search_api_key: str | None = None
    paper_search_endpoint: str = "/pasa/search"
    paper_search_timeout_seconds: int = 120
    paper_search_health_endpoint: str = "/health"
    paper_search_health_timeout_seconds: int = 5

    paper_read_base_url: str | None = None
    paper_read_api_key: str | None = None
    paper_read_endpoint: str = "/read"
    paper_read_timeout_seconds: int = 180

    # Objective retrieval for niche-positioning table (Section 2)
    semantic_scholar_enabled: bool = True
    semantic_scholar_base_url: str = "https://api.semanticscholar.org/graph/v1"
    semantic_scholar_api_key: str | None = None
    semantic_scholar_timeout_seconds: int = 20
    semantic_scholar_top_k: int = 8
    openalex_base_url: str = "https://api.openalex.org"
    openalex_api_key: str | None = None

    # Final-report finalization gates
    enable_final_gates: bool = False
    min_paper_search_calls_for_pdf_annotate: int = 3
    min_paper_search_calls_for_final: int = 3
    min_distinct_paper_queries_for_final: int = 3
    min_annotations_for_final: int = 10
    min_english_words_for_final: int = 0
    min_chinese_chars_for_final: int = 0
    force_english_output: bool = True
    ui_language: str = "en"
    enable_final_report_audit: bool = True
    final_report_audit_max_iterations: int = 3
    final_report_audit_max_source_chars: int = 80000
    final_report_audit_max_review_chars: int = 50000

    # Optional reference-accuracy checking via RefCopilot/.
    reference_check_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "FACTREVIEW_ENABLE_REFCHECK",
            "REFERENCE_CHECK_ENABLED",
            "REFCHECK_ENABLED",
            "ENABLE_REFCHECK",
        ),
    )
    reference_check_report_max_issues: int = 20

    # PDF export
    pdf_font_name: str = "Helvetica"
    pdf_title_font_size: int = 15
    pdf_body_font_size: int = 10
    pdf_page_margin: int = 48

    # Evaluation status threshold (absolute delta, directional by metric type)
    eval_status_threshold: float = 0.05

    # Toggle for the optional reference-check sweep that the execution stage's
    # refcheck node performs against the run's bibliography. Independent from
    # ``reference_check_enabled`` (the global gate).
    execution_enable_refcheck: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "FACTREVIEW_EXECUTION_ENABLE_REFCHECK",
            "EXECUTION_ENABLE_REFCHECK",
        ),
    )

    def mineru_poll_templates(self) -> list[str]:
        templates: list[str] = []
        for item in self.mineru_poll_endpoint_templates.split(","):
            normalized = item.strip()
            if not normalized:
                continue
            templates.append(normalized)
        return templates


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "jobs").mkdir(parents=True, exist_ok=True)
    return settings

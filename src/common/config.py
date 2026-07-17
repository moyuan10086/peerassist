from __future__ import annotations

from functools import lru_cache
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    @field_validator("oidc_issuer", "s3_endpoint", "public_base_url")
    @classmethod
    def validate_platform_url(cls, value: str, info: object) -> str:
        return _http_url(value, field_name=getattr(info, "field_name", "URL"))

    @field_validator("allowed_origins")
    @classmethod
    def validate_origins(cls, values: list[str]) -> list[str]:
        return [value if value == "*" else _http_url(value, field_name="allowed origin") for value in values]

    @field_validator("trusted_proxy_cidrs")
    @classmethod
    def validate_proxy_cidrs(cls, values: list[str]) -> list[str]:
        for value in values:
            ip_network(value, strict=False)
        return values

    @model_validator(mode="after")
    def validate_production_boundary(self) -> PlatformSettings:
        if self.environment != "production":
            return self

        if urlsplit(self.oidc_issuer).scheme != "https":
            raise ValueError("production OIDC issuer must use HTTPS")
        if urlsplit(self.public_base_url).scheme != "https":
            raise ValueError("production public base URL must use HTTPS")
        if "*" in self.allowed_origins:
            raise ValueError("production allowed origins must not contain wildcards")
        if not self.session_key_ring:
            raise ValueError("production session key ring must not be empty")
        try:
            legacy_address = ip_address(self.legacy_bind_host.strip("[]"))
        except ValueError:
            legacy_address = None
        if self.legacy_bind_host != "localhost" and not (legacy_address and legacy_address.is_loopback):
            raise ValueError("production legacy service must not bind publicly")

        database_url = self.database_url.get_secret_value().lower()
        if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError("platform database URL must use PostgreSQL with Psycopg")
        forbidden_defaults = {"postgres:postgres", "minioadmin"}
        credentials = [
            database_url,
            self.s3_access_key_id.get_secret_value().lower() if self.s3_access_key_id else "",
            self.s3_secret_access_key.get_secret_value().lower() if self.s3_secret_access_key else "",
        ]
        if any(default in credential for default in forbidden_defaults for credential in credentials):
            raise ValueError("production credentials must not use provider defaults")
        return self


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

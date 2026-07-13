"""Durable Paper, review job, checkpoint, and authorization contracts."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PAPER_SCHEMA_VERSION = "peerassist.paper.v1"
REVIEW_JOB_SCHEMA_VERSION = "peerassist.review_job.v2"
STAGE_CHECKPOINT_SCHEMA_VERSION = "peerassist.stage_checkpoint.v1"
STAGE_MANIFEST_SCHEMA_VERSION = "peerassist.stage_manifest.v1"
REVIEW_JOB_EVENT_SCHEMA_VERSION = "peerassist.review_job_event.v1"
FINAL_REPORT_MANIFEST_SCHEMA_VERSION = "peerassist.final_report_manifest.v1"
AUTH_SCHEMA_VERSION = "peerassist.auth.v1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def utcnow() -> datetime:
    return datetime.now(UTC)


def _validate_sha256(value: str, *, field_name: str) -> str:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a full 64-character lowercase SHA-256 digest")
    return value


def _validate_path(value: str, *, field_name: str, allow_absolute: bool) -> str:
    if not value or "\x00" in value:
        raise ValueError(f"{field_name} must be a non-empty safe path")
    if "\\" in value:
        raise ValueError(f"{field_name} must use forward slashes")

    posix_path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if not allow_absolute and (posix_path.is_absolute() or windows_path.is_absolute()):
        raise ValueError(f"{field_name} must be relative")
    if windows_path.drive:
        raise ValueError(f"{field_name} must not contain a Windows drive")
    if ".." in posix_path.parts:
        raise ValueError(f"{field_name} must not traverse parent directories")
    return value


def _validate_identifier(value: str, *, field_name: str) -> str:
    if not _SAFE_ID_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a safe identifier")
    return value


class ReviewStage(StrEnum):
    VALIDATE = "validate"
    PARSE = "parse"
    EVIDENCE = "evidence"
    PROFILE = "profile"
    PLAN = "plan"
    DETERMINISTIC = "deterministic"
    CITATION = "citation"
    AGENTS = "agents"
    INTEGRATE = "integrate"
    AWAIT_CONFIRMATION = "await_confirmation"
    FINALIZE = "finalize"
    COMPLETE = "complete"


class ReviewJobStatus(StrEnum):
    QUEUED = "queued"
    CANCEL_REQUESTED = "cancel_requested"
    INTERRUPTED = "interrupted"
    VALIDATING_INPUT = "validating_input"
    PARSING = "parsing"
    EVIDENCE_BUILDING = "evidence_building"
    PROFILING = "profiling"
    PLANNING_REVIEW = "planning_review"
    DETERMINISTIC_CHECKING = "deterministic_checking"
    CITATION_CHECKING = "citation_checking"
    AGENTS_RUNNING = "agents_running"
    INTEGRATING = "integrating"
    BLOCKED = "blocked"
    AWAITING_HUMAN_CONFIRMATION = "awaiting_human_confirmation"
    EXPORTING_REPORT = "exporting_report"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ConsentDecision(StrEnum):
    PENDING = "pending"
    GRANTED = "granted"
    DENIED = "denied"
    NOT_REQUIRED = "not_required"


class ExternalServiceConsent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: str = "peerassist.external_service_consent.v1"
    decision: ConsentDecision = ConsentDecision.PENDING
    service: str = ""
    decided_by: str | None = None
    decided_at: datetime | None = None
    reason: str = ""


class PaperRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: str = PAPER_SCHEMA_VERSION
    paper_id: str
    source_pdf_name: str = "paper.pdf"
    source_pdf_path: str = "source.pdf"
    size_bytes: int = Field(default=0, ge=0)
    content_type: str = "application/pdf"
    owner_principal_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("paper_id")
    @classmethod
    def validate_paper_id(cls, value: str) -> str:
        return _validate_sha256(value, field_name="paper_id")

    @field_validator("source_pdf_path")
    @classmethod
    def validate_source_pdf_path(cls, value: str) -> str:
        return _validate_path(value, field_name="source_pdf_path", allow_absolute=False)


class StageManifest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: str = STAGE_MANIFEST_SCHEMA_VERSION
    stage: ReviewStage
    attempt_id: str
    checkpoint_path: str
    output_dir: str
    artifacts: dict[str, str] = Field(default_factory=dict)
    artifact_sha256: dict[str, str] = Field(default_factory=dict)
    artifact_sizes: dict[str, int] = Field(default_factory=dict)
    committed_at: datetime = Field(default_factory=utcnow)

    @field_validator("attempt_id")
    @classmethod
    def validate_attempt_id(cls, value: str) -> str:
        return _validate_identifier(value, field_name="attempt_id")

    @field_validator("checkpoint_path", "output_dir")
    @classmethod
    def validate_manifest_path(cls, value: str, info: Any) -> str:
        return _validate_path(value, field_name=info.field_name, allow_absolute=False)

    @field_validator("artifacts")
    @classmethod
    def validate_artifact_paths(cls, value: dict[str, str]) -> dict[str, str]:
        for name, path in value.items():
            _validate_identifier(name, field_name="artifact name")
            _validate_path(path, field_name=f"artifact path {name}", allow_absolute=False)
        return value

    @field_validator("artifact_sha256")
    @classmethod
    def validate_artifact_hashes(cls, value: dict[str, str]) -> dict[str, str]:
        for name, digest in value.items():
            _validate_sha256(digest, field_name=f"artifact_sha256[{name}]")
        return value

    @field_validator("artifact_sizes")
    @classmethod
    def validate_artifact_sizes(cls, value: dict[str, int]) -> dict[str, int]:
        if any(size < 0 for size in value.values()):
            raise ValueError("artifact sizes must be non-negative")
        return value

    @model_validator(mode="after")
    def validate_artifact_metadata(self) -> StageManifest:
        artifact_names = set(self.artifacts)
        if set(self.artifact_sha256) != artifact_names or set(self.artifact_sizes) != artifact_names:
            raise ValueError("artifact hash and size entries must exactly match declared artifacts")
        return self


class StageCheckpoint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: str = STAGE_CHECKPOINT_SCHEMA_VERSION
    stage: ReviewStage
    attempt_id: str
    status: ReviewJobStatus
    committed: bool = False
    committed_at: datetime | None = None
    mode: Literal["fast"] = "fast"
    input_artifact_sha256: dict[str, str] = Field(default_factory=dict)
    implementation_version: str = ""
    retryable: bool = False
    cancel_observed: bool = False
    error_code: str | None = None
    manifest: StageManifest | None = None

    @field_validator("attempt_id")
    @classmethod
    def validate_attempt_id(cls, value: str) -> str:
        return _validate_identifier(value, field_name="attempt_id")

    @field_validator("input_artifact_sha256")
    @classmethod
    def validate_input_hashes(cls, value: dict[str, str]) -> dict[str, str]:
        for name, digest in value.items():
            _validate_sha256(digest, field_name=f"input_artifact_sha256[{name}]")
        return value

    @model_validator(mode="after")
    def validate_manifest_consistency(self) -> StageCheckpoint:
        if self.committed and self.manifest is None:
            raise ValueError("committed checkpoint requires a manifest")
        if self.committed and self.committed_at is None:
            raise ValueError("committed checkpoint requires committed_at")
        if self.manifest is None:
            return self
        if self.manifest.stage != self.stage:
            raise ValueError("checkpoint stage must match manifest stage")
        if self.manifest.attempt_id != self.attempt_id:
            raise ValueError("checkpoint attempt_id must match manifest attempt_id")
        if self.committed and self.committed_at != self.manifest.committed_at:
            raise ValueError("checkpoint committed_at must match manifest committed_at")
        return self


class ReviewJobState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: str = REVIEW_JOB_SCHEMA_VERSION
    id: UUID = Field(default_factory=uuid4)
    paper_id: str
    run_dir: str
    mode: Literal["fast"] = "fast"
    stage: ReviewStage = ReviewStage.VALIDATE
    status: ReviewJobStatus = ReviewJobStatus.QUEUED
    revision: int = Field(default=0, ge=0)
    attempt_id: str
    cancel_requested: bool = False
    last_event_id: int = Field(default=0, ge=0)
    confirmation_revision: int = Field(default=0, ge=0)
    current_stage_manifests: dict[ReviewStage, StageManifest] = Field(default_factory=dict)
    parse_consent: ExternalServiceConsent = Field(
        default_factory=lambda: ExternalServiceConsent(service="parse")
    )
    search_consent: ExternalServiceConsent = Field(
        default_factory=lambda: ExternalServiceConsent(service="search")
    )
    model_consent: ExternalServiceConsent = Field(
        default_factory=lambda: ExternalServiceConsent(service="model")
    )
    blocked_reason: str | None = None
    required_consents: list[Literal["parse", "search", "model"]] = Field(default_factory=list)
    resume_stage: ReviewStage | None = None
    error_code: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("paper_id")
    @classmethod
    def validate_paper_id(cls, value: str) -> str:
        return _validate_sha256(value, field_name="paper_id")

    @field_validator("run_dir")
    @classmethod
    def validate_run_dir(cls, value: str) -> str:
        return _validate_path(value, field_name="run_dir", allow_absolute=True)

    @field_validator("attempt_id")
    @classmethod
    def validate_attempt_id(cls, value: str) -> str:
        return _validate_identifier(value, field_name="attempt_id")

    @field_validator("required_consents")
    @classmethod
    def validate_required_consents(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("required_consents must not contain duplicates")
        return value

    @model_validator(mode="after")
    def validate_durable_state(self) -> ReviewJobState:
        for stage, manifest in self.current_stage_manifests.items():
            if stage != manifest.stage:
                raise ValueError("current stage manifest key must match manifest stage")
        if self.blocked_reason == "approval_required":
            if not self.required_consents:
                raise ValueError("approval_required block must name required_consents")
            if self.resume_stage is None:
                raise ValueError("approval_required block must persist resume_stage")
        return self


class ReviewJobEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: str = REVIEW_JOB_EVENT_SCHEMA_VERSION
    job_id: UUID
    event_id: int = Field(ge=1)
    event_type: str
    timestamp: datetime = Field(default_factory=utcnow)
    stage: ReviewStage | None = None
    status: ReviewJobStatus | None = None
    attempt_id: str | None = None
    message: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("attempt_id")
    @classmethod
    def validate_optional_attempt_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_identifier(value, field_name="attempt_id")


class FinalReportManifest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: str = FINAL_REPORT_MANIFEST_SCHEMA_VERSION
    job_id: UUID
    paper_id: str
    report_version: str
    confirmation_revision: int = Field(ge=0)
    artifacts: dict[str, str]
    artifact_sha256: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    override_reason: str | None = None

    @field_validator("paper_id")
    @classmethod
    def validate_paper_id(cls, value: str) -> str:
        return _validate_sha256(value, field_name="paper_id")

    @field_validator("report_version")
    @classmethod
    def validate_report_version(cls, value: str) -> str:
        return _validate_identifier(value, field_name="report_version")

    @field_validator("artifacts")
    @classmethod
    def validate_report_paths(cls, value: dict[str, str]) -> dict[str, str]:
        for name, path in value.items():
            _validate_identifier(name, field_name="report artifact name")
            _validate_path(path, field_name=f"report artifact path {name}", allow_absolute=False)
        return value

    @field_validator("artifact_sha256")
    @classmethod
    def validate_report_hashes(cls, value: dict[str, str]) -> dict[str, str]:
        for name, digest in value.items():
            _validate_sha256(digest, field_name=f"artifact_sha256[{name}]")
        return value

    @model_validator(mode="after")
    def validate_report_artifacts(self) -> FinalReportManifest:
        if set(self.artifact_sha256) - set(self.artifacts):
            raise ValueError("report hashes must reference declared artifacts")
        return self


class Principal(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: str = AUTH_SCHEMA_VERSION
    principal_id: str
    display_name: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    disabled: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("principal_id")
    @classmethod
    def validate_principal_id(cls, value: str) -> str:
        return _validate_identifier(value, field_name="principal_id")


class SessionRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: str = AUTH_SCHEMA_VERSION
    session_id: UUID = Field(default_factory=uuid4)
    principal_id: str
    token_hash: str
    csrf_token_hash: str
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    revoked_at: datetime | None = None
    origin: str | None = None

    @field_validator("principal_id")
    @classmethod
    def validate_principal_id(cls, value: str) -> str:
        return _validate_identifier(value, field_name="principal_id")

    @model_validator(mode="after")
    def validate_expiry(self) -> SessionRecord:
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")
        return self


class ResourceRole(StrEnum):
    OWNER = "owner"
    REVIEWER = "reviewer"
    READ_ONLY = "read_only"


class ResourceGrant(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: str = AUTH_SCHEMA_VERSION
    grant_id: UUID = Field(default_factory=uuid4)
    principal_id: str
    resource_type: Literal["paper", "job"]
    resource_id: str
    role: ResourceRole
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    granted_by: str | None = None

    @field_validator("principal_id", "resource_id")
    @classmethod
    def validate_resource_identifier(cls, value: str, info: Any) -> str:
        if info.field_name == "resource_id" and _SHA256_RE.fullmatch(value):
            return value
        return _validate_identifier(value, field_name=info.field_name)

    @model_validator(mode="after")
    def validate_expiry(self) -> ResourceGrant:
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")
        return self

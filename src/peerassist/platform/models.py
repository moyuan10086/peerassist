"""Provider-independent domain records for the M1 platform boundary."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import TypeAlias
from uuid import UUID, uuid5

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


class FrozenJsonArray(tuple["FrozenJsonValue", ...]):
    """Tuple-compatible marker that distinguishes frozen JSON from caller tuples."""


FrozenJsonValue: TypeAlias = (
    None | bool | int | float | str | FrozenJsonArray | Mapping[str, "FrozenJsonValue"]
)

_SENSITIVE_KEY_WORDS = frozenset(
    {"authorization", "cookie", "credential", "password", "secret", "token"}
)
_SENSITIVE_KEY_PHRASES = frozenset(
    {
        ("api", "key"),
        ("manuscript", "content"),
        ("object", "key"),
        ("presigned", "url"),
        ("private", "path"),
        ("provider", "response"),
        ("signed", "url"),
    }
)
_SENSITIVE_COMPACT_SUFFIXES = (
    "apikey",
    "authorization",
    "clientsecret",
    "credential",
    "manuscripttext",
    "objectkey",
    "password",
    "pdfcontent",
    "presignedurl",
    "privatepath",
    "providerresponse",
    "rawtoken",
    "refreshtoken",
    "secret",
    "signedurl",
    "token",
)
_KEY_SEPARATOR = re.compile(r"[^a-z0-9]+")
_STANDARD_OIDC_CLAIMS = frozenset(
    {
        "address",
        "aud",
        "auth_time",
        "azp",
        "birthdate",
        "email",
        "email_verified",
        "exp",
        "family_name",
        "gender",
        "given_name",
        "groups",
        "iat",
        "iss",
        "jti",
        "locale",
        "middle_name",
        "name",
        "nbf",
        "nickname",
        "phone_number",
        "phone_number_verified",
        "picture",
        "preferred_username",
        "profile",
        "roles",
        "sub",
        "updated_at",
        "website",
        "zoneinfo",
    }
)
_SAFE_CLAIM_EXTENSION = re.compile(r"[A-Za-z][A-Za-z0-9_.:/-]{0,127}")


def freeze_json(value: JsonValue | FrozenJsonValue) -> FrozenJsonValue:
    """Take a recursive immutable snapshot of JSON-compatible data."""
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return value
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError("JSON strings must be valid Unicode") from error
        return value
    if isinstance(value, FrozenJsonArray):
        return FrozenJsonArray(freeze_json(item) for item in value)
    if isinstance(value, list):
        return FrozenJsonArray(freeze_json(item) for item in value)
    if isinstance(value, tuple):
        raise TypeError("ordinary tuples are ambiguous JSON values")
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        return MappingProxyType({key: freeze_json(item) for key, item in value.items()})
    raise TypeError("value must contain only JSON-compatible data")


def mutable_json(value: JsonValue | FrozenJsonValue) -> JsonValue:
    """Return detached JSON-native data for serialization and adapters."""
    if isinstance(value, Mapping):
        return {key: mutable_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [mutable_json(item) for item in value]
    if isinstance(value, FrozenJsonArray):
        return [mutable_json(item) for item in value]
    if isinstance(value, tuple):
        raise TypeError("ordinary tuples are ambiguous JSON values")
    return value


def freeze_public_json(value: JsonValue | FrozenJsonValue) -> FrozenJsonValue:
    """Freeze public data after rejecting secret fields and private paths."""
    _validate_public_json(value)
    return freeze_json(value)


def freeze_identity_claims(value: Mapping[str, JsonValue]) -> Mapping[str, FrozenJsonValue]:
    """Freeze standard OIDC claims and syntactically safe provider extensions."""
    for key in value:
        if _is_sensitive_public_key(key):
            raise ValueError("safe public JSON must not contain secret or provider-private fields")
        if key not in _STANDARD_OIDC_CLAIMS and _SAFE_CLAIM_EXTENSION.fullmatch(key) is None:
            raise ValueError("identity claim key is neither standard nor a safe extension")
    frozen = freeze_public_json(value)
    if not isinstance(frozen, Mapping):
        raise TypeError("identity claims must be a JSON object")
    return frozen


def _is_sensitive_public_key(key: str) -> bool:
    words = tuple(word for word in _KEY_SEPARATOR.sub("_", key.casefold()).split("_") if word)
    if any(word in _SENSITIVE_KEY_WORDS for word in words):
        return True
    compact = "".join(words)
    if compact.endswith(_SENSITIVE_COMPACT_SUFFIXES):
        return True
    return any(
        words[index : index + len(phrase)] == phrase
        for phrase in _SENSITIVE_KEY_PHRASES
        for index in range(len(words) - len(phrase) + 1)
    )


def _validate_public_json(value: JsonValue | FrozenJsonValue) -> None:
    if isinstance(value, str):
        if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute() or value.startswith("file:"):
            raise ValueError("safe public JSON must not contain absolute private paths")
        return
    if isinstance(value, (list, FrozenJsonArray)):
        for item in value:
            _validate_public_json(item)
        return
    if isinstance(value, tuple):
        raise TypeError("ordinary tuples are ambiguous JSON values")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("safe public JSON keys must be strings")
            if _is_sensitive_public_key(key):
                raise ValueError("safe public JSON must not contain secret or provider-private fields")
            _validate_public_json(item)


def _uuid(value: UUID, name: str) -> None:
    if not isinstance(value, UUID):
        raise TypeError(f"{name} must be a UUID")
    if value.int == 0:
        raise ValueError(f"{name} must not be the nil UUID")


def _utc(value: datetime | None, name: str) -> None:
    if value is None:
        return
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware UTC")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError(f"{name} must be UTC")


def _positive(value: int, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _nonnegative(value: int, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")


def _nonempty(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")


def _bounded_string(value: str, name: str, maximum_length: int, *, empty: bool = False) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not empty and not value.strip():
        raise ValueError(f"{name} must not be empty")
    if len(value) > maximum_length:
        raise ValueError(f"{name} must be at most {maximum_length} characters")


def _freeze_bounded_json(
    value: JsonValue | FrozenJsonValue,
    name: str,
    maximum_bytes: int,
) -> FrozenJsonValue:
    frozen = freeze_json(value)
    encoded = json.dumps(
        mutable_json(frozen),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > maximum_bytes:
        raise ValueError(f"{name} JSON must be at most {maximum_bytes} bytes")
    return frozen


def _sha256(value: str, name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _opaque(value: str, name: str) -> None:
    _nonempty(value, name)
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute() or value.startswith("file:"):
        raise ValueError(f"{name} must be an opaque identifier, not an absolute private path")


def _tenant_ids(organization_id: UUID, project_id: UUID | None = None) -> None:
    _uuid(organization_id, "organization_id")
    if project_id is not None:
        _uuid(project_id, "project_id")


class ActorKind(StrEnum):
    USER = "user"
    OPERATOR = "operator"
    SERVICE = "service"


class Role(StrEnum):
    ORGANIZATION_ADMIN = "organization_admin"
    PROJECT_OWNER = "project_owner"
    REVIEWER = "reviewer"
    VIEWER = "viewer"
    SERVICE_AGENT = "service_agent"


class Action(StrEnum):
    ORGANIZATION_BOOTSTRAP = "organization.bootstrap"
    ORGANIZATION_READ = "organization.read"
    ORGANIZATION_MANAGE_POLICY = "organization.manage_policy"
    ORGANIZATION_MANAGE_MEMBERS = "organization.manage_members"
    ORGANIZATION_READ_AUDIT = "organization.read_audit"
    PROJECT_READ = "project.read"
    PROJECT_CREATE = "project.create"
    PROJECT_MANAGE_SETTINGS = "project.manage_settings"
    PROJECT_MANAGE_MEMBERS = "project.manage_members"
    PAPER_READ = "paper.read"
    PAPER_UPLOAD = "paper.upload"
    PAPER_ADD_VERSION = "paper.add_version"
    REVIEW_JOB_READ = "review_job.read"
    REVIEW_JOB_CREATE = "review_job.create"
    REVIEW_JOB_CANCEL = "review_job.cancel"
    REVIEW_JOB_RETRY = "review_job.retry"
    REVIEW_EVENT_READ = "review_event.read"
    ARTIFACT_READ = "artifact.read"
    CONCERN_DECIDE = "concern.decide"
    REPORT_DRAFT = "report.draft"
    REPORT_FINALIZE = "report.finalize"
    RETENTION_MANAGE = "retention.manage"
    PUBLICATION_GRANT = "publication.grant"
    LEGACY_READ = "legacy.read"
    INTERNAL_TOOL_EXECUTE = "internal.tool_execute"
    INTERNAL_WORK_CLAIM = "internal.work_claim"
    INTERNAL_WORK_COMPLETE = "internal.work_complete"
    ORGANIZATION_MEMBERSHIP_GRANT = "organization_membership.grant"
    ORGANIZATION_MEMBERSHIP_CHANGE = "organization_membership.change"
    ORGANIZATION_MEMBERSHIP_REVOKE = "organization_membership.revoke"
    PROJECT_MEMBERSHIP_GRANT = "project_membership.grant"
    PROJECT_MEMBERSHIP_CHANGE = "project_membership.change"
    PROJECT_MEMBERSHIP_REVOKE = "project_membership.revoke"
    IDENTITY_DISABLE = "identity.disable"
    IDENTITY_UNLINK = "identity.unlink"


class Decision(StrEnum):
    ALLOW = "allow"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"


class IdempotencyDecision(StrEnum):
    EXECUTE = "execute"
    REPLAY = "replay"


@dataclass(frozen=True, slots=True)
class TenantScope:
    organization_id: UUID
    project_id: UUID | None = None

    def __post_init__(self) -> None:
        _tenant_ids(self.organization_id, self.project_id)

    def contains(self, other: TenantScope) -> bool:
        return self.organization_id == other.organization_id and (
            self.project_id is None or self.project_id == other.project_id
        )


@dataclass(frozen=True, slots=True)
class InternalServiceGrant:
    service_actor_id: UUID
    scope: TenantScope
    audience: str
    actions: frozenset[Action]
    issued_at: datetime
    expires_at: datetime
    grant_id: UUID | None = None

    def __post_init__(self) -> None:
        _uuid(self.service_actor_id, "service_actor_id")
        if self.grant_id is not None:
            _uuid(self.grant_id, "grant_id")
        if not isinstance(self.scope, TenantScope):
            raise TypeError("scope must be a TenantScope")
        _nonempty(self.audience, "audience")
        if not isinstance(self.actions, frozenset) or not self.actions:
            raise ValueError("actions must be a nonempty frozenset")
        if any(not isinstance(action, Action) for action in self.actions):
            raise TypeError("actions must contain Action values")
        internal_actions = {
            Action.INTERNAL_TOOL_EXECUTE,
            Action.INTERNAL_WORK_CLAIM,
            Action.INTERNAL_WORK_COMPLETE,
        }
        if not self.actions.issubset(internal_actions):
            raise ValueError("service grants may contain only internal actions")
        _utc(self.issued_at, "issued_at")
        _utc(self.expires_at, "expires_at")
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")
        if self.expires_at - self.issued_at > timedelta(minutes=15):
            raise ValueError("service grants must be short-lived (at most 15 minutes)")


@dataclass(frozen=True, slots=True)
class Actor:
    actor_id: UUID
    kind: ActorKind
    identity_id: UUID | None = None

    def __post_init__(self) -> None:
        _uuid(self.actor_id, "actor_id")
        if self.identity_id is not None:
            _uuid(self.identity_id, "identity_id")
        if not isinstance(self.kind, ActorKind):
            raise TypeError("kind must be an ActorKind")


@dataclass(frozen=True, slots=True)
class User:
    id: UUID
    status: str
    display_name: str
    created_at: datetime
    updated_at: datetime
    version: int = 1

    def __post_init__(self) -> None:
        _uuid(self.id, "id")
        _nonempty(self.status, "status")
        _nonempty(self.display_name, "display_name")
        _positive(self.version, "version")
        _utc(self.created_at, "created_at")
        _utc(self.updated_at, "updated_at")


@dataclass(frozen=True, slots=True)
class ExternalIdentity:
    id: UUID
    user_id: UUID
    issuer: str
    subject: str
    verified_claims: Mapping[str, JsonValue]
    last_seen_at: datetime
    created_at: datetime
    disabled_at: datetime | None = None
    version: int = 1

    def __post_init__(self) -> None:
        _uuid(self.id, "id")
        _uuid(self.user_id, "user_id")
        _nonempty(self.issuer, "issuer")
        _nonempty(self.subject, "subject")
        _positive(self.version, "version")
        _utc(self.last_seen_at, "last_seen_at")
        _utc(self.created_at, "created_at")
        _utc(self.disabled_at, "disabled_at")
        object.__setattr__(self, "verified_claims", freeze_identity_claims(self.verified_claims))


@dataclass(frozen=True, slots=True)
class BrowserSession:
    id: UUID
    user_id: UUID
    identity_id: UUID
    session_digest: str
    csrf_digest: str
    provider_credential_ref: str
    expires_at: datetime
    idle_expires_at: datetime
    created_at: datetime
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        _uuid(self.id, "id")
        _uuid(self.user_id, "user_id")
        _uuid(self.identity_id, "identity_id")
        _sha256(self.session_digest, "session_digest")
        _sha256(self.csrf_digest, "csrf_digest")
        _opaque(self.provider_credential_ref, "provider_credential_ref")
        for name in ("expires_at", "idle_expires_at", "created_at", "revoked_at"):
            _utc(getattr(self, name), name)
        if self.idle_expires_at > self.expires_at:
            raise ValueError("idle_expires_at must not exceed expires_at")


@dataclass(frozen=True, slots=True)
class OidcTransaction:
    id: UUID
    state_digest: str
    nonce_digest: str
    encrypted_pkce_verifier: bytes
    encryption_key_id: str
    return_path: str
    expires_at: datetime
    created_at: datetime
    consumed_at: datetime | None = None

    def __post_init__(self) -> None:
        _uuid(self.id, "id")
        _sha256(self.state_digest, "state_digest")
        _sha256(self.nonce_digest, "nonce_digest")
        if not isinstance(self.encrypted_pkce_verifier, bytes) or not self.encrypted_pkce_verifier:
            raise ValueError("encrypted_pkce_verifier must contain protected server-side material")
        _nonempty(self.encryption_key_id, "encryption_key_id")
        if not self.return_path.startswith("/") or self.return_path.startswith("//") or "://" in self.return_path:
            raise ValueError("return_path must be an allowlisted relative application path")
        for name in ("expires_at", "created_at", "consumed_at"):
            _utc(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class Organization:
    id: UUID
    slug: str
    name: str
    status: str
    version: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _uuid(self.id, "id")
        for name in ("slug", "name", "status"):
            _nonempty(getattr(self, name), name)
        _positive(self.version, "version")
        _utc(self.created_at, "created_at")
        _utc(self.updated_at, "updated_at")


@dataclass(frozen=True, slots=True)
class OrganizationMembership:
    id: UUID
    organization_id: UUID
    user_id: UUID
    role: Role
    status: str
    version: int
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        _uuid(self.id, "id")
        _tenant_ids(self.organization_id)
        _uuid(self.user_id, "user_id")
        if self.role is not Role.ORGANIZATION_ADMIN:
            raise ValueError("organization membership role must be organization_admin")
        _nonempty(self.status, "status")
        _positive(self.version, "version")
        for name in ("created_at", "updated_at", "revoked_at"):
            _utc(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class Project:
    id: UUID
    organization_id: UUID
    name: str
    status: str
    version: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _uuid(self.id, "id")
        _tenant_ids(self.organization_id, self.id)
        _nonempty(self.name, "name")
        _nonempty(self.status, "status")
        _positive(self.version, "version")
        _utc(self.created_at, "created_at")
        _utc(self.updated_at, "updated_at")

    @property
    def scope(self) -> TenantScope:
        return TenantScope(self.organization_id, self.id)


@dataclass(frozen=True, slots=True)
class ProjectMembership:
    id: UUID
    organization_id: UUID
    project_id: UUID
    user_id: UUID
    role: Role
    status: str
    version: int
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None = None
    is_default: bool = False

    def __post_init__(self) -> None:
        _uuid(self.id, "id")
        _tenant_ids(self.organization_id, self.project_id)
        _uuid(self.user_id, "user_id")
        if self.role not in {Role.PROJECT_OWNER, Role.REVIEWER, Role.VIEWER}:
            raise ValueError("project membership role must be project_owner, reviewer, or viewer")
        _nonempty(self.status, "status")
        _positive(self.version, "version")
        if not isinstance(self.is_default, bool):
            raise TypeError("is_default must be a boolean")
        if self.is_default and (self.status != "active" or self.revoked_at is not None):
            raise ValueError("only an active project membership may be the default")
        for name in ("created_at", "updated_at", "revoked_at"):
            _utc(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class Paper:
    id: UUID
    organization_id: UUID
    project_id: UUID
    content_sha256: str
    current_version_id: UUID
    status: str
    version: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _uuid(self.id, "id")
        _tenant_ids(self.organization_id, self.project_id)
        _uuid(self.current_version_id, "current_version_id")
        _sha256(self.content_sha256, "content_sha256")
        _nonempty(self.status, "status")
        _positive(self.version, "version")
        _utc(self.created_at, "created_at")
        _utc(self.updated_at, "updated_at")


@dataclass(frozen=True, slots=True)
class PaperVersion:
    id: UUID
    organization_id: UUID
    project_id: UUID
    paper_id: UUID
    source_object_id: str
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    created_by: UUID
    created_at: datetime
    revision: int = 1

    def __post_init__(self) -> None:
        _uuid(self.id, "id")
        _tenant_ids(self.organization_id, self.project_id)
        _uuid(self.paper_id, "paper_id")
        _uuid(self.created_by, "created_by")
        _opaque(self.source_object_id, "source_object_id")
        _nonempty(self.filename, "filename")
        if PurePosixPath(self.filename).is_absolute() or PureWindowsPath(self.filename).is_absolute():
            raise ValueError("filename must not be an absolute private path")
        _nonempty(self.media_type, "media_type")
        _nonnegative(self.size_bytes, "size_bytes")
        _sha256(self.sha256, "sha256")
        _positive(self.revision, "revision")
        _utc(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class ReviewJob:
    id: UUID
    organization_id: UUID
    project_id: UUID
    paper_version_id: UUID
    mode: str
    stage: str
    status: str
    version: int
    attempt: int
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    cancelled_at: datetime | None = None
    safe_error_code: str | None = None

    def __post_init__(self) -> None:
        _uuid(self.id, "id")
        _tenant_ids(self.organization_id, self.project_id)
        _uuid(self.paper_version_id, "paper_version_id")
        _uuid(self.created_by, "created_by")
        for name in ("mode", "stage", "status"):
            _nonempty(getattr(self, name), name)
        _positive(self.version, "version")
        _nonnegative(self.attempt, "attempt")
        for name in ("created_at", "updated_at", "cancelled_at"):
            _utc(getattr(self, name), name)


CONSENT_STATUSES = frozenset(
    {"pending", "granted", "denied", "revoked", "expired", "not_required"}
)
REVIEW_DOCUMENT_SECTIONS = (
    "overall_assessment",
    "major_issues",
    "minor_issues",
    "revision_suggestions",
)
_REVIEW_DOCUMENT_SECTION_SET = frozenset(REVIEW_DOCUMENT_SECTIONS)


@dataclass(frozen=True, slots=True)
class ExternalServiceConsent:
    id: UUID
    organization_id: UUID
    project_id: UUID
    review_job_id: UUID
    paper_version_id: UUID
    service: str
    provider_config_revision: int
    policy_version: str
    data_scope: Mapping[str, JsonValue]
    status: str
    generation: int
    version: int
    decided_by: UUID | None
    decided_at: datetime | None
    expires_at: datetime | None
    superseded_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _uuid(self.id, "id")
        _tenant_ids(self.organization_id, self.project_id)
        _uuid(self.review_job_id, "review_job_id")
        _uuid(self.paper_version_id, "paper_version_id")
        _bounded_string(self.service, "service", 128)
        _positive(self.provider_config_revision, "provider_config_revision")
        _bounded_string(self.policy_version, "policy_version", 128)
        if not isinstance(self.data_scope, Mapping):
            raise TypeError("data_scope must be a JSON object")
        frozen_scope = _freeze_bounded_json(self.data_scope, "data_scope", 64 * 1024)
        if not isinstance(frozen_scope, Mapping):
            raise TypeError("data_scope must be a JSON object")
        object.__setattr__(self, "data_scope", frozen_scope)
        if self.status not in CONSENT_STATUSES:
            raise ValueError(f"status must be one of {sorted(CONSENT_STATUSES)}")
        _positive(self.generation, "generation")
        _positive(self.version, "version")
        if self.decided_by is not None:
            _uuid(self.decided_by, "decided_by")
        if (self.decided_by is None) != (self.decided_at is None):
            raise ValueError("decided_by and decided_at must be recorded together")
        for name in ("decided_at", "expires_at", "superseded_at", "created_at", "updated_at"):
            _utc(getattr(self, name), name)
        if self.status in {"granted", "denied", "revoked", "expired", "not_required"} and self.decided_at is None:
            raise ValueError(f"{self.status} consent must include a decision")
        if self.expires_at is not None and self.decided_at is not None and self.expires_at <= self.decided_at:
            raise ValueError("expires_at must be after decided_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        if self.superseded_at is not None and self.superseded_at < self.created_at:
            raise ValueError("superseded_at must not precede created_at")

    def is_effective(self, *, provider_config_revision: int, at: datetime) -> bool:
        _positive(provider_config_revision, "provider_config_revision")
        _utc(at, "at")
        return (
            self.status == "granted"
            and self.decided_at is not None
            and self.decided_at <= at
            and self.superseded_at is None
            and self.provider_config_revision == provider_config_revision
            and (self.expires_at is None or at < self.expires_at)
        )


@dataclass(frozen=True, slots=True)
class ReviewDocumentBlock:
    id: UUID
    section: str
    text: str
    source_type: str
    finding_lineage_id: str | None = None
    finding_id: str | None = None
    finding_revision: int | None = None
    evidence_ids: tuple[str, ...] = ()
    evidence_locator: Mapping[str, JsonValue] | None = None

    def __post_init__(self) -> None:
        _uuid(self.id, "id")
        if self.section not in _REVIEW_DOCUMENT_SECTION_SET:
            raise ValueError(f"section must be one of {REVIEW_DOCUMENT_SECTIONS}")
        _bounded_string(self.text, "text", 100_000, empty=True)
        _bounded_string(self.source_type, "source_type", 64)
        finding_values = (self.finding_lineage_id, self.finding_id, self.finding_revision)
        if any(value is not None for value in finding_values) and not all(
            value is not None for value in finding_values
        ):
            raise ValueError("finding lineage, ID, and revision must be recorded together")
        if self.finding_lineage_id is not None:
            _bounded_string(self.finding_lineage_id, "finding_lineage_id", 256)
            _bounded_string(self.finding_id, "finding_id", 256)  # type: ignore[arg-type]
            _positive(self.finding_revision, "finding_revision")  # type: ignore[arg-type]
        if not isinstance(self.evidence_ids, (tuple, list)):
            raise TypeError("evidence_ids must be a tuple or list of identifiers")
        evidence_ids = tuple(self.evidence_ids)
        if len(evidence_ids) > 256:
            raise ValueError("evidence_ids must contain at most 256 identifiers")
        for evidence_id in evidence_ids:
            _bounded_string(evidence_id, "evidence_id", 256)
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("evidence_ids must not contain duplicates")
        object.__setattr__(self, "evidence_ids", evidence_ids)
        if self.evidence_locator is not None:
            if not isinstance(self.evidence_locator, Mapping):
                raise TypeError("evidence_locator must be a JSON object")
            locator = _freeze_bounded_json(
                self.evidence_locator,
                "evidence_locator",
                64 * 1024,
            )
            if not isinstance(locator, Mapping):
                raise TypeError("evidence_locator must be a JSON object")
            object.__setattr__(self, "evidence_locator", locator)


@dataclass(frozen=True, slots=True)
class ReviewDocument:
    id: UUID
    organization_id: UUID
    project_id: UUID
    review_job_id: UUID
    blocks: tuple[ReviewDocumentBlock, ...]
    document_version: int
    base_decision_event_id: UUID | None
    last_edited_by: UUID
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _uuid(self.id, "id")
        _tenant_ids(self.organization_id, self.project_id)
        _uuid(self.review_job_id, "review_job_id")
        _uuid(self.last_edited_by, "last_edited_by")
        if self.base_decision_event_id is not None:
            _uuid(self.base_decision_event_id, "base_decision_event_id")
        _positive(self.document_version, "document_version")
        if not isinstance(self.blocks, (tuple, list)):
            raise TypeError("blocks must be a tuple or list of ReviewDocumentBlock values")
        blocks = tuple(self.blocks)
        if len(blocks) > 10_000:
            raise ValueError("blocks must contain at most 10000 entries")
        if any(not isinstance(block, ReviewDocumentBlock) for block in blocks):
            raise TypeError("blocks must contain only ReviewDocumentBlock values")
        if len({block.id for block in blocks}) != len(blocks):
            raise ValueError("block IDs must be unique within a review document")
        if {block.section for block in blocks} != _REVIEW_DOCUMENT_SECTION_SET:
            raise ValueError("review document must contain all four sections")
        object.__setattr__(self, "blocks", blocks)
        _utc(self.created_at, "created_at")
        _utc(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")

    @classmethod
    def empty(
        cls,
        *,
        id: UUID,
        organization_id: UUID,
        project_id: UUID,
        review_job_id: UUID,
        last_edited_by: UUID,
        created_at: datetime,
    ) -> ReviewDocument:
        return cls(
            id=id,
            organization_id=organization_id,
            project_id=project_id,
            review_job_id=review_job_id,
            blocks=tuple(
                ReviewDocumentBlock(
                    id=uuid5(id, f"peerassist.review-document.section:{section}"),
                    section=section,
                    text="",
                    source_type="manual",
                )
                for section in REVIEW_DOCUMENT_SECTIONS
            ),
            document_version=1,
            base_decision_event_id=None,
            last_edited_by=last_edited_by,
            created_at=created_at,
            updated_at=created_at,
        )


@dataclass(frozen=True, slots=True)
class ReportVersion:
    id: UUID
    organization_id: UUID
    project_id: UUID
    job_id: UUID
    revision: int
    schema_version: int
    content_sha256: str
    status: str
    created_by: UUID
    created_at: datetime
    published_at: datetime | None = None
    superseded_at: datetime | None = None

    def __post_init__(self) -> None:
        _uuid(self.id, "id")
        _tenant_ids(self.organization_id, self.project_id)
        _uuid(self.job_id, "job_id")
        _uuid(self.created_by, "created_by")
        _positive(self.revision, "revision")
        _positive(self.schema_version, "schema_version")
        _sha256(self.content_sha256, "content_sha256")
        if self.status not in {"draft", "published", "superseded"}:
            raise ValueError("status must be draft, published, or superseded")
        for name in ("created_at", "published_at", "superseded_at"):
            _utc(getattr(self, name), name)
        if self.published_at is not None and self.published_at < self.created_at:
            raise ValueError("published_at must not precede created_at")
        valid_state_timestamps = (
            self.status == "draft"
            and self.published_at is None
            and self.superseded_at is None
        ) or (
            self.status == "published"
            and self.published_at is not None
            and self.superseded_at is None
        ) or (
            self.status == "superseded"
            and self.published_at is not None
            and self.superseded_at is not None
            and self.superseded_at >= self.published_at
        )
        if not valid_state_timestamps:
            raise ValueError("status and timestamps must describe the same report lifecycle state")


@dataclass(frozen=True, slots=True)
class ReviewEvent:
    id: UUID
    organization_id: UUID
    project_id: UUID
    job_id: UUID
    aggregate_sequence: int
    event_type: str
    schema_version: int
    payload: Mapping[str, JsonValue]
    created_at: datetime

    def __post_init__(self) -> None:
        _uuid(self.id, "id")
        _tenant_ids(self.organization_id, self.project_id)
        _uuid(self.job_id, "job_id")
        _positive(self.aggregate_sequence, "aggregate_sequence")
        _nonempty(self.event_type, "event_type")
        _positive(self.schema_version, "schema_version")
        _utc(self.created_at, "created_at")
        object.__setattr__(self, "payload", freeze_public_json(self.payload))


@dataclass(frozen=True, slots=True)
class CommandRecord:
    id: UUID
    organization_id: UUID
    actor_id: UUID
    operation: str
    idempotency_key: str
    payload_digest: str
    created_at: datetime
    project_id: UUID | None = None
    response_status: int | None = None
    response_body: JsonValue = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        _uuid(self.id, "id")
        _tenant_ids(self.organization_id, self.project_id)
        _uuid(self.actor_id, "actor_id")
        _nonempty(self.operation, "operation")
        _nonempty(self.idempotency_key, "idempotency_key")
        _sha256(self.payload_digest, "payload_digest")
        _utc(self.created_at, "created_at")
        _utc(self.completed_at, "completed_at")
        if (self.response_status is None) != (self.completed_at is None):
            raise ValueError("response_status and completed_at must be recorded together")
        if self.response_status is not None and not 100 <= self.response_status <= 599:
            raise ValueError("response_status must be a valid HTTP status")
        object.__setattr__(self, "response_body", freeze_public_json(self.response_body))


@dataclass(frozen=True, slots=True)
class WorkItem:
    id: UUID
    organization_id: UUID
    project_id: UUID
    job_id: UUID
    attempt_id: UUID
    stage: str
    input_revision: int
    attempt_count: int
    max_attempts: int
    available_at: datetime
    created_at: datetime
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    dead_lettered_at: datetime | None = None
    safe_error_code: str | None = None

    def __post_init__(self) -> None:
        _uuid(self.id, "id")
        _tenant_ids(self.organization_id, self.project_id)
        _uuid(self.job_id, "job_id")
        _uuid(self.attempt_id, "attempt_id")
        _nonempty(self.stage, "stage")
        _nonnegative(self.input_revision, "input_revision")
        _nonnegative(self.attempt_count, "attempt_count")
        _positive(self.max_attempts, "max_attempts")
        if self.attempt_count > self.max_attempts:
            raise ValueError("attempt_count must not exceed max_attempts")
        for name in ("available_at", "created_at", "lease_expires_at", "dead_lettered_at"):
            _utc(getattr(self, name), name)
        if (self.lease_owner is None) != (self.lease_expires_at is None):
            raise ValueError("lease owner and lease expiry must be set together")
        if self.lease_owner is not None:
            _nonempty(self.lease_owner, "lease_owner")
            if self.lease_expires_at <= self.available_at:
                raise ValueError("lease expiry must be after availability")


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    id: UUID
    organization_id: UUID
    aggregate_type: str
    aggregate_id: UUID
    aggregate_sequence: int
    event_type: str
    schema_version: int
    payload: Mapping[str, JsonValue]
    created_at: datetime
    project_id: UUID | None = None
    publication_attempts: int = 0
    published_at: datetime | None = None

    def __post_init__(self) -> None:
        _uuid(self.id, "id")
        _tenant_ids(self.organization_id, self.project_id)
        _uuid(self.aggregate_id, "aggregate_id")
        _positive(self.aggregate_sequence, "aggregate_sequence")
        _positive(self.schema_version, "schema_version")
        _nonnegative(self.publication_attempts, "publication_attempts")
        for name in ("aggregate_type", "event_type"):
            _nonempty(getattr(self, name), name)
        _utc(self.created_at, "created_at")
        _utc(self.published_at, "published_at")
        object.__setattr__(self, "payload", freeze_public_json(self.payload))


@dataclass(frozen=True, slots=True)
class ObjectDescriptor:
    object_id: str
    size_bytes: int
    sha256: str
    media_type: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        _opaque(self.object_id, "object_id")
        _nonnegative(self.size_bytes, "size_bytes")
        _sha256(self.sha256, "sha256")
        _nonempty(self.media_type, "media_type")
        _positive(self.schema_version, "schema_version")


@dataclass(frozen=True, slots=True)
class TemporaryObjectDescriptor:
    upload_id: UUID
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        _uuid(self.upload_id, "upload_id")
        _nonnegative(self.size_bytes, "size_bytes")
        _sha256(self.sha256, "sha256")


@dataclass(frozen=True, slots=True)
class DownloadDescriptor:
    object: ObjectDescriptor
    filename: str
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        _nonempty(self.filename, "filename")
        if PurePosixPath(self.filename).is_absolute() or PureWindowsPath(self.filename).is_absolute():
            raise ValueError("filename must not be an absolute private path")
        _utc(self.expires_at, "expires_at")


@dataclass(frozen=True, slots=True)
class Artifact:
    id: UUID
    organization_id: UUID
    project_id: UUID
    job_id: UUID
    logical_name: str
    object: ObjectDescriptor
    status: str
    created_at: datetime

    def __post_init__(self) -> None:
        _uuid(self.id, "id")
        _tenant_ids(self.organization_id, self.project_id)
        _uuid(self.job_id, "job_id")
        _nonempty(self.logical_name, "logical_name")
        _nonempty(self.status, "status")
        _utc(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class LegacyRegistration:
    id: UUID
    organization_id: UUID
    project_id: UUID
    legacy_type: str
    opaque_locator: str
    manifest_sha256: str
    status: str
    version: int
    created_at: datetime

    def __post_init__(self) -> None:
        _uuid(self.id, "id")
        _tenant_ids(self.organization_id, self.project_id)
        _nonempty(self.legacy_type, "legacy_type")
        _opaque(self.opaque_locator, "opaque_locator")
        _sha256(self.manifest_sha256, "manifest_sha256")
        if self.status != "read_only":
            raise ValueError("legacy registrations must remain read_only")
        _positive(self.version, "version")
        _utc(self.created_at, "created_at")

    @property
    def read_only(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class AuditEvent:
    id: UUID
    actor_id: UUID
    organization_id: UUID
    action: Action
    resource_type: str
    resource_id: UUID
    outcome: str
    request_id: str
    created_at: datetime
    project_id: UUID | None = None
    identity_id: UUID | None = None
    command_id: UUID | None = None
    safe_metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("id", "actor_id", "resource_id"):
            _uuid(getattr(self, name), name)
        _tenant_ids(self.organization_id, self.project_id)
        for name in ("identity_id", "command_id"):
            value = getattr(self, name)
            if value is not None:
                _uuid(value, name)
        for name in ("resource_type", "outcome", "request_id"):
            _nonempty(getattr(self, name), name)
        _utc(self.created_at, "created_at")
        object.__setattr__(self, "safe_metadata", freeze_public_json(self.safe_metadata))


@dataclass(frozen=True, slots=True)
class AuthenticatedIdentity:
    issuer: str
    subject: str
    claims: Mapping[str, JsonValue]
    expires_at: datetime

    def __post_init__(self) -> None:
        _nonempty(self.issuer, "issuer")
        _nonempty(self.subject, "subject")
        _utc(self.expires_at, "expires_at")
        object.__setattr__(self, "claims", freeze_identity_claims(self.claims))


@dataclass(frozen=True, slots=True)
class StageInputManifest:
    revision: int
    objects: tuple[ObjectDescriptor, ...]

    def __post_init__(self) -> None:
        _nonnegative(self.revision, "revision")
        object.__setattr__(self, "objects", tuple(self.objects))


@dataclass(frozen=True, slots=True)
class StageOutputSpec:
    logical_names: tuple[str, ...]
    maximum_size_bytes: int

    def __post_init__(self) -> None:
        if not self.logical_names or any(not name.strip() for name in self.logical_names):
            raise ValueError("logical_names must contain declared relative outputs")
        for name in self.logical_names:
            if PurePosixPath(name).is_absolute() or PureWindowsPath(name).is_absolute() or ".." in PurePosixPath(name).parts:
                raise ValueError("logical_names must contain declared relative outputs")
        _positive(self.maximum_size_bytes, "maximum_size_bytes")
        object.__setattr__(self, "logical_names", tuple(self.logical_names))


@dataclass(frozen=True, slots=True)
class IdempotencyResult:
    decision: IdempotencyDecision
    response_status: int | None = None
    response_body: JsonValue = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "response_body", freeze_public_json(self.response_body))


def public_model_field_names() -> frozenset[str]:
    """Return names forbidden from all persisted/public records for boundary tests."""
    return frozenset({"raw_token", "access_token", "refresh_token", "provider_response", "absolute_path"})

"""SQLAlchemy 2 PostgreSQL unit of work and tenant-scoped repositories."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, and_, false
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from peerassist.platform.errors import (
    DependencyUnavailable,
    NotFound,
    StaleVersion,
)
from peerassist.platform.models import (
    Action,
    Artifact,
    AuditEvent,
    BrowserSession,
    CommandRecord,
    ExternalIdentity,
    LegacyRegistration,
    ObjectDescriptor,
    OidcTransaction,
    Organization,
    OrganizationMembership,
    OutboxEvent,
    Paper,
    PaperVersion,
    Project,
    ProjectMembership,
    ReviewEvent,
    ReviewJob,
    Role,
    TenantScope,
    User,
    WorkItem,
    mutable_json,
)

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _project_filter(table: Any, scope: TenantScope) -> Any:
    if scope.project_id is None:
        return false()
    project_id = table.c.project_id if "project_id" in table.c else table.c.id
    return and_(
        table.c.organization_id == scope.organization_id,
        project_id == scope.project_id,
    )


def _record_filter(table: Any, scope: TenantScope) -> Any:
    project = table.c.project_id
    return and_(
        table.c.organization_id == scope.organization_id,
        project.is_(None) if scope.project_id is None else project == scope.project_id,
    )


def _require_project(scope: TenantScope, value: object) -> None:
    project_id = getattr(value, "project_id", getattr(value, "id", None))
    if scope.project_id is None or (
        getattr(value, "organization_id", None), project_id
    ) != (scope.organization_id, scope.project_id):
        raise NotFound()


def _require_record(scope: TenantScope, value: object) -> None:
    if (getattr(value, "organization_id", None), getattr(value, "project_id", None)) != (
        scope.organization_id,
        scope.project_id,
    ):
        raise NotFound()


def _require_initial(value: object) -> None:
    if getattr(value, "version", None) != 1:
        raise StaleVersion(details={"expected_version": 1, "current_version": None})


def _require_next(replacement: object, expected_version: int | None) -> None:
    required = 1 if expected_version is None else expected_version + 1
    actual = getattr(replacement, "version", None)
    if actual != required:
        raise StaleVersion(details={"expected_version": required, "current_version": actual})


def _json(value: object) -> object:
    return mutable_json(value)  # type: ignore[arg-type]


def _user(row: Mapping[str, Any]) -> User:
    return User(row["id"], row["status"], row["display_name"], row["created_at"], row["updated_at"], row["version"])


def _identity(row: Mapping[str, Any]) -> ExternalIdentity:
    return ExternalIdentity(
        row["id"], row["user_id"], row["issuer"], row["subject"], row["verified_claims"],
        row["last_seen_at"], row["created_at"], row["disabled_at"], row["version"],
    )


def _organization(row: Mapping[str, Any]) -> Organization:
    return Organization(
        row["id"], row["slug"], row["name"], row["status"], row["version"],
        row["created_at"], row["updated_at"],
    )


def _organization_membership(row: Mapping[str, Any]) -> OrganizationMembership:
    return OrganizationMembership(
        row["id"], row["organization_id"], row["user_id"], Role(row["role"]), row["status"],
        row["version"], row["created_at"], row["updated_at"], row["revoked_at"],
    )


def _project(row: Mapping[str, Any]) -> Project:
    return Project(
        row["id"], row["organization_id"], row["name"], row["status"], row["version"],
        row["created_at"], row["updated_at"],
    )


def _project_membership(row: Mapping[str, Any]) -> ProjectMembership:
    return ProjectMembership(
        row["id"], row["organization_id"], row["project_id"], row["user_id"], Role(row["role"]),
        row["status"], row["version"], row["created_at"], row["updated_at"], row["revoked_at"],
    )


def _paper(row: Mapping[str, Any]) -> Paper:
    return Paper(
        row["id"], row["organization_id"], row["project_id"], row["content_sha256"],
        row["current_version_id"], row["status"], row["version"], row["created_at"], row["updated_at"],
    )


def _paper_version(row: Mapping[str, Any]) -> PaperVersion:
    return PaperVersion(
        row["id"], row["organization_id"], row["project_id"], row["paper_id"],
        row["source_object_id"], row["filename"], row["media_type"], row["size_bytes"], row["sha256"],
        row["created_by"], row["created_at"], row["revision"],
    )


def _review_job(row: Mapping[str, Any]) -> ReviewJob:
    return ReviewJob(
        row["id"], row["organization_id"], row["project_id"], row["paper_version_id"], row["mode"],
        row["stage"], row["status"], row["version"], row["attempt"], row["created_by"],
        row["created_at"], row["updated_at"], row["cancelled_at"], row["safe_error_code"],
    )


def _review_event(row: Mapping[str, Any]) -> ReviewEvent:
    return ReviewEvent(
        row["id"], row["organization_id"], row["project_id"], row["job_id"],
        row["aggregate_sequence"], row["event_type"], row["schema_version"], row["payload"], row["created_at"],
    )


def _command(row: Mapping[str, Any]) -> CommandRecord:
    return CommandRecord(
        row["id"], row["organization_id"], row["actor_id"], row["operation"], row["idempotency_key"],
        row["payload_digest"], row["created_at"], row["project_id"], row["response_status"],
        row["response_body"], row["completed_at"],
    )


def _work_item(row: Mapping[str, Any]) -> WorkItem:
    return WorkItem(
        row["id"], row["organization_id"], row["project_id"], row["job_id"], row["attempt_id"],
        row["stage"], row["input_revision"], row["attempt_count"], row["max_attempts"],
        row["available_at"], row["created_at"], row["lease_owner"], row["lease_expires_at"],
        row["dead_lettered_at"], row["safe_error_code"],
    )


def _outbox(row: Mapping[str, Any]) -> OutboxEvent:
    return OutboxEvent(
        row["id"], row["organization_id"], row["aggregate_type"], row["aggregate_id"],
        row["aggregate_sequence"], row["event_type"], row["schema_version"], row["payload"],
        row["created_at"], row["project_id"], row["publication_attempts"], row["published_at"],
    )


def _artifact(row: Mapping[str, Any]) -> Artifact:
    descriptor = ObjectDescriptor(
        row["object_id"], row["size_bytes"], row["sha256"], row["media_type"], row["schema_version"]
    )
    return Artifact(
        row["id"], row["organization_id"], row["project_id"], row["job_id"], row["logical_name"],
        descriptor, row["status"], row["created_at"],
    )


def _legacy(row: Mapping[str, Any]) -> LegacyRegistration:
    return LegacyRegistration(
        row["id"], row["organization_id"], row["project_id"], row["legacy_type"],
        row["opaque_locator"], row["manifest_digest"], row["status"], row["schema_version"], row["created_at"],
    )


def _audit(row: Mapping[str, Any]) -> AuditEvent:
    return AuditEvent(
        row["id"], row["actor_id"], row["organization_id"], Action(row["action"]), row["resource_type"],
        UUID(row["resource_id"]), row["outcome"], row["request_id"], row["created_at"], row["project_id"],
        row["identity_id"], row["command_id"], row["metadata"],
    )


def _session(row: Mapping[str, Any]) -> BrowserSession:
    return BrowserSession(
        row["id"], row["user_id"], row["identity_id"], row["session_digest"], row["csrf_digest"],
        row["provider_credential_ref"], row["expires_at"], row["idle_expires_at"], row["created_at"], row["revoked_at"],
    )


def _oidc_transaction(row: Mapping[str, Any]) -> OidcTransaction:
    return OidcTransaction(
        row["id"], row["state_digest"], row["nonce_digest"], bytes(row["encrypted_pkce_verifier"]),
        row["encryption_key_id"], row["return_path"], row["expires_at"], row["created_at"], row["consumed_at"],
    )


class _Repository:
    def __init__(self, uow: Any) -> None:
        self._uow = uow
        self._preserve_integrity = False

    @property
    def connection(self) -> _SafeConnection:
        return _SafeConnection(
            self._uow._connection_for_repository(),
            preserve_integrity=self._preserve_integrity,
        )

    def _one(self, statement: Any) -> Mapping[str, Any] | None:
        return self.connection.execute(statement).mappings().one_or_none()

    def _integrity(self, operation: Callable[[], Any], message: str) -> Any:
        connection = self._uow._connection_for_repository()
        try:
            with connection.begin_nested():
                self._preserve_integrity = True
                try:
                    return operation()
                finally:
                    self._preserve_integrity = False
        except IntegrityError:
            raise ValueError(message) from None
        except SQLAlchemyError as error:
            raise DependencyUnavailable(cause=error) from None


class _SafeConnection:
    """Expose only normalized SQL execution to repositories."""

    def __init__(self, connection: Connection, *, preserve_integrity: bool) -> None:
        self._connection = connection
        self._preserve_integrity = preserve_integrity

    def execute(self, statement: Any) -> Any:
        try:
            return self._connection.execute(statement)
        except IntegrityError:
            if self._preserve_integrity:
                raise
            raise DependencyUnavailable() from None
        except SQLAlchemyError as error:
            raise DependencyUnavailable(cause=error) from None

    def scalar(self, statement: Any) -> Any:
        try:
            return self._connection.scalar(statement)
        except SQLAlchemyError as error:
            raise DependencyUnavailable(cause=error) from None

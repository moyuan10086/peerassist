"""PostgreSQL schema primitives shared by the authoritative Alembic revision."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Computed,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.engine import Connection, Engine

from .postgres_v0002_signature import (
    CATALOG_INSPECTION_SQL,
    EXPECTED_CATALOG_FINGERPRINT,
    EXPECTED_CATALOG_SIGNATURE,
    OWNED_TABLES,
    catalog_fingerprint,
    catalog_signature_from_row,
)

HEAD_REVISION = "0002_review_workspace"

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
metadata = MetaData(naming_convention=NAMING_CONVENTION)

UUID_TYPE = UUID(as_uuid=True)
UTC_TIMESTAMP = TIMESTAMP(timezone=True)
NIL_UUID_SQL = "'00000000-0000-0000-0000-000000000000'::uuid"


def _uuid(
    name: str,
    *,
    nullable: bool = False,
    primary_key: bool = False,
) -> Column[Any]:
    return Column(name, UUID_TYPE, nullable=nullable, primary_key=primary_key)


def _timestamp(name: str, *, nullable: bool = False) -> Column[Any]:
    return Column(name, UTC_TIMESTAMP, nullable=nullable)


def _json(name: str, *, nullable: bool = False) -> Column[Any]:
    return Column(name, JSONB, nullable=nullable)


def _check(expression: str, name: str) -> CheckConstraint:
    return CheckConstraint(expression, name=name)


def _values(column: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    allowed = ", ".join(f"'{value}'" for value in values)
    return _check(f"{column} IN ({allowed})", name)


schema_metadata = Table(
    "schema_metadata",
    metadata,
    Column("key", String(100), primary_key=True),
    Column("value", String(255), nullable=False),
    _timestamp("updated_at"),
)

users = Table(
    "users",
    metadata,
    _uuid("id", primary_key=True),
    Column("status", String(32), nullable=False),
    Column("display_name", String(255), nullable=False),
    Column("version", Integer, nullable=False),
    _timestamp("created_at"),
    _timestamp("updated_at"),
    _check("version > 0", "ck_users_version_positive"),
    _values("status", ("active", "disabled"), "ck_users_status"),
)

external_identities = Table(
    "external_identities",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("user_id"),
    Column("issuer", String(2048), nullable=False),
    Column("subject", String(512), nullable=False),
    _json("verified_claims"),
    _timestamp("last_seen_at"),
    _timestamp("created_at"),
    _timestamp("disabled_at", nullable=True),
    Column("version", Integer, nullable=False),
    ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_external_identities_user"),
    UniqueConstraint("issuer", "subject", name="uq_external_identities_issuer_subject"),
    UniqueConstraint("user_id", "id", name="uq_external_identities_user_id"),
    _check("version > 0", "ck_external_identities_version_positive"),
)

browser_sessions = Table(
    "browser_sessions",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("user_id"),
    _uuid("identity_id"),
    Column("session_digest", String(64), nullable=False),
    Column("csrf_digest", String(64), nullable=False),
    Column("provider_credential_ref", Text, nullable=False),
    _timestamp("token_expires_at"),
    _timestamp("expires_at"),
    _timestamp("idle_expires_at"),
    _timestamp("created_at"),
    _timestamp("updated_at"),
    _timestamp("revoked_at", nullable=True),
    ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_browser_sessions_user"),
    ForeignKeyConstraint(
        ["user_id", "identity_id"],
        ["external_identities.user_id", "external_identities.id"],
        name="fk_browser_sessions_user_identity",
    ),
    UniqueConstraint("session_digest", name="uq_browser_sessions_digest"),
    _check("idle_expires_at <= expires_at", "ck_browser_sessions_idle_expiry"),
)
Index("ix_browser_sessions_expiry", browser_sessions.c.expires_at, browser_sessions.c.revoked_at)

oidc_transactions = Table(
    "oidc_transactions",
    metadata,
    _uuid("id", primary_key=True),
    Column("state_digest", String(64), nullable=False),
    Column("nonce_digest", String(64), nullable=False),
    Column("encrypted_pkce_verifier", LargeBinary, nullable=False),
    Column("encryption_key_id", String(64), nullable=False),
    Column("return_path", Text, nullable=False),
    _timestamp("expires_at"),
    _timestamp("created_at"),
    _timestamp("consumed_at", nullable=True),
    UniqueConstraint("nonce_digest", name="uq_oidc_transactions_nonce_digest"),
    UniqueConstraint("state_digest", name="uq_oidc_transactions_state_digest"),
    _check("char_length(return_path) > 0", "ck_oidc_transactions_return_path"),
)
Index("ix_oidc_transactions_expiry", oidc_transactions.c.expires_at)

organizations = Table(
    "organizations",
    metadata,
    _uuid("id", primary_key=True),
    Column("slug", String(100), nullable=False),
    Column("name", String(255), nullable=False),
    Column("status", String(32), nullable=False),
    Column("version", Integer, nullable=False),
    _timestamp("created_at"),
    _timestamp("updated_at"),
    UniqueConstraint("slug", name="uq_organizations_slug"),
    _check("version > 0", "ck_organizations_version_positive"),
    _values("status", ("active", "disabled", "tombstoned"), "ck_organizations_status"),
)

organization_memberships = Table(
    "organization_memberships",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("organization_id"),
    _uuid("user_id"),
    Column("role", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    Column("version", Integer, nullable=False),
    _timestamp("created_at"),
    _timestamp("updated_at"),
    _timestamp("revoked_at", nullable=True),
    ForeignKeyConstraint(
        ["organization_id"], ["organizations.id"], name="fk_organization_memberships_organization"
    ),
    ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_organization_memberships_user"),
    UniqueConstraint(
        "organization_id", "user_id", name="uq_organization_memberships_organization_user"
    ),
    _values("role", ("organization_admin",), "ck_organization_memberships_role"),
    _values("status", ("active", "revoked"), "ck_organization_memberships_status"),
    _check("version > 0", "ck_organization_memberships_version_positive"),
)

projects = Table(
    "projects",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("organization_id"),
    Column("name", String(255), nullable=False),
    Column("status", String(32), nullable=False),
    Column("version", Integer, nullable=False),
    _timestamp("created_at"),
    _timestamp("updated_at"),
    ForeignKeyConstraint(["organization_id"], ["organizations.id"], name="fk_projects_organization"),
    UniqueConstraint("organization_id", "id", name="uq_projects_tenant_id"),
    _check("version > 0", "ck_projects_version_positive"),
    _values("status", ("active", "disabled", "tombstoned"), "ck_projects_status"),
)
Index("ix_projects_organization", projects.c.organization_id)

project_memberships = Table(
    "project_memberships",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("organization_id"),
    _uuid("project_id"),
    _uuid("user_id"),
    Column("role", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    Column("version", Integer, nullable=False),
    _timestamp("created_at"),
    _timestamp("updated_at"),
    _timestamp("revoked_at", nullable=True),
    Column("is_default", Boolean, nullable=False, server_default=text("false")),
    ForeignKeyConstraint(
        ["organization_id", "project_id"],
        ["projects.organization_id", "projects.id"],
        name="fk_project_memberships_project_tenant",
    ),
    ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_project_memberships_user"),
    UniqueConstraint("project_id", "user_id", name="uq_project_memberships_project_user"),
    _values(
        "role", ("project_owner", "reviewer", "viewer"), "ck_project_memberships_role"
    ),
    _values("status", ("active", "revoked"), "ck_project_memberships_status"),
    _check("version > 0", "ck_project_memberships_version_positive"),
)
Index("ix_project_memberships_tenant_user", project_memberships.c.organization_id, project_memberships.c.user_id)
Index(
    "uq_project_memberships_default_user_organization",
    project_memberships.c.organization_id,
    project_memberships.c.user_id,
    unique=True,
    postgresql_where=text("is_default AND status = 'active' AND revoked_at IS NULL"),
)

papers = Table(
    "papers",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("organization_id"),
    _uuid("project_id"),
    Column("content_sha256", String(64), nullable=False),
    _uuid("current_version_id", nullable=True),
    Column("status", String(32), nullable=False),
    Column("version", Integer, nullable=False),
    _timestamp("created_at"),
    _timestamp("updated_at"),
    ForeignKeyConstraint(
        ["organization_id", "project_id"],
        ["projects.organization_id", "projects.id"],
        name="fk_papers_project_tenant",
    ),
    UniqueConstraint("organization_id", "project_id", "id", name="uq_papers_tenant_id"),
    UniqueConstraint("project_id", "content_sha256", name="uq_papers_project_digest"),
    _check("version > 0", "ck_papers_version_positive"),
    _values("status", ("active", "tombstoned"), "ck_papers_status"),
)
Index("ix_papers_tenant_project", papers.c.organization_id, papers.c.project_id)

paper_versions = Table(
    "paper_versions",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("organization_id"),
    _uuid("project_id"),
    _uuid("paper_id"),
    Column("source_object_id", Text, nullable=False),
    Column("filename", Text, nullable=False),
    Column("media_type", String(255), nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column("sha256", String(64), nullable=False),
    _uuid("created_by"),
    Column("revision", Integer, nullable=False),
    _timestamp("created_at"),
    ForeignKeyConstraint(
        ["organization_id", "project_id", "paper_id"],
        ["papers.organization_id", "papers.project_id", "papers.id"],
        name="fk_paper_versions_paper_tenant",
    ),
    ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_paper_versions_created_by"),
    UniqueConstraint("organization_id", "project_id", "id", name="uq_paper_versions_tenant_id"),
    UniqueConstraint(
        "organization_id",
        "project_id",
        "paper_id",
        "id",
        name="uq_paper_versions_paper_id",
    ),
    UniqueConstraint("paper_id", "revision", name="uq_paper_versions_paper_revision"),
    UniqueConstraint("source_object_id", name="uq_paper_versions_source_object"),
    _check("revision > 0", "ck_paper_versions_revision_positive"),
    _check("size_bytes >= 0", "ck_paper_versions_size_nonnegative"),
    _check("char_length(sha256) = 64", "ck_paper_versions_sha256"),
)
papers.append_constraint(
    ForeignKeyConstraint(
        [
            papers.c.organization_id,
            papers.c.project_id,
            papers.c.id,
            papers.c.current_version_id,
        ],
        [
            paper_versions.c.organization_id,
            paper_versions.c.project_id,
            paper_versions.c.paper_id,
            paper_versions.c.id,
        ],
        name="fk_papers_current_version_tenant_paper",
        use_alter=True,
    )
)

review_jobs = Table(
    "review_jobs",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("organization_id"),
    _uuid("project_id"),
    _uuid("paper_version_id"),
    Column("mode", String(32), nullable=False),
    Column("stage", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("version", Integer, nullable=False),
    Column("attempt", Integer, nullable=False),
    _uuid("created_by"),
    _timestamp("created_at"),
    _timestamp("updated_at"),
    _timestamp("cancelled_at", nullable=True),
    Column("safe_error_code", String(128), nullable=True),
    ForeignKeyConstraint(
        ["organization_id", "project_id", "paper_version_id"],
        [
            "paper_versions.organization_id",
            "paper_versions.project_id",
            "paper_versions.id",
        ],
        name="fk_review_jobs_paper_version_tenant",
    ),
    ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_review_jobs_created_by"),
    UniqueConstraint("organization_id", "project_id", "id", name="uq_review_jobs_tenant_id"),
    _values("mode", ("fast", "full"), "ck_review_jobs_mode"),
    _values(
        "stage",
        (
            "queued",
            "prepare",
            "parse",
            "positioning",
            "citation_audit",
            "execution",
            "review",
            "report",
            "finalize",
            "completed",
        ),
        "ck_review_jobs_stage",
    ),
    _values(
        "status",
        ("queued", "running", "blocked", "cancelling", "cancelled", "failed", "completed"),
        "ck_review_jobs_status",
    ),
    _check("version > 0", "ck_review_jobs_version_positive"),
    _check("attempt >= 0", "ck_review_jobs_attempt_nonnegative"),
)
Index("ix_review_jobs_tenant_status", review_jobs.c.organization_id, review_jobs.c.project_id, review_jobs.c.status)

review_attempts = Table(
    "review_attempts",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("organization_id"),
    _uuid("project_id"),
    _uuid("job_id"),
    Column("attempt_number", Integer, nullable=False),
    Column("status", String(32), nullable=False),
    _timestamp("started_at", nullable=True),
    _timestamp("finished_at", nullable=True),
    _timestamp("created_at"),
    ForeignKeyConstraint(
        ["organization_id", "project_id", "job_id"],
        ["review_jobs.organization_id", "review_jobs.project_id", "review_jobs.id"],
        name="fk_review_attempts_job_tenant",
    ),
    UniqueConstraint(
        "organization_id", "project_id", "job_id", "id", name="uq_review_attempts_tenant_id"
    ),
    UniqueConstraint("job_id", "attempt_number", name="uq_review_attempts_job_number"),
    _check("attempt_number > 0", "ck_review_attempts_number_positive"),
    _values(
        "status", ("queued", "running", "blocked", "cancelled", "failed", "completed"), "ck_review_attempts_status"
    ),
)

review_events = Table(
    "review_events",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("organization_id"),
    _uuid("project_id"),
    _uuid("job_id"),
    Column("aggregate_sequence", BigInteger, nullable=False),
    Column("event_type", String(128), nullable=False),
    Column("schema_version", Integer, nullable=False),
    _json("payload"),
    _timestamp("created_at"),
    ForeignKeyConstraint(
        ["organization_id", "project_id", "job_id"],
        ["review_jobs.organization_id", "review_jobs.project_id", "review_jobs.id"],
        name="fk_review_events_job_tenant",
    ),
    UniqueConstraint("job_id", "aggregate_sequence", name="uq_review_events_job_sequence"),
    UniqueConstraint(
        "organization_id",
        "project_id",
        "job_id",
        "id",
        name="uq_review_events_tenant_id",
    ),
    _check("aggregate_sequence > 0", "ck_review_events_sequence_positive"),
    _check("schema_version > 0", "ck_review_events_schema_version_positive"),
)
Index("ix_review_events_tenant_cursor", review_events.c.organization_id, review_events.c.project_id, review_events.c.created_at, review_events.c.id)

external_service_consents = Table(
    "external_service_consents",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("organization_id"),
    _uuid("project_id"),
    _uuid("review_job_id"),
    _uuid("paper_version_id"),
    Column("service", String(128), nullable=False),
    Column("provider_config_revision", Integer, nullable=False),
    Column("policy_version", String(128), nullable=False),
    _json("data_scope"),
    Column("status", String(32), nullable=False),
    Column("generation", Integer, nullable=False),
    Column("version", Integer, nullable=False),
    _uuid("decided_by", nullable=True),
    _timestamp("decided_at", nullable=True),
    _timestamp("expires_at", nullable=True),
    _timestamp("superseded_at", nullable=True),
    _timestamp("created_at"),
    _timestamp("updated_at"),
    ForeignKeyConstraint(
        ["organization_id", "project_id", "review_job_id"],
        ["review_jobs.organization_id", "review_jobs.project_id", "review_jobs.id"],
        name="fk_external_service_consents_job_tenant",
    ),
    ForeignKeyConstraint(
        ["organization_id", "project_id", "paper_version_id"],
        ["paper_versions.organization_id", "paper_versions.project_id", "paper_versions.id"],
        name="fk_external_service_consents_paper_version_tenant",
    ),
    ForeignKeyConstraint(
        ["decided_by"], ["users.id"], name="fk_external_service_consents_decided_by"
    ),
    UniqueConstraint(
        "review_job_id",
        "paper_version_id",
        "service",
        "generation",
        name="uq_external_service_consents_generation",
    ),
    _check(
        "provider_config_revision > 0 AND generation > 0 AND version > 0",
        "ck_external_service_consents_versions_positive",
    ),
    _values("status", tuple(sorted(("pending", "granted", "denied", "revoked", "expired", "not_required"))), "ck_external_service_consents_status"),
    _check(
        "(decided_by IS NULL) = (decided_at IS NULL)",
        "ck_external_service_consents_decision_pair",
    ),
)
Index(
    "uq_external_service_consents_current",
    external_service_consents.c.review_job_id,
    external_service_consents.c.paper_version_id,
    external_service_consents.c.service,
    unique=True,
    postgresql_where=text("superseded_at IS NULL"),
)
Index(
    "ix_external_service_consents_tenant_job",
    external_service_consents.c.organization_id,
    external_service_consents.c.project_id,
    external_service_consents.c.review_job_id,
)

review_documents = Table(
    "review_documents",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("organization_id"),
    _uuid("project_id"),
    _uuid("review_job_id"),
    _json("blocks"),
    Column("document_version", Integer, nullable=False),
    _uuid("base_decision_event_id", nullable=True),
    _uuid("last_edited_by"),
    _timestamp("created_at"),
    _timestamp("updated_at"),
    ForeignKeyConstraint(
        ["organization_id", "project_id", "review_job_id"],
        ["review_jobs.organization_id", "review_jobs.project_id", "review_jobs.id"],
        name="fk_review_documents_job_tenant",
    ),
    ForeignKeyConstraint(
        ["organization_id", "project_id", "review_job_id", "base_decision_event_id"],
        [
            "review_events.organization_id",
            "review_events.project_id",
            "review_events.job_id",
            "review_events.id",
        ],
        name="fk_review_documents_base_event_tenant",
    ),
    ForeignKeyConstraint(
        ["last_edited_by"], ["users.id"], name="fk_review_documents_last_edited_by"
    ),
    UniqueConstraint("review_job_id", name="uq_review_documents_job"),
    _check("document_version > 0", "ck_review_documents_version_positive"),
)
Index(
    "ix_review_documents_tenant_job",
    review_documents.c.organization_id,
    review_documents.c.project_id,
    review_documents.c.review_job_id,
)

commands = Table(
    "commands",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("organization_id"),
    _uuid("project_id", nullable=True),
    Column(
        "scope_project_id",
        UUID_TYPE,
        Computed(f"COALESCE(project_id, {NIL_UUID_SQL})", persisted=True),
        nullable=False,
    ),
    _uuid("actor_id"),
    Column("operation", String(128), nullable=False),
    Column("idempotency_key", String(255), nullable=False),
    Column("payload_digest", String(64), nullable=False),
    Column("response_status", Integer, nullable=True),
    _json("response_body", nullable=True),
    _timestamp("created_at"),
    _timestamp("completed_at", nullable=True),
    ForeignKeyConstraint(["organization_id"], ["organizations.id"], name="fk_commands_organization"),
    ForeignKeyConstraint(
        ["organization_id", "project_id"],
        ["projects.organization_id", "projects.id"],
        name="fk_commands_project_tenant",
    ),
    UniqueConstraint(
        "organization_id", "actor_id", "operation", "idempotency_key", name="uq_commands_identity"
    ),
    UniqueConstraint("organization_id", "id", name="uq_commands_organization_id"),
    UniqueConstraint(
        "organization_id", "project_id", "id", name="uq_commands_project_id"
    ),
    UniqueConstraint(
        "organization_id", "scope_project_id", "id", name="uq_commands_scope_id"
    ),
    _check("char_length(payload_digest) = 64", "ck_commands_payload_digest"),
    _check(
        "(response_status IS NULL AND completed_at IS NULL) OR "
        "(response_status BETWEEN 100 AND 599 AND completed_at IS NOT NULL)",
        "ck_commands_completion_pair",
    ),
    _check(
        f"project_id IS NULL OR project_id <> {NIL_UUID_SQL}",
        "ck_commands_project_not_scope_sentinel",
    ),
)

work_items = Table(
    "work_items",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("organization_id"),
    _uuid("project_id"),
    _uuid("job_id"),
    _uuid("attempt_id"),
    Column("stage", String(64), nullable=False),
    Column("input_revision", Integer, nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("max_attempts", Integer, nullable=False),
    _timestamp("available_at"),
    Column("lease_owner", String(255), nullable=True),
    _timestamp("lease_expires_at", nullable=True),
    _timestamp("dead_lettered_at", nullable=True),
    Column("safe_error_code", String(128), nullable=True),
    _timestamp("created_at"),
    _timestamp("updated_at"),
    ForeignKeyConstraint(
        ["organization_id", "project_id", "job_id"],
        ["review_jobs.organization_id", "review_jobs.project_id", "review_jobs.id"],
        name="fk_work_items_job_tenant",
    ),
    ForeignKeyConstraint(
        ["organization_id", "project_id", "job_id", "attempt_id"],
        [
            "review_attempts.organization_id",
            "review_attempts.project_id",
            "review_attempts.job_id",
            "review_attempts.id",
        ],
        name="fk_work_items_attempt_tenant",
    ),
    UniqueConstraint("job_id", "attempt_id", "stage", "input_revision", name="uq_work_items_dedupe"),
    _check("input_revision >= 0", "ck_work_items_input_revision_nonnegative"),
    _check(
        "attempt_count >= 0 AND max_attempts > 0 AND attempt_count <= max_attempts",
        "ck_work_items_attempt_bounds",
    ),
    _check(
        "(lease_owner IS NULL) = (lease_expires_at IS NULL)",
        "ck_work_items_lease_pair",
    ),
)
Index("ix_work_items_claim", work_items.c.available_at, work_items.c.lease_expires_at, work_items.c.dead_lettered_at)

outbox_events = Table(
    "outbox_events",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("organization_id"),
    _uuid("project_id", nullable=True),
    Column("aggregate_type", String(128), nullable=False),
    _uuid("aggregate_id"),
    Column("aggregate_sequence", BigInteger, nullable=False),
    Column("event_type", String(128), nullable=False),
    Column("schema_version", Integer, nullable=False),
    _json("payload"),
    Column("publication_attempts", Integer, nullable=False),
    _timestamp("published_at", nullable=True),
    _timestamp("created_at"),
    ForeignKeyConstraint(["organization_id"], ["organizations.id"], name="fk_outbox_events_organization"),
    ForeignKeyConstraint(
        ["organization_id", "project_id"],
        ["projects.organization_id", "projects.id"],
        name="fk_outbox_events_project_tenant",
    ),
    UniqueConstraint("aggregate_type", "aggregate_id", "aggregate_sequence", name="uq_outbox_events_aggregate_sequence"),
    _check("aggregate_sequence > 0", "ck_outbox_events_sequence_positive"),
    _check("schema_version > 0", "ck_outbox_events_schema_version_positive"),
    _check("publication_attempts >= 0", "ck_outbox_events_attempts_nonnegative"),
)
Index("ix_outbox_events_unpublished", outbox_events.c.published_at, outbox_events.c.created_at)

stage_manifests = Table(
    "stage_manifests",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("organization_id"),
    _uuid("project_id"),
    _uuid("job_id"),
    _uuid("attempt_id"),
    Column("stage", String(64), nullable=False),
    Column("input_revision", Integer, nullable=False),
    Column("output_revision", Integer, nullable=False),
    Column("schema_version", Integer, nullable=False),
    _json("manifest"),
    Column("status", String(32), nullable=False),
    _timestamp("created_at"),
    _timestamp("committed_at", nullable=True),
    ForeignKeyConstraint(
        ["organization_id", "project_id", "job_id", "attempt_id"],
        [
            "review_attempts.organization_id",
            "review_attempts.project_id",
            "review_attempts.job_id",
            "review_attempts.id",
        ],
        name="fk_stage_manifests_attempt_tenant",
    ),
    UniqueConstraint(
        "job_id", "attempt_id", "stage", "input_revision", name="uq_stage_manifests_commit"
    ),
    UniqueConstraint(
        "organization_id",
        "project_id",
        "job_id",
        "id",
        name="uq_stage_manifests_tenant_id",
    ),
    _values("status", ("pending", "committed", "superseded"), "ck_stage_manifests_status"),
    _check(
        "input_revision >= 0 AND output_revision > 0 AND schema_version > 0",
        "ck_stage_manifests_versions",
    ),
)

report_versions = Table(
    "report_versions",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("organization_id"),
    _uuid("project_id"),
    _uuid("job_id"),
    Column("revision", Integer, nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("content_sha256", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    _uuid("created_by"),
    _timestamp("created_at"),
    _timestamp("published_at", nullable=True),
    _timestamp("superseded_at", nullable=True),
    ForeignKeyConstraint(
        ["organization_id", "project_id", "job_id"],
        ["review_jobs.organization_id", "review_jobs.project_id", "review_jobs.id"],
        name="fk_report_versions_job_tenant",
    ),
    ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_report_versions_created_by"),
    UniqueConstraint("job_id", "revision", name="uq_report_versions_job_revision"),
    UniqueConstraint(
        "organization_id", "project_id", "job_id", "id", name="uq_report_versions_tenant_id"
    ),
    _check("revision > 0 AND schema_version > 0", "ck_report_versions_versions_positive"),
    _values("status", ("draft", "published", "superseded"), "ck_report_versions_status"),
    _check("char_length(content_sha256) = 64", "ck_report_versions_digest"),
)

artifacts = Table(
    "artifacts",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("organization_id"),
    _uuid("project_id"),
    _uuid("job_id"),
    _uuid("stage_manifest_id", nullable=True),
    _uuid("report_version_id", nullable=True),
    Column("logical_name", String(255), nullable=False),
    Column("object_id", Text, nullable=False),
    Column("media_type", String(255), nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("status", String(32), nullable=False),
    _timestamp("created_at"),
    _timestamp("tombstoned_at", nullable=True),
    ForeignKeyConstraint(
        ["organization_id", "project_id", "job_id"],
        ["review_jobs.organization_id", "review_jobs.project_id", "review_jobs.id"],
        name="fk_artifacts_job_tenant",
    ),
    ForeignKeyConstraint(
        ["organization_id", "project_id", "job_id", "stage_manifest_id"],
        [
            "stage_manifests.organization_id",
            "stage_manifests.project_id",
            "stage_manifests.job_id",
            "stage_manifests.id",
        ],
        name="fk_artifacts_stage_manifest_tenant",
    ),
    ForeignKeyConstraint(
        ["organization_id", "project_id", "job_id", "report_version_id"],
        [
            "report_versions.organization_id",
            "report_versions.project_id",
            "report_versions.job_id",
            "report_versions.id",
        ],
        name="fk_artifacts_report_version_tenant",
    ),
    UniqueConstraint("job_id", "logical_name", name="uq_artifacts_job_logical_name"),
    UniqueConstraint("object_id", name="uq_artifacts_object_id"),
    _check("size_bytes >= 0", "ck_artifacts_size_nonnegative"),
    _check("schema_version > 0", "ck_artifacts_schema_version_positive"),
    _check("char_length(sha256) = 64", "ck_artifacts_sha256"),
    _values("status", ("available", "tombstoned"), "ck_artifacts_status"),
)
Index("ix_artifacts_tenant_job", artifacts.c.organization_id, artifacts.c.project_id, artifacts.c.job_id)

legacy_registrations = Table(
    "legacy_registrations",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("organization_id"),
    _uuid("project_id"),
    Column("legacy_type", String(64), nullable=False),
    Column("opaque_locator", Text, nullable=False),
    Column("manifest_digest", String(64), nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("status", String(32), nullable=False),
    _uuid("registered_by"),
    _timestamp("created_at"),
    _timestamp("updated_at"),
    ForeignKeyConstraint(
        ["organization_id", "project_id"],
        ["projects.organization_id", "projects.id"],
        name="fk_legacy_registrations_project_tenant",
    ),
    ForeignKeyConstraint(["registered_by"], ["users.id"], name="fk_legacy_registrations_registered_by"),
    UniqueConstraint("project_id", "opaque_locator", name="uq_legacy_registrations_project_locator"),
    _check("schema_version > 0", "ck_legacy_registrations_schema_version_positive"),
    _check("char_length(manifest_digest) = 64", "ck_legacy_registrations_digest"),
    _values("status", ("read_only", "imported", "tombstoned"), "ck_legacy_registrations_status"),
)

audit_events = Table(
    "audit_events",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("actor_id"),
    Column("actor_kind", String(32), nullable=False),
    _uuid("identity_id", nullable=True),
    _uuid("organization_id", nullable=True),
    _uuid("project_id", nullable=True),
    Column(
        "scope_project_id",
        UUID_TYPE,
        Computed(f"COALESCE(project_id, {NIL_UUID_SQL})", persisted=True),
        nullable=False,
    ),
    _uuid("command_id", nullable=True),
    Column("action", String(128), nullable=False),
    Column("resource_type", String(128), nullable=False),
    Column("resource_id", String(255), nullable=False),
    Column("outcome", String(32), nullable=False),
    Column("request_id", String(255), nullable=False),
    _json("metadata"),
    _timestamp("created_at"),
    ForeignKeyConstraint(
        ["identity_id"], ["external_identities.id"], name="fk_audit_events_identity"
    ),
    ForeignKeyConstraint(
        ["organization_id"], ["organizations.id"], name="fk_audit_events_organization"
    ),
    ForeignKeyConstraint(
        ["organization_id", "project_id"],
        ["projects.organization_id", "projects.id"],
        name="fk_audit_events_project_tenant",
    ),
    ForeignKeyConstraint(
        ["organization_id", "command_id"],
        ["commands.organization_id", "commands.id"],
        name="fk_audit_events_command_organization",
    ),
    ForeignKeyConstraint(
        ["organization_id", "project_id", "command_id"],
        ["commands.organization_id", "commands.project_id", "commands.id"],
        name="fk_audit_events_command_project",
    ),
    ForeignKeyConstraint(
        ["organization_id", "scope_project_id", "command_id"],
        ["commands.organization_id", "commands.scope_project_id", "commands.id"],
        name="fk_audit_events_command_scope",
    ),
    _values("actor_kind", ("user", "operator", "service"), "ck_audit_events_actor_kind"),
    _values("outcome", ("succeeded", "denied", "failed"), "ck_audit_events_outcome"),
    _check(
        "command_id IS NULL OR organization_id IS NOT NULL",
        "ck_audit_events_command_organization_required",
    ),
    _check(
        f"project_id IS NULL OR project_id <> {NIL_UUID_SQL}",
        "ck_audit_events_project_not_scope_sentinel",
    ),
)
Index("ix_audit_events_tenant_created", audit_events.c.organization_id, audit_events.c.project_id, audit_events.c.created_at, audit_events.c.id)
Index("ix_audit_events_command", audit_events.c.organization_id, audit_events.c.project_id, audit_events.c.command_id)

AUDIT_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION peerassist_reject_audit_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'audit_events are append-only' USING ERRCODE = '55000';
END;
$$
"""
AUDIT_TRIGGER_SQL = """
CREATE TRIGGER trg_audit_events_append_only
BEFORE UPDATE OR DELETE ON audit_events
FOR EACH ROW EXECUTE FUNCTION peerassist_reject_audit_mutation()
"""
AUDIT_PROTECTION_SQL = f"{AUDIT_FUNCTION_SQL}\n{AUDIT_TRIGGER_SQL}"

def create_platform_schema(connection: Connection) -> None:
    """Create only M1-owned tables; Alembic remains the migration authority."""

    metadata.create_all(connection, checkfirst=True)
    connection.execute(text(AUDIT_FUNCTION_SQL))
    connection.execute(text("DROP TRIGGER IF EXISTS trg_audit_events_append_only ON audit_events"))
    connection.execute(text(AUDIT_TRIGGER_SQL))
    connection.execute(
        text(
            "INSERT INTO schema_metadata (key, value, updated_at) "
            "VALUES ('platform_revision', :revision, now()), "
            "('platform_catalog_fingerprint', :fingerprint, now()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at"
        ),
        {"revision": HEAD_REVISION, "fingerprint": EXPECTED_CATALOG_FINGERPRINT},
    )


def drop_platform_schema(connection: Connection) -> None:
    """Drop only tables and database objects introduced by the M1 revision."""

    connection.execute(text("DROP TRIGGER IF EXISTS trg_audit_events_append_only ON audit_events"))
    connection.execute(text("DROP FUNCTION IF EXISTS peerassist_reject_audit_mutation()"))
    metadata.drop_all(connection, checkfirst=True)


@dataclass(frozen=True)
class PostgresUnitOfWorkFactory:
    """Validated engine boundary; repositories are added by the next M1 task."""

    engine: Engine

    @classmethod
    def from_url(cls, database_url: str) -> PostgresUnitOfWorkFactory:
        return cls(create_engine(database_url, pool_pre_ping=True))

    def __call__(self, actor: object) -> object:
        del actor
        raise RuntimeError("PostgreSQL repositories are not available in this schema checkpoint.")

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        self.engine.dispose()


@dataclass(frozen=True)
class PostgresSchemaReadiness:
    """Report ready only when the configured database is at the M1 Alembic head."""

    engine: Engine
    name: str = "database"

    async def check(self) -> bool:
        try:
            with self.engine.connect() as connection:
                result = connection.execute(
                    text(CATALOG_INSPECTION_SQL),
                    {"owned_tables": list(OWNED_TABLES)},
                ).mappings().one()
            actual_signature = catalog_signature_from_row(result)
            return (
                result["alembic_revision"] == HEAD_REVISION
                and result["schema_revision"] == HEAD_REVISION
                and result["stored_fingerprint"] == EXPECTED_CATALOG_FINGERPRINT
                and actual_signature == EXPECTED_CATALOG_SIGNATURE
                and catalog_fingerprint(actual_signature) == EXPECTED_CATALOG_FINGERPRINT
            )
        except Exception:
            return False

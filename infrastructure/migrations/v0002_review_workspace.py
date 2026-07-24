"""Frozen schema operations for review workspace revision 0002."""

from __future__ import annotations

import sqlalchemy as sa
from alembic.operations import Operations
from sqlalchemy.dialects import postgresql

REVISION = "0002_review_workspace"
V0001_CATALOG_FINGERPRINT = "9473563169d5a7d77afb227c64de954cf5f7bf8b471d239f3ceeccd982304e4d"
V0002_CATALOG_FINGERPRINT = "4821a463423baf155a91f096cdc57dbefb85ea7dc83bdf92fac615ef7aa12441"


def upgrade_v0002(op: Operations) -> None:
    op.add_column(
        "project_memberships",
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_index(
        "uq_project_memberships_default_user_organization",
        "project_memberships",
        ["organization_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("is_default AND status = 'active' AND revoked_at IS NULL"),
    )
    op.create_unique_constraint(
        "uq_review_events_tenant_id",
        "review_events",
        ["organization_id", "project_id", "job_id", "id"],
    )
    op.create_table(
        "external_service_consents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service", sa.String(length=128), nullable=False),
        sa.Column("provider_config_revision", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.String(length=128), nullable=False),
        sa.Column("data_scope", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("superseded_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(decided_by IS NULL) = (decided_at IS NULL)",
            name="ck_external_service_consents_decision_pair",
        ),
        sa.CheckConstraint(
            "status IN ('denied', 'expired', 'granted', 'not_required', 'pending', 'revoked')",
            name="ck_external_service_consents_status",
        ),
        sa.CheckConstraint(
            "provider_config_revision > 0 AND generation > 0 AND version > 0",
            name="ck_external_service_consents_versions_positive",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["users.id"], name="fk_external_service_consents_decided_by"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id", "review_job_id"],
            ["review_jobs.organization_id", "review_jobs.project_id", "review_jobs.id"],
            name="fk_external_service_consents_job_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id", "paper_version_id"],
            ["paper_versions.organization_id", "paper_versions.project_id", "paper_versions.id"],
            name="fk_external_service_consents_paper_version_tenant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_external_service_consents"),
        sa.UniqueConstraint(
            "review_job_id",
            "paper_version_id",
            "service",
            "generation",
            name="uq_external_service_consents_generation",
        ),
    )
    op.create_index(
        "ix_external_service_consents_tenant_job",
        "external_service_consents",
        ["organization_id", "project_id", "review_job_id"],
        unique=False,
    )
    op.create_index(
        "uq_external_service_consents_current",
        "external_service_consents",
        ["review_job_id", "paper_version_id", "service"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )
    op.create_table(
        "review_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("blocks", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("document_version", sa.Integer(), nullable=False),
        sa.Column("base_decision_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_edited_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "document_version > 0", name="ck_review_documents_version_positive"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id", "review_job_id", "base_decision_event_id"],
            [
                "review_events.organization_id",
                "review_events.project_id",
                "review_events.job_id",
                "review_events.id",
            ],
            name="fk_review_documents_base_event_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id", "review_job_id"],
            ["review_jobs.organization_id", "review_jobs.project_id", "review_jobs.id"],
            name="fk_review_documents_job_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["last_edited_by"], ["users.id"], name="fk_review_documents_last_edited_by"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_review_documents"),
        sa.UniqueConstraint("review_job_id", name="uq_review_documents_job"),
    )
    op.create_index(
        "ix_review_documents_tenant_job",
        "review_documents",
        ["organization_id", "project_id", "review_job_id"],
        unique=False,
    )
    _set_revision(op, REVISION, V0002_CATALOG_FINGERPRINT)


def downgrade_v0002(op: Operations) -> None:
    op.drop_index("ix_review_documents_tenant_job", table_name="review_documents")
    op.drop_table("review_documents")
    op.drop_index("uq_external_service_consents_current", table_name="external_service_consents")
    op.drop_index("ix_external_service_consents_tenant_job", table_name="external_service_consents")
    op.drop_table("external_service_consents")
    op.drop_constraint("uq_review_events_tenant_id", "review_events", type_="unique")
    op.drop_index(
        "uq_project_memberships_default_user_organization",
        table_name="project_memberships",
    )
    op.drop_column("project_memberships", "is_default")
    _set_revision(op, "0001_platform_m1", V0001_CATALOG_FINGERPRINT)


def _set_revision(op: Operations, revision: str, fingerprint: str) -> None:
    op.execute(
        sa.text(
            "UPDATE schema_metadata SET value = :revision, updated_at = now() "
            "WHERE key = 'platform_revision'"
        ).bindparams(revision=revision)
    )
    op.execute(
        sa.text(
            "UPDATE schema_metadata SET value = :fingerprint, updated_at = now() "
            "WHERE key = 'platform_catalog_fingerprint'"
        ).bindparams(fingerprint=fingerprint)
    )

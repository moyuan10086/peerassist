from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

EXPECTED_TABLES = {
    "schema_metadata",
    "users",
    "external_identities",
    "browser_sessions",
    "oidc_transactions",
    "organizations",
    "organization_memberships",
    "projects",
    "project_memberships",
    "papers",
    "paper_versions",
    "review_jobs",
    "review_attempts",
    "review_events",
    "commands",
    "work_items",
    "outbox_events",
    "stage_manifests",
    "report_versions",
    "artifacts",
    "legacy_registrations",
    "audit_events",
}


def test_platform_metadata_declares_the_approved_m1_schema() -> None:
    from peerassist.platform.adapters.postgres_schema import metadata

    assert set(metadata.tables) == EXPECTED_TABLES
    assert all(table.primary_key.name for table in metadata.tables.values())
    for table_name in ("users", "external_identities", "browser_sessions", "oidc_transactions"):
        assert "role" not in metadata.tables[table_name].c
        assert "roles" not in metadata.tables[table_name].c

    for table in metadata.tables.values():
        assert all(constraint.name for constraint in table.constraints)
        assert all(index.name for index in table.indexes)
        for column in table.columns:
            assert not column.name.endswith("role") or table.name.endswith("memberships")
            if column.name == "id" or any(foreign_key.parent is column for foreign_key in column.foreign_keys):
                assert isinstance(column.type, UUID)
            if column.name in {"payload", "metadata", "verified_claims", "response_body", "manifest"}:
                assert isinstance(column.type, JSONB)
            if column.name.endswith("_at"):
                assert isinstance(column.type, TIMESTAMP)
                assert column.type.timezone is True


def test_schema_names_tenant_constraints_and_required_deduplication_keys() -> None:
    from peerassist.platform.adapters.postgres_schema import metadata

    named = {
        constraint.name: constraint
        for table in metadata.tables.values()
        for constraint in table.constraints
    }
    indexes = {
        index.name: index for table in metadata.tables.values() for index in table.indexes
    }

    for name in {
        "uq_external_identities_issuer_subject",
        "uq_commands_identity",
        "uq_papers_project_digest",
        "uq_review_events_job_sequence",
        "uq_work_items_dedupe",
        "fk_projects_organization",
        "fk_project_memberships_project_tenant",
        "fk_papers_project_tenant",
        "fk_paper_versions_paper_tenant",
        "fk_review_jobs_paper_version_tenant",
        "fk_review_events_job_tenant",
        "fk_artifacts_job_tenant",
    }:
        assert name in named

    assert isinstance(named["uq_commands_identity"], UniqueConstraint)
    assert isinstance(named["fk_review_jobs_paper_version_tenant"], ForeignKeyConstraint)
    assert {column.name for column in named["uq_commands_identity"].columns} == {
        "organization_id",
        "actor_id",
        "operation",
        "idempotency_key",
    }
    assert {column.name for column in named["uq_work_items_dedupe"].columns} == {
        "job_id",
        "attempt_id",
        "stage",
        "input_revision",
    }
    assert {
        "ix_work_items_claim",
        "ix_outbox_events_unpublished",
        "ix_audit_events_tenant_created",
        "ix_browser_sessions_expiry",
        "ix_review_events_tenant_cursor",
    }.issubset(indexes)
    assert all(isinstance(index, Index) for index in indexes.values())


def test_artifact_stage_manifest_reference_preserves_tenant_and_job_scope() -> None:
    from peerassist.platform.adapters.postgres_schema import metadata

    stage_unique = _constraint(metadata, "uq_stage_manifests_tenant_id")
    artifact_stage = _constraint(metadata, "fk_artifacts_stage_manifest_tenant")

    assert _column_names(stage_unique) == {
        "organization_id",
        "project_id",
        "job_id",
        "id",
    }
    assert _column_names(artifact_stage) == {
        "organization_id",
        "project_id",
        "job_id",
        "stage_manifest_id",
    }
    assert metadata.tables["artifacts"].c.stage_manifest_id.nullable is True


def test_current_paper_version_reference_must_belong_to_the_same_paper() -> None:
    from peerassist.platform.adapters.postgres_schema import metadata

    paper_version_unique = _constraint(metadata, "uq_paper_versions_paper_id")
    current_version = _constraint(metadata, "fk_papers_current_version_tenant_paper")

    assert _column_names(paper_version_unique) == {
        "organization_id",
        "project_id",
        "paper_id",
        "id",
    }
    assert _column_names(current_version) == {
        "organization_id",
        "project_id",
        "id",
        "current_version_id",
    }
    assert metadata.tables["papers"].c.current_version_id.nullable is True


def test_audit_command_reference_preserves_organization_and_optional_project_scope() -> None:
    from peerassist.platform.adapters.postgres_schema import metadata

    audit = metadata.tables["audit_events"]
    assert audit.c.command_id.nullable is True
    assert _column_names(_constraint(metadata, "fk_audit_events_command_organization")) == {
        "organization_id",
        "command_id",
    }
    assert _column_names(_constraint(metadata, "fk_audit_events_command_project")) == {
        "organization_id",
        "project_id",
        "command_id",
    }
    assert "ix_audit_events_command" in {index.name for index in audit.indexes}


def test_audit_command_reference_uses_an_exact_nullable_project_scope_key() -> None:
    from peerassist.platform.adapters.postgres_schema import metadata

    commands = metadata.tables["commands"]
    audit = metadata.tables["audit_events"]
    assert commands.c.scope_project_id.computed is not None
    assert audit.c.scope_project_id.computed is not None
    assert commands.c.scope_project_id.nullable is False
    assert audit.c.scope_project_id.nullable is False
    assert _column_names(_constraint(metadata, "uq_commands_scope_id")) == {
        "organization_id",
        "scope_project_id",
        "id",
    }
    assert _column_names(_constraint(metadata, "fk_audit_events_command_scope")) == {
        "organization_id",
        "scope_project_id",
        "command_id",
    }


def test_browser_session_identity_reference_preserves_user_scope() -> None:
    from peerassist.platform.adapters.postgres_schema import metadata

    assert _column_names(_constraint(metadata, "uq_external_identities_user_id")) == {
        "user_id",
        "id",
    }
    assert _column_names(_constraint(metadata, "fk_browser_sessions_user_identity")) == {
        "user_id",
        "identity_id",
    }


def test_mutable_versions_statuses_and_enums_have_strict_checks() -> None:
    from peerassist.platform.adapters.postgres_schema import metadata

    checks = {
        constraint.name
        for table in metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert {
        "ck_users_version_positive",
        "ck_users_status",
        "ck_organizations_version_positive",
        "ck_organizations_status",
        "ck_organization_memberships_role",
        "ck_organization_memberships_status",
        "ck_projects_version_positive",
        "ck_projects_status",
        "ck_project_memberships_role",
        "ck_project_memberships_status",
        "ck_papers_status",
        "ck_paper_versions_revision_positive",
        "ck_review_jobs_mode",
        "ck_review_jobs_stage",
        "ck_review_jobs_status",
        "ck_review_attempts_status",
        "ck_work_items_attempt_bounds",
        "ck_work_items_lease_pair",
        "ck_stage_manifests_status",
        "ck_report_versions_status",
        "ck_artifacts_status",
        "ck_legacy_registrations_status",
        "ck_audit_events_outcome",
    }.issubset(checks)


def test_alembic_configuration_is_deterministic_and_revision_is_reversible() -> None:
    from peerassist.platform.adapters.postgres_schema import HEAD_REVISION

    root = Path(__file__).parents[2]
    config = (root / "alembic.ini").read_text(encoding="utf-8")
    environment = (root / "infrastructure/migrations/env.py").read_text(encoding="utf-8")
    revision_path = root / "infrastructure/migrations/versions/0001_platform_m1.py"
    revision = revision_path.read_text(
        encoding="utf-8"
    )

    assert "script_location = %(here)s/infrastructure/migrations" in config
    assert "sqlalchemy.url" not in config
    assert "PEERASSIST_TEST_DATABASE_URL" in environment
    assert "compare_type=True" in environment
    assert f'revision = "{HEAD_REVISION}"' in revision
    assert "def upgrade()" in revision
    assert "def downgrade()" in revision
    imports = {
        node.module
        for node in ast.walk(ast.parse(revision))
        if isinstance(node, ast.ImportFrom)
    }
    assert "peerassist.platform.adapters.postgres_schema" not in imports
    assert "infrastructure.migrations.v0001_schema" in imports
    assert "upgrade_v0001" in revision
    assert "downgrade_v0001" in revision
    assert "drop_all" not in revision


def test_frozen_v0001_schema_does_not_depend_on_live_application_metadata() -> None:
    root = Path(__file__).parents[2]
    snapshot = (root / "infrastructure/migrations/v0001_schema.py").read_text(encoding="utf-8")

    assert "peerassist.platform.adapters.postgres_schema" not in snapshot
    assert "checkfirst=False" in snapshot
    assert "REVISION_TABLES" in snapshot


def test_readiness_uses_the_frozen_semantic_catalog_signature() -> None:
    from peerassist.platform.adapters.postgres_v0001_signature import (
        CATALOG_INSPECTION_SQL,
        EXPECTED_CATALOG_FINGERPRINT,
        EXPECTED_CATALOG_SIGNATURE,
    )

    assert len(EXPECTED_CATALOG_SIGNATURE["tables"]) == 22
    assert set(EXPECTED_CATALOG_SIGNATURE) == {
        "tables",
        "columns",
        "constraints",
        "indexes",
        "triggers",
        "functions",
    }
    assert len(EXPECTED_CATALOG_SIGNATURE["functions"]) == 1
    assert EXPECTED_CATALOG_SIGNATURE["functions"][0].startswith(
        "peerassist_reject_audit_mutation||plpgsql|"
    )
    assert len(EXPECTED_CATALOG_FINGERPRINT) == 64
    normalized = " ".join(CATALOG_INSPECTION_SQL.lower().split())
    for expression in (
        "format_type",
        "pg_get_expr",
        "pg_get_constraintdef",
        "pg_get_indexdef",
        "pg_get_triggerdef",
        "pg_get_functiondef",
        "pg_get_function_identity_arguments",
        "attnotnull",
        "attidentity",
        "attgenerated",
    ):
        assert expression in normalized
    for catalog in ("pg_constraint", "pg_index", "pg_trigger", "pg_attribute", "pg_proc"):
        assert catalog in normalized
    assert "statement_timeout" in normalized

    root = Path(__file__).parents[2]
    readiness_source = (
        root / "src/peerassist/platform/adapters/postgres_schema.py"
    ).read_text(encoding="utf-8")
    frozen_source = (root / "infrastructure/migrations/v0001_schema.py").read_text(
        encoding="utf-8"
    )
    assert "postgres_v0001_signature" in readiness_source
    assert "EXPECTED_CATALOG_FINGERPRINT" in frozen_source
    assert "EXPECTED_SCHEMA_CONSTRAINTS.issubset" not in readiness_source


def test_audit_table_is_protected_from_update_and_delete() -> None:
    from peerassist.platform.adapters.postgres_schema import AUDIT_PROTECTION_SQL

    normalized = " ".join(AUDIT_PROTECTION_SQL.lower().split())
    assert "before update or delete on audit_events" in normalized
    assert "raise exception" in normalized
    assert "audit_events are append-only" in normalized


def test_migration_module_does_not_auto_run_when_imported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    monkeypatch.delenv("PEERASSIST_TEST_DATABASE_URL", raising=False)
    module = importlib.import_module("peerassist.platform.adapters.postgres_schema")

    assert module.HEAD_REVISION == "0001_platform_m1"


def _constraint(metadata: object, name: str) -> object:
    return next(
        constraint
        for table in metadata.tables.values()
        for constraint in table.constraints
        if constraint.name == name
    )


def _column_names(constraint: object) -> set[str]:
    return {column.name for column in constraint.columns}

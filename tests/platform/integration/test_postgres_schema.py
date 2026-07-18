from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

import peerassist.platform.adapters.postgres_schema as live_schema
from peerassist.platform.adapters.postgres_schema import PostgresSchemaReadiness

pytestmark = pytest.mark.requires_docker


@pytest.fixture(scope="module")
def database_url() -> str:
    value = os.environ.get("PEERASSIST_TEST_DATABASE_URL")
    if not value:
        pytest.skip("PEERASSIST_TEST_DATABASE_URL is not configured")
    return value.replace("postgresql://", "postgresql+psycopg://", 1)


@pytest.fixture(scope="module")
def alembic_config(database_url: str) -> Config:
    root = Path(__file__).parents[3]
    config = Config(root / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


async def test_real_postgres_upgrade_downgrade_and_repeat(
    database_url: str,
    alembic_config: Config,
) -> None:
    engine = create_engine(database_url)
    command.downgrade(alembic_config, "base")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE m0_legacy_marker (id integer PRIMARY KEY)"))
    readiness = PostgresSchemaReadiness(engine)
    assert await readiness.check() is False
    original_metadata = live_schema.metadata
    live_schema.metadata = MetaData()
    try:
        command.upgrade(alembic_config, "head")
    finally:
        live_schema.metadata = original_metadata
    command.upgrade(alembic_config, "head")
    assert "audit_events" in inspect(engine).get_table_names()
    assert await readiness.check() is True

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO audit_events "
                "(id, actor_id, actor_kind, action, resource_type, resource_id, outcome, "
                "request_id, metadata, created_at) "
                "VALUES (gen_random_uuid(), gen_random_uuid(), 'operator', 'test.append', "
                "'schema', 'migration', 'succeeded', 'migration-test', '{}'::jsonb, now())"
            )
        )
        with pytest.raises(Exception, match="append-only"):
            connection.execute(text("UPDATE audit_events SET outcome = 'failed'"))

    command.downgrade(alembic_config, "base")
    remaining = set(inspect(engine).get_table_names())
    assert remaining == {"alembic_version", "m0_legacy_marker"}
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM alembic_version")) == 0
    assert await readiness.check() is False

    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id uuid PRIMARY KEY)"))
    with pytest.raises(Exception):
        command.upgrade(alembic_config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM alembic_version")) == 0
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE users"))

    command.upgrade(alembic_config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0001_platform_m1"
    assert await readiness.check() is True
    _assert_relationship_integrity(engine)
    await _assert_readiness_detects_catalog_drift(engine, readiness)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE m0_legacy_marker"))
    engine.dispose()


def _assert_relationship_integrity(engine: object) -> None:
    ids = {
        "user_a": "00000000-0000-0000-0000-000000000001",
        "user_b": "00000000-0000-0000-0000-000000000002",
        "identity_a": "00000000-0000-0000-0000-000000000101",
        "org_a": "00000000-0000-0000-0000-000000000201",
        "org_b": "00000000-0000-0000-0000-000000000202",
        "project_a": "00000000-0000-0000-0000-000000000301",
        "project_b": "00000000-0000-0000-0000-000000000302",
        "paper_a": "00000000-0000-0000-0000-000000000401",
        "paper_b": "00000000-0000-0000-0000-000000000402",
        "paper_c": "00000000-0000-0000-0000-000000000403",
        "version_a": "00000000-0000-0000-0000-000000000411",
        "version_b": "00000000-0000-0000-0000-000000000412",
        "version_c": "00000000-0000-0000-0000-000000000413",
        "job_a": "00000000-0000-0000-0000-000000000501",
        "job_b": "00000000-0000-0000-0000-000000000502",
        "job_c": "00000000-0000-0000-0000-000000000503",
        "attempt_a": "00000000-0000-0000-0000-000000000511",
        "attempt_b": "00000000-0000-0000-0000-000000000512",
        "manifest_a": "00000000-0000-0000-0000-000000000521",
        "command_org_a": "00000000-0000-0000-0000-000000000601",
        "command_org_b": "00000000-0000-0000-0000-000000000602",
        "command_project_a": "00000000-0000-0000-0000-000000000603",
        "command_project_b": "00000000-0000-0000-0000-000000000604",
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, status, display_name, version, created_at, updated_at) "
                "VALUES (:user_a, 'active', 'A', 1, now(), now()), "
                "(:user_b, 'active', 'B', 1, now(), now())"
            ),
            ids,
        )
        connection.execute(
            text(
                "INSERT INTO external_identities "
                "(id, user_id, issuer, subject, verified_claims, last_seen_at, created_at, version) "
                "VALUES (:identity_a, :user_a, 'https://issuer.test', 'subject-a', '{}', now(), now(), 1)"
            ),
            ids,
        )
        connection.execute(
            text(
                "INSERT INTO browser_sessions "
                "(id, user_id, identity_id, session_digest, csrf_digest, provider_credential_ref, "
                "token_expires_at, expires_at, idle_expires_at, created_at, updated_at) VALUES "
                "(gen_random_uuid(), :user_a, :identity_a, repeat('a', 64), repeat('b', 64), "
                "'credential-a', now() + interval '1 hour', now() + interval '1 hour', "
                "now() + interval '30 minutes', now(), now())"
            ),
            ids,
        )
    _expect_integrity_error(
        engine,
        "INSERT INTO browser_sessions "
        "(id, user_id, identity_id, session_digest, csrf_digest, provider_credential_ref, "
        "token_expires_at, expires_at, idle_expires_at, created_at, updated_at) VALUES "
        "(gen_random_uuid(), :user_b, :identity_a, repeat('c', 64), repeat('d', 64), "
        "'credential-b', now() + interval '1 hour', now() + interval '1 hour', "
        "now() + interval '30 minutes', now(), now())",
        ids,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organizations (id, slug, name, status, version, created_at, updated_at) "
                "VALUES (:org_a, 'org-a', 'Org A', 'active', 1, now(), now()), "
                "(:org_b, 'org-b', 'Org B', 'active', 1, now(), now())"
            ),
            ids,
        )
        connection.execute(
            text(
                "INSERT INTO projects (id, organization_id, name, status, version, created_at, updated_at) "
                "VALUES (:project_a, :org_a, 'Project A', 'active', 1, now(), now()), "
                "(:project_b, :org_a, 'Project B', 'active', 1, now(), now())"
            ),
            ids,
        )
        connection.execute(
            text(
                "INSERT INTO papers "
                "(id, organization_id, project_id, content_sha256, current_version_id, status, "
                "version, created_at, updated_at) VALUES "
                "(:paper_a, :org_a, :project_a, repeat('1', 64), NULL, 'active', 1, now(), now()), "
                "(:paper_b, :org_a, :project_a, repeat('2', 64), NULL, 'active', 1, now(), now()), "
                "(:paper_c, :org_a, :project_b, repeat('3', 64), NULL, 'active', 1, now(), now())"
            ),
            ids,
        )
        connection.execute(
            text(
                "INSERT INTO paper_versions "
                "(id, organization_id, project_id, paper_id, source_object_id, filename, media_type, "
                "size_bytes, sha256, created_by, revision, created_at) VALUES "
                "(:version_a, :org_a, :project_a, :paper_a, 'object-a', 'a.pdf', 'application/pdf', "
                "1, repeat('1', 64), :user_a, 1, now()), "
                "(:version_b, :org_a, :project_a, :paper_b, 'object-b', 'b.pdf', 'application/pdf', "
                "1, repeat('2', 64), :user_a, 1, now()), "
                "(:version_c, :org_a, :project_b, :paper_c, 'object-c', 'c.pdf', 'application/pdf', "
                "1, repeat('3', 64), :user_a, 1, now())"
            ),
            ids,
        )
        connection.execute(
            text(
                "UPDATE papers SET current_version_id = CASE id "
                "WHEN :paper_a THEN CAST(:version_a AS uuid) "
                "WHEN :paper_b THEN CAST(:version_b AS uuid) "
                "WHEN :paper_c THEN CAST(:version_c AS uuid) END"
            ),
            ids,
        )
    _expect_integrity_error(
        engine,
        "UPDATE papers SET current_version_id = :version_b WHERE id = :paper_a",
        ids,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO review_jobs "
                "(id, organization_id, project_id, paper_version_id, mode, stage, status, version, "
                "attempt, created_by, created_at, updated_at) VALUES "
                "(:job_a, :org_a, :project_a, :version_a, 'fast', 'queued', 'queued', 1, 1, "
                ":user_a, now(), now()), "
                "(:job_b, :org_a, :project_a, :version_a, 'fast', 'queued', 'queued', 1, 1, "
                ":user_a, now(), now()), "
                "(:job_c, :org_a, :project_b, :version_c, 'fast', 'queued', 'queued', 1, 1, "
                ":user_a, now(), now())"
            ),
            ids,
        )
        connection.execute(
            text(
                "INSERT INTO review_attempts "
                "(id, organization_id, project_id, job_id, attempt_number, status, created_at) VALUES "
                "(:attempt_a, :org_a, :project_a, :job_a, 1, 'queued', now()), "
                "(:attempt_b, :org_a, :project_a, :job_b, 1, 'queued', now())"
            ),
            ids,
        )
        connection.execute(
            text(
                "INSERT INTO stage_manifests "
                "(id, organization_id, project_id, job_id, attempt_id, stage, input_revision, "
                "output_revision, schema_version, manifest, status, created_at) VALUES "
                "(:manifest_a, :org_a, :project_a, :job_a, :attempt_a, 'parse', 0, 1, 1, '{}', "
                "'committed', now())"
            ),
            ids,
        )
        connection.execute(
            text(
                "INSERT INTO artifacts "
                "(id, organization_id, project_id, job_id, stage_manifest_id, logical_name, object_id, "
                "media_type, size_bytes, sha256, schema_version, status, created_at) VALUES "
                "(gen_random_uuid(), :org_a, :project_a, :job_a, :manifest_a, 'valid', 'artifact-valid', "
                "'application/json', 1, repeat('a', 64), 1, 'available', now())"
            ),
            ids,
        )
        connection.execute(
            text(
                "INSERT INTO artifacts "
                "(id, organization_id, project_id, job_id, stage_manifest_id, logical_name, object_id, "
                "media_type, size_bytes, sha256, schema_version, status, created_at) VALUES "
                "(gen_random_uuid(), :org_a, :project_a, :job_a, NULL, 'without-manifest', "
                "'artifact-without-manifest', 'application/json', 1, repeat('0', 64), 1, "
                "'available', now())"
            ),
            ids,
        )
    _expect_integrity_error(
        engine,
        "INSERT INTO artifacts "
        "(id, organization_id, project_id, job_id, stage_manifest_id, logical_name, object_id, "
        "media_type, size_bytes, sha256, schema_version, status, created_at) VALUES "
        "(gen_random_uuid(), :org_a, :project_a, :job_b, :manifest_a, 'invalid', 'artifact-invalid', "
        "'application/json', 1, repeat('b', 64), 1, 'available', now())",
        ids,
    )
    _expect_integrity_error(
        engine,
        "INSERT INTO artifacts "
        "(id, organization_id, project_id, job_id, stage_manifest_id, logical_name, object_id, "
        "media_type, size_bytes, sha256, schema_version, status, created_at) VALUES "
        "(gen_random_uuid(), :org_a, :project_b, :job_c, :manifest_a, 'cross-project', "
        "'artifact-cross-project', 'application/json', 1, repeat('c', 64), 1, 'available', now())",
        ids,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO commands "
                "(id, organization_id, project_id, actor_id, operation, idempotency_key, "
                "payload_digest, created_at) VALUES "
                "(:command_org_a, :org_a, NULL, :user_a, 'org-a', 'org-a', repeat('a', 64), now()), "
                "(:command_org_b, :org_b, NULL, :user_a, 'org-b', 'org-b', repeat('b', 64), now()), "
                "(:command_project_a, :org_a, :project_a, :user_a, 'project-a', 'project-a', "
                "repeat('c', 64), now()), "
                "(:command_project_b, :org_a, :project_b, :user_a, 'project-b', 'project-b', "
                "repeat('d', 64), now())"
            ),
            ids,
        )
        connection.execute(
            text(
                "INSERT INTO audit_events "
                "(id, actor_id, actor_kind, organization_id, project_id, command_id, action, "
                "resource_type, resource_id, outcome, request_id, metadata, created_at) VALUES "
                "(gen_random_uuid(), :user_a, 'user', :org_a, NULL, :command_org_a, 'valid.org', "
                "'command', 'org-a', 'succeeded', 'valid-org', '{}', now()), "
                "(gen_random_uuid(), :user_a, 'user', :org_a, :project_a, :command_project_a, "
                "'valid.project', 'command', 'project-a', 'succeeded', 'valid-project', '{}', now())"
            ),
            ids,
        )
    _expect_integrity_error(
        engine,
        "INSERT INTO audit_events "
        "(id, actor_id, actor_kind, organization_id, command_id, action, resource_type, resource_id, "
        "outcome, request_id, metadata, created_at) VALUES "
        "(gen_random_uuid(), :user_a, 'user', :org_a, :command_org_b, 'invalid.org', 'command', "
        "'org-b', 'failed', 'invalid-org', '{}', now())",
        ids,
    )
    _expect_integrity_error(
        engine,
        "INSERT INTO audit_events "
        "(id, actor_id, actor_kind, organization_id, project_id, command_id, action, resource_type, "
        "resource_id, outcome, request_id, metadata, created_at) VALUES "
        "(gen_random_uuid(), :user_a, 'user', :org_a, :project_a, :command_project_b, "
        "'invalid.project', 'command', 'project-b', 'failed', 'invalid-project', '{}', now())",
        ids,
    )
    _expect_integrity_error(
        engine,
        "INSERT INTO audit_events "
        "(id, actor_id, actor_kind, organization_id, project_id, command_id, action, resource_type, "
        "resource_id, outcome, request_id, metadata, created_at) VALUES "
        "(gen_random_uuid(), :user_a, 'user', :org_a, :project_a, :command_org_a, "
        "'invalid.project-to-org', 'command', 'org-a', 'failed', 'invalid-project-to-org', '{}', now())",
        ids,
    )
    _expect_integrity_error(
        engine,
        "INSERT INTO audit_events "
        "(id, actor_id, actor_kind, organization_id, command_id, action, resource_type, resource_id, "
        "outcome, request_id, metadata, created_at) VALUES "
        "(gen_random_uuid(), :user_a, 'user', :org_a, :command_project_a, 'invalid.org-to-project', "
        "'command', 'project-a', 'failed', 'invalid-org-to-project', '{}', now())",
        ids,
    )


async def _assert_readiness_detects_catalog_drift(
    engine: object,
    readiness: PostgresSchemaReadiness,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE browser_sessions "
                "DROP CONSTRAINT fk_browser_sessions_user_identity"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE browser_sessions ADD CONSTRAINT "
                "fk_browser_sessions_user_identity FOREIGN KEY (identity_id) "
                "REFERENCES external_identities (id)"
            )
        )
    assert await readiness.check() is False
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO browser_sessions "
                "(id, user_id, identity_id, session_digest, csrf_digest, provider_credential_ref, "
                "token_expires_at, expires_at, idle_expires_at, created_at, updated_at) VALUES "
                "(gen_random_uuid(), '00000000-0000-0000-0000-000000000002', "
                "'00000000-0000-0000-0000-000000000101', repeat('e', 64), repeat('f', 64), "
                "'weaker-fk-proof', now() + interval '1 hour', now() + interval '1 hour', "
                "now() + interval '30 minutes', now(), now())"
            )
        )
        connection.execute(
            text("DELETE FROM browser_sessions WHERE session_digest = repeat('e', 64)")
        )
        connection.execute(
            text(
                "ALTER TABLE browser_sessions "
                "DROP CONSTRAINT fk_browser_sessions_user_identity"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE browser_sessions ADD CONSTRAINT "
                "fk_browser_sessions_user_identity FOREIGN KEY (user_id, identity_id) "
                "REFERENCES external_identities (user_id, id)"
            )
        )
    assert await readiness.check() is True

    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE users ALTER COLUMN display_name DROP NOT NULL"))
    assert await readiness.check() is False
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE users ALTER COLUMN display_name SET NOT NULL"))
    assert await readiness.check() is True

    with engine.begin() as connection:
        connection.execute(text("DROP INDEX ix_audit_events_command"))
        connection.execute(text("CREATE INDEX ix_audit_events_command ON audit_events (command_id)"))
    assert await readiness.check() is False
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX ix_audit_events_command"))
        connection.execute(
            text(
                "CREATE INDEX ix_audit_events_command ON audit_events "
                "(organization_id, project_id, command_id)"
            )
        )
    assert await readiness.check() is True

    with engine.begin() as connection:
        connection.execute(text("DROP TRIGGER trg_audit_events_append_only ON audit_events"))
        connection.execute(
            text(
                "CREATE TRIGGER trg_audit_events_append_only "
                "BEFORE DELETE OR UPDATE ON audit_events FOR EACH STATEMENT "
                "EXECUTE FUNCTION peerassist_reject_audit_mutation()"
            )
        )
    assert await readiness.check() is False
    with engine.begin() as connection:
        connection.execute(text("DROP TRIGGER trg_audit_events_append_only ON audit_events"))
        connection.execute(text(live_schema.AUDIT_TRIGGER_SQL))
    assert await readiness.check() is True

    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE OR REPLACE FUNCTION peerassist_reject_audit_mutation() "
                "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$"
            )
        )
    assert await readiness.check() is False
    with engine.begin() as connection:
        result = connection.execute(
            text("UPDATE audit_events SET outcome = 'failed' WHERE request_id = 'valid-org'")
        )
        assert result.rowcount == 1

    with engine.begin() as connection:
        connection.execute(text(live_schema.AUDIT_FUNCTION_SQL))
    assert await readiness.check() is True
    with pytest.raises(Exception, match="append-only"):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE audit_events SET outcome = 'succeeded' WHERE request_id = 'valid-org'")
            )


def _expect_integrity_error(
    engine: object,
    statement: str,
    parameters: dict[str, str],
) -> None:
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(text(statement), parameters)

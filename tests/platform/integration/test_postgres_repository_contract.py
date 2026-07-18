from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from peerassist.platform.adapters.postgres import PostgresUnitOfWorkFactory
from peerassist.platform.adapters.postgres_schema import metadata
from peerassist.platform.models import Actor, ActorKind, Project, TenantScope

pytestmark = pytest.mark.requires_docker


@pytest.fixture
def postgres_factory(request: pytest.FixtureRequest) -> PostgresUnitOfWorkFactory:
    raw_url = os.environ.get("PEERASSIST_TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("PEERASSIST_TEST_DATABASE_URL is not configured")
    database_url = raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
    config = Config(request.config.rootpath / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")
    engine = create_engine(database_url, pool_pre_ping=True)
    with engine.begin() as connection:
        tables = ", ".join(f'"{table.name}"' for table in reversed(metadata.sorted_tables))
        connection.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
    factory = PostgresUnitOfWorkFactory(engine)
    yield factory
    engine.dispose()


def _seed_projects(factory: PostgresUnitOfWorkFactory) -> tuple[Actor, TenantScope, TenantScope]:
    actor = Actor(uuid4(), ActorKind.USER)
    visible = TenantScope(uuid4(), uuid4())
    hidden = TenantScope(uuid4(), visible.project_id)
    now = datetime(2026, 7, 18, 8, tzinfo=UTC)
    with factory.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organizations (id, slug, name, status, version, created_at, updated_at) "
                "VALUES (:visible, :visible_slug, 'Visible', 'active', 1, :now, :now), "
                "(:hidden, :hidden_slug, 'Hidden', 'active', 1, :now, :now)"
            ),
            {
                "visible": visible.organization_id,
                "hidden": hidden.organization_id,
                "visible_slug": f"visible-{visible.organization_id}",
                "hidden_slug": f"hidden-{hidden.organization_id}",
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO projects (id, organization_id, name, status, version, created_at, updated_at) "
                "VALUES (:project, :organization, 'Visible project', 'active', 1, :now, :now)"
            ),
            {"project": visible.project_id, "organization": visible.organization_id, "now": now},
        )
    return actor, visible, hidden


def test_repository_requires_active_uow_and_does_not_leak_connection(
    postgres_factory: PostgresUnitOfWorkFactory,
) -> None:
    actor, scope, _ = _seed_projects(postgres_factory)
    uow = postgres_factory(actor)
    with pytest.raises(RuntimeError, match="active"):
        _ = uow.projects
    with uow:
        repository = uow.projects
        assert repository.get(scope) is not None
    with pytest.raises(RuntimeError, match="active"):
        repository.get(scope)
    assert postgres_factory.engine.pool.checkedout() == 0


def test_rollback_and_cross_tenant_repository_bypass_are_hidden(
    postgres_factory: PostgresUnitOfWorkFactory,
) -> None:
    actor, scope, hidden = _seed_projects(postgres_factory)
    now = datetime(2026, 7, 18, 8, tzinfo=UTC)
    discarded_scope = TenantScope(scope.organization_id, uuid4())
    discarded = Project(
        discarded_scope.project_id,
        discarded_scope.organization_id,
        "Discarded",
        "active",
        1,
        now,
        now,
    )
    with postgres_factory(actor) as uow:
        assert uow.projects.get(hidden) is None
        assert tuple(uow.projects.list(hidden)) == ()
        uow.projects.add(discarded_scope, discarded)
        uow.rollback()
    with postgres_factory(actor) as uow:
        assert uow.projects.get(discarded_scope) is None
    assert postgres_factory.engine.pool.checkedout() == 0


def test_database_errors_are_stable_and_do_not_expose_sql_or_dsn(
    postgres_factory: PostgresUnitOfWorkFactory,
) -> None:
    actor, scope, _ = _seed_projects(postgres_factory)
    now = datetime(2026, 7, 18, 8, tzinfo=UTC)
    duplicate = Project(scope.project_id, scope.organization_id, "Changed", "active", 1, now, now)
    with postgres_factory(actor) as uow:
        with pytest.raises(ValueError, match="already exists") as caught:
            uow.projects.add(scope, duplicate)
    rendered = f"{caught.value!s} {caught.value!r}"
    assert "INSERT" not in rendered
    assert "postgresql" not in rendered


def test_cross_tenant_global_project_id_collision_is_a_safe_generic_conflict(
    postgres_factory: PostgresUnitOfWorkFactory,
) -> None:
    actor, visible, hidden = _seed_projects(postgres_factory)
    now = datetime(2026, 7, 18, 8, tzinfo=UTC)
    collision = Project(hidden.project_id, hidden.organization_id, "Collision", "active", 1, now, now)
    with postgres_factory(actor) as uow:
        with pytest.raises(ValueError) as caught:
            uow.projects.add(hidden, collision)
    rendered = f"{caught.value!s} {caught.value!r}"
    assert str(visible.organization_id) not in rendered
    assert str(visible.project_id) not in rendered


def test_foreign_keys_and_append_only_trigger_remain_enabled(
    postgres_factory: PostgresUnitOfWorkFactory,
) -> None:
    with pytest.raises(IntegrityError):
        with postgres_factory.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO projects (id, organization_id, name, status, version, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), gen_random_uuid(), 'Invalid', 'active', 1, now(), now())"
                )
            )
    actor, scope, _ = _seed_projects(postgres_factory)
    with pytest.raises(Exception, match="append-only"):
        with postgres_factory.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO audit_events (id, actor_id, actor_kind, organization_id, project_id, "
                    "action, resource_type, resource_id, outcome, request_id, metadata, created_at) "
                    "VALUES (:id, :actor, 'user', :organization, :project, 'review_job.create', "
                    "'review_job', :resource, 'succeeded', 'trigger-probe', '{}', now())"
                ),
                {
                    "id": uuid4(),
                    "actor": actor.actor_id,
                    "organization": scope.organization_id,
                    "project": scope.project_id,
                    "resource": str(uuid4()),
                },
            )
            connection.execute(text("UPDATE audit_events SET outcome = 'failed'"))

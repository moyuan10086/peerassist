from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

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
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE m0_legacy_marker (id integer PRIMARY KEY)"))
    readiness = PostgresSchemaReadiness(engine)
    assert await readiness.check() is False
    command.upgrade(alembic_config, "head")
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

    command.upgrade(alembic_config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0001_platform_m1"
    assert await readiness.check() is True
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE m0_legacy_marker"))
    engine.dispose()

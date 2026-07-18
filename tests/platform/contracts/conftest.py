from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from peerassist.platform.adapters.memory import (
    FakeIdentityProvider,
    MemoryObjectStore,
    MemoryUnitOfWorkFactory,
)


class MutableClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--adapter",
        action="store",
        default="memory",
        help="contract adapter: memory/postgresql, minio, fake/keycloak",
    )


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock()


@pytest.fixture
def uow_factory(request: pytest.FixtureRequest, clock: MutableClock):
    adapter = request.config.getoption("--adapter")
    if adapter == "postgresql":
        from alembic import command
        from alembic.config import Config
        from sqlalchemy import event, text

        from peerassist.platform.adapters.postgres import PostgresUnitOfWorkFactory
        from peerassist.platform.adapters.postgres_schema import metadata

        database_url = os.environ.get("PEERASSIST_TEST_DATABASE_URL")
        if not database_url:
            pytest.skip("PEERASSIST_TEST_DATABASE_URL is not configured")
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        root = request.config.rootpath
        config = Config(root / "alembic.ini")
        config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
        command.upgrade(config, "head")
        factory = PostgresUnitOfWorkFactory.from_url(database_url, clock=clock)
        with factory.engine.begin() as connection:
            table_names = ", ".join(f'"{table.name}"' for table in reversed(metadata.sorted_tables))
            connection.execute(text(f"TRUNCATE TABLE {table_names} CASCADE"))

        @event.listens_for(factory.engine, "begin")
        def disable_relationship_triggers(connection) -> None:
            connection.exec_driver_sql("SET LOCAL session_replication_role = replica")

        request.addfinalizer(factory.engine.dispose)
        return factory
    if adapter != "memory":
        pytest.skip(f"unit-of-work adapter {adapter!r} is not implemented")
    return MemoryUnitOfWorkFactory(clock=clock)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--adapter") == "postgresql":
        for item in items:
            if "/platform/contracts/" in str(item.path):
                item.add_marker(pytest.mark.requires_docker)


@pytest.fixture
def object_store_factory(request: pytest.FixtureRequest, clock: MutableClock):
    adapter = request.config.getoption("--adapter")
    if adapter != "memory":
        pytest.skip(f"object-store adapter {adapter!r} is not implemented in Task 3")
    return lambda: MemoryObjectStore(clock=clock)


@pytest.fixture
def identity_provider_factory(request: pytest.FixtureRequest, clock: MutableClock):
    adapter = request.config.getoption("--adapter")
    if adapter not in {"memory", "fake"}:
        pytest.skip(f"identity adapter {adapter!r} is not implemented in Task 3")
    return lambda **kwargs: FakeIdentityProvider(clock=clock, **kwargs)

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

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
        from sqlalchemy import text

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
            table_names = ", ".join(
                f'"{table.name}"'
                for table in reversed(metadata.sorted_tables)
                if table.name != "schema_metadata"
            )
            connection.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))

        request.addfinalizer(factory.engine.dispose)
        return _ContractPostgresFactory(factory, text)
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


class _ContractPostgresFactory:
    """Supply explicit FK parents while exercising the real repositories."""

    def __init__(self, factory: Any, text_factory: Any) -> None:
        self._factory = factory
        self._text = text_factory

    def __call__(self, actor: Any) -> _ContractPostgresUow:
        return _ContractPostgresUow(self._factory(actor), actor, self._text)


class _ContractPostgresUow:
    _repository_names = (
        "users", "organizations", "projects", "papers", "review_jobs", "artifacts",
        "commands", "work_items", "outbox", "audit", "legacy_registrations",
        "browser_sessions", "oidc_transactions",
    )

    def __init__(self, real: Any, actor: Any, text_factory: Any) -> None:
        self._real = real
        self.actor = actor
        self._text = text_factory

    def __enter__(self) -> _ContractPostgresUow:
        self._real.__enter__()
        for name in self._repository_names:
            setattr(self, name, _ContractRepository(self, name, getattr(self._real, name)))
        return self

    def __exit__(self, *args: Any) -> Any:
        return self._real.__exit__(*args)

    def commit(self) -> None:
        self._real.commit()

    def rollback(self) -> None:
        self._real.rollback()

    def _execute(self, statement: str, parameters: dict[str, Any]) -> None:
        connection = self._real._connection_for_repository()
        connection.execute(self._text(statement), parameters)

    @staticmethod
    def _now() -> datetime:
        return datetime(2026, 7, 17, 8, tzinfo=UTC)

    def ensure_user(self, user_id: UUID) -> None:
        self._execute(
            "INSERT INTO users (id, status, display_name, version, created_at, updated_at) "
            "VALUES (:id, 'active', 'Contract parent', 1, :now, :now) ON CONFLICT (id) DO NOTHING",
            {"id": user_id, "now": self._now()},
        )

    def ensure_organization(self, organization_id: UUID) -> None:
        self._execute(
            "INSERT INTO organizations (id, slug, name, status, version, created_at, updated_at) "
            "VALUES (:id, :slug, 'Contract parent', 'active', 1, :now, :now) "
            "ON CONFLICT (id) DO NOTHING",
            {"id": organization_id, "slug": f"contract-{organization_id.hex}", "now": self._now()},
        )

    def ensure_project(self, scope: Any) -> None:
        self.ensure_organization(scope.organization_id)
        if scope.project_id is None:
            return
        self._execute(
            "INSERT INTO projects (id, organization_id, name, status, version, created_at, updated_at) "
            "VALUES (:id, :organization_id, 'Contract parent', 'active', 1, :now, :now) "
            "ON CONFLICT (id) DO NOTHING",
            {"id": scope.project_id, "organization_id": scope.organization_id, "now": self._now()},
        )

    def ensure_paper_version(self, scope: Any, version_id: UUID, created_by: UUID | None = None) -> None:
        assert scope.project_id is not None
        creator = created_by or self.actor.actor_id
        paper_id = uuid5(NAMESPACE_URL, f"contract-paper:{version_id}")
        self.ensure_project(scope)
        self.ensure_user(creator)
        parameters = {
            "paper": paper_id,
            "version": version_id,
            "organization": scope.organization_id,
            "project": scope.project_id,
            "creator": creator,
            "digest": version_id.hex * 2,
            "now": self._now(),
        }
        self._execute(
            "INSERT INTO papers (id, organization_id, project_id, content_sha256, current_version_id, "
            "status, version, created_at, updated_at) VALUES "
            "(:paper, :organization, :project, :digest, NULL, 'active', 1, :now, :now) "
            "ON CONFLICT (id) DO NOTHING",
            parameters,
        )
        self._execute(
            "INSERT INTO paper_versions (id, organization_id, project_id, paper_id, source_object_id, "
            "filename, media_type, size_bytes, sha256, created_by, revision, created_at) VALUES "
            "(:version, :organization, :project, :paper, :object_id, 'parent.pdf', 'application/pdf', "
            "1, :digest, :creator, 1, :now) ON CONFLICT (id) DO NOTHING",
            parameters | {"object_id": f"contract-parent-{version_id}"},
        )
        self._execute(
            "UPDATE papers SET current_version_id = :version WHERE organization_id = :organization "
            "AND project_id = :project AND id = :paper AND current_version_id IS NULL",
            parameters,
        )

    def ensure_job(self, scope: Any, job_id: UUID) -> None:
        assert scope.project_id is not None
        version_id = uuid5(NAMESPACE_URL, f"contract-version:{job_id}")
        self.ensure_paper_version(scope, version_id)
        self._execute(
            "INSERT INTO review_jobs (id, organization_id, project_id, paper_version_id, mode, stage, "
            "status, version, attempt, created_by, created_at, updated_at) VALUES "
            "(:id, :organization, :project, :version, 'fast', 'queued', 'queued', 1, 0, :creator, "
            ":now, :now) ON CONFLICT (id) DO NOTHING",
            {
                "id": job_id, "organization": scope.organization_id, "project": scope.project_id,
                "version": version_id, "creator": self.actor.actor_id, "now": self._now(),
            },
        )

    def ensure_attempt(self, scope: Any, item: Any) -> None:
        self.ensure_job(scope, item.job_id)
        self._execute(
            "INSERT INTO review_attempts (id, organization_id, project_id, job_id, attempt_number, "
            "status, created_at) VALUES (:id, :organization, :project, :job, 1, 'queued', :now) "
            "ON CONFLICT (id) DO NOTHING",
            {
                "id": item.attempt_id, "organization": scope.organization_id,
                "project": scope.project_id, "job": item.job_id, "now": self._now(),
            },
        )


class _ContractRepository:
    def __init__(self, uow: _ContractPostgresUow, name: str, real: Any) -> None:
        self._uow = uow
        self._name = name
        self._real = real

    def __getattr__(self, operation: str) -> Any:
        real_operation = getattr(self._real, operation)

        def invoke(*args: Any, **kwargs: Any) -> Any:
            self._prepare(operation, args)
            return real_operation(*args, **kwargs)

        return invoke

    def _prepare(self, operation: str, args: tuple[Any, ...]) -> None:
        if not args:
            return
        scope = args[0] if hasattr(args[0], "organization_id") else None
        value = args[1] if len(args) > 1 else None
        project_repositories = {
            "projects", "papers", "review_jobs", "artifacts", "work_items", "legacy_registrations"
        }
        if self._name in project_repositories and scope.project_id is None:
            return
        if self._name == "projects" and operation == "add":
            self._uow.ensure_organization(scope.organization_id)
        elif self._name == "organizations" and operation == "save_membership":
            self._uow.ensure_organization(scope.organization_id)
            self._uow.ensure_user(value.user_id)
        elif self._name == "projects" and operation == "save_membership":
            self._uow.ensure_project(scope)
            self._uow.ensure_user(value.user_id)
        elif self._name == "papers" and operation in {"add", "add_version"}:
            self._uow.ensure_project(scope)
            version = args[2] if operation == "add" else value
            self._uow.ensure_user(version.created_by)
        elif self._name == "review_jobs" and operation == "add":
            self._uow.ensure_paper_version(scope, value.paper_version_id, value.created_by)
        elif self._name == "artifacts" and operation == "add":
            self._uow.ensure_job(scope, value.job_id)
        elif self._name == "legacy_registrations" and operation == "add":
            self._uow.ensure_project(scope)
            self._uow.ensure_user(self._uow.actor.actor_id)
        elif self._name == "work_items" and operation == "enqueue":
            self._uow.ensure_attempt(scope, value)
        elif self._name in {"commands", "outbox", "audit"} and operation in {
            "reserve", "reserve_or_replay", "complete", "append",
        }:
            self._uow.ensure_project(scope)

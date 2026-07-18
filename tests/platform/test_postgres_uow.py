from __future__ import annotations

import ast
import inspect
import textwrap
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from peerassist.platform.adapters import postgres_schema as schema
from peerassist.platform.adapters.postgres import PostgresUnitOfWorkFactory
from peerassist.platform.adapters.postgres_core import _project_filter, _record_filter
from peerassist.platform.adapters.postgres_queue import _Audit, _Outbox, _WorkItems
from peerassist.platform.adapters.postgres_repositories import _Projects
from peerassist.platform.adapters.postgres_review import (
    _Artifacts,
    _LegacyRegistrations,
    _Papers,
    _ReviewJobs,
)
from peerassist.platform.errors import DependencyUnavailable
from peerassist.platform.models import Actor, ActorKind, Project, TenantScope


class FakeTransaction:
    def __init__(self, *, commit_error: bool = False, rollback_error: bool = False) -> None:
        self.is_active = True
        self.commit_error = commit_error
        self.rollback_error = rollback_error
        self.rollback_calls = 0

    def commit(self) -> None:
        if self.commit_error:
            raise _provider_error()
        self.is_active = False

    def rollback(self) -> None:
        self.rollback_calls += 1
        if self.rollback_error:
            raise _provider_error()
        self.is_active = False


class FakeConnection:
    def __init__(
        self,
        *,
        begin_error: bool = False,
        execute_error: bool = False,
        close_error: bool = False,
        transaction: FakeTransaction | None = None,
    ) -> None:
        self.begin_error = begin_error
        self.execute_error = execute_error
        self.close_error = close_error
        self.transaction = transaction or FakeTransaction()
        self.closed = False

    def begin(self) -> FakeTransaction:
        if self.begin_error:
            raise _provider_error()
        return self.transaction

    def execute(self, statement):
        del statement
        if self.execute_error:
            raise _provider_error()
        raise AssertionError("unexpected successful SQL execution")

    def close(self) -> None:
        self.closed = True
        if self.close_error:
            raise _provider_error()


@dataclass
class FakeEngine:
    connection: FakeConnection | None = None
    connect_error: bool = False

    def connect(self) -> FakeConnection:
        if self.connect_error:
            raise _provider_error()
        assert self.connection is not None
        return self.connection

    def dispose(self) -> None:
        return None


def _provider_error() -> OperationalError:
    return OperationalError(
        "SELECT private_sql",
        {},
        RuntimeError("postgresql://user:secret@private-db/database"),
    )


def _actor() -> Actor:
    return Actor(uuid4(), ActorKind.SERVICE)


def _assert_safe(error: DependencyUnavailable) -> None:
    rendered = f"{error!s} {error!r}"
    assert "private_sql" not in rendered
    assert "secret" not in rendered
    assert "postgresql" not in rendered


def test_begin_failure_closes_the_acquired_connection() -> None:
    connection = FakeConnection(begin_error=True)
    factory = PostgresUnitOfWorkFactory(FakeEngine(connection))
    with pytest.raises(DependencyUnavailable) as caught:
        factory(_actor()).__enter__()
    _assert_safe(caught.value)
    assert connection.closed is True


@pytest.mark.asyncio
async def test_start_connect_failure_is_safe() -> None:
    factory = PostgresUnitOfWorkFactory(FakeEngine(connect_error=True))
    with pytest.raises(DependencyUnavailable) as caught:
        await factory.start()
    _assert_safe(caught.value)


def test_commit_failure_is_safe_and_exit_rolls_back_and_closes() -> None:
    transaction = FakeTransaction(commit_error=True)
    connection = FakeConnection(transaction=transaction)
    factory = PostgresUnitOfWorkFactory(FakeEngine(connection))
    with factory(_actor()) as uow:
        with pytest.raises(DependencyUnavailable) as caught:
            uow.commit()
        _assert_safe(caught.value)
    assert transaction.rollback_calls == 1
    assert connection.closed is True


def test_rollback_and_close_failures_are_safe_without_connection_leak() -> None:
    transaction = FakeTransaction(rollback_error=True)
    connection = FakeConnection(transaction=transaction, close_error=True)
    factory = PostgresUnitOfWorkFactory(FakeEngine(connection))
    with pytest.raises(DependencyUnavailable) as caught:
        with factory(_actor()) as uow:
            with pytest.raises(DependencyUnavailable):
                uow.rollback()
    _assert_safe(caught.value)
    assert connection.closed is True


@pytest.mark.parametrize("operation", ["read", "write", "claim"])
def test_repository_sqlalchemy_errors_are_normalized(operation: str) -> None:
    connection = FakeConnection(execute_error=True)
    factory = PostgresUnitOfWorkFactory(FakeEngine(connection))
    scope = TenantScope(uuid4(), uuid4())
    with factory(_actor()) as uow:
        with pytest.raises(DependencyUnavailable) as caught:
            if operation == "read":
                uow.projects.get(scope)
            elif operation == "write":
                now = datetime(2026, 7, 18, tzinfo=UTC)
                uow.projects.add(
                    scope,
                    Project(scope.project_id, scope.organization_id, "Project", "active", 1, now, now),
                )
            else:
                uow.work_items.claim(scope, "worker", 30)
        _assert_safe(caught.value)


def test_compiled_project_and_record_predicates_include_exact_tenant_columns() -> None:
    scope = TenantScope(uuid4(), uuid4())
    project_tables = (
        schema.projects,
        schema.papers,
        schema.paper_versions,
        schema.review_jobs,
        schema.review_events,
        schema.work_items,
        schema.artifacts,
        schema.legacy_registrations,
    )
    for table in project_tables:
        sql = str(select(table).where(_project_filter(table, scope)).compile())
        assert f"{table.name}.organization_id" in sql
        project_column = "id" if table is schema.projects else "project_id"
        assert f"{table.name}.{project_column}" in sql
    for table in (schema.commands, schema.outbox_events, schema.audit_events):
        sql = str(select(table).where(_record_filter(table, scope)).compile())
        assert f"{table.name}.organization_id" in sql
        assert f"{table.name}.project_id" in sql


def test_project_repository_sql_ast_uses_tenant_predicates_for_every_resource_path() -> None:
    expected = {
        _Projects: {"get", "list", "add", "save", "get_membership", "save_membership"},
        _Papers: {"get", "find_by_content_digest", "get_version", "list", "add", "add_version"},
        _ReviewJobs: {"get", "list", "add", "save", "append_event", "list_events"},
        _Artifacts: {"get", "list_for_job", "add"},
        _LegacyRegistrations: {"get", "add"},
        _WorkItems: {"enqueue", "get", "claim", "renew", "complete", "fail", "_active"},
        _Outbox: {"append", "claim_batch", "mark_published"},
        _Audit: {"append", "list"},
    }
    record_scoped = {_Outbox, _Audit}
    for repository, methods in expected.items():
        predicate = "_record_filter" if repository in record_scoped else "_project_filter"
        for method_name in methods:
            tree = ast.parse(textwrap.dedent(inspect.getsource(getattr(repository, method_name))))
            assert predicate in ast.unparse(tree), f"{repository.__name__}.{method_name} lacks {predicate}"

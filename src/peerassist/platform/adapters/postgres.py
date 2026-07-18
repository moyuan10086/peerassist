"""Public PostgreSQL unit-of-work composition facade."""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self

from sqlalchemy import Connection, Engine, create_engine
from sqlalchemy.exc import SQLAlchemyError

from peerassist.platform.errors import DependencyUnavailable
from peerassist.platform.models import Actor

from .postgres_core import Clock, _utc_now
from .postgres_queue import _Audit, _Commands, _Outbox, _WorkItems
from .postgres_repositories import (
    _BrowserSessions,
    _OidcTransactions,
    _Organizations,
    _Projects,
    _Users,
)
from .postgres_review import _Artifacts, _LegacyRegistrations, _Papers, _ReviewJobs


class PostgresUnitOfWork:
    """One explicit database transaction with adapter-private connection state."""

    _repository_names = (
        "users", "organizations", "projects", "papers", "review_jobs", "artifacts", "commands",
        "work_items", "outbox", "audit", "legacy_registrations", "browser_sessions", "oidc_transactions",
    )

    def __init__(self, factory: PostgresUnitOfWorkFactory, actor: Actor) -> None:
        self._factory = factory
        self.actor = actor
        self._connection: Connection | None = None
        self._transaction: Any | None = None
        self._repositories: dict[str, object] = {}
        self._active = False

    def __getattr__(self, name: str) -> object:
        if name in self._repository_names:
            if not self._active:
                raise RuntimeError("unit of work is not active")
            return self._repositories[name]
        raise AttributeError(name)

    def __enter__(self) -> Self:
        if self._active:
            raise RuntimeError("unit of work is already active")
        try:
            self._connection = self._factory.engine.connect()
            self._transaction = self._connection.begin()
        except SQLAlchemyError as error:
            raise DependencyUnavailable(cause=error) from None
        self._active = True
        self._repositories = {
            "users": _Users(self),
            "organizations": _Organizations(self),
            "projects": _Projects(self),
            "papers": _Papers(self),
            "review_jobs": _ReviewJobs(self),
            "artifacts": _Artifacts(self),
            "commands": _Commands(self),
            "work_items": _WorkItems(self, self._factory.clock),
            "outbox": _Outbox(self, self._factory.clock),
            "audit": _Audit(self),
            "legacy_registrations": _LegacyRegistrations(self),
            "browser_sessions": _BrowserSessions(self, self._factory.clock),
            "oidc_transactions": _OidcTransactions(self),
        }
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_value, traceback
        try:
            if self._transaction is not None and self._transaction.is_active:
                self._transaction.rollback()
        finally:
            self._active = False
            self._repositories.clear()
            if self._connection is not None:
                self._connection.close()
            self._connection = None
            self._transaction = None
        if exc_type is not None:
            return None

    def _connection_for_repository(self) -> Connection:
        if not self._active or self._connection is None or self._transaction is None:
            raise RuntimeError("unit of work is not active")
        if not self._transaction.is_active:
            raise RuntimeError("unit of work transaction is not active")
        return self._connection

    def commit(self) -> None:
        if not self._active or self._transaction is None or not self._transaction.is_active:
            raise RuntimeError("unit of work is not active")
        try:
            self._transaction.commit()
        except SQLAlchemyError as error:
            raise DependencyUnavailable(cause=error) from None

    def rollback(self) -> None:
        if not self._active or self._transaction is None or not self._transaction.is_active:
            raise RuntimeError("unit of work is not active")
        self._transaction.rollback()


class PostgresUnitOfWorkFactory:
    """Create PostgreSQL UoWs without exposing provider sessions to services."""

    def __init__(self, engine: Engine, *, clock: Clock = _utc_now) -> None:
        self.engine = engine
        self.clock = clock

    @classmethod
    def from_url(
        cls, database_url: str, *, clock: Clock = _utc_now
    ) -> PostgresUnitOfWorkFactory:
        return cls(
            create_engine(database_url, pool_pre_ping=True, pool_reset_on_return="rollback"),
            clock=clock,
        )

    def __call__(self, actor: Actor) -> PostgresUnitOfWork:
        if not isinstance(actor, Actor):
            raise TypeError("actor must be an Actor")
        return PostgresUnitOfWork(self, actor)

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        self.engine.dispose()


__all__ = ["PostgresUnitOfWork", "PostgresUnitOfWorkFactory"]

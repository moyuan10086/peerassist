"""Idempotent commands, work leases, outbox publication, and audit ledger."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, exists, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from peerassist.platform.errors import IdempotencyConflict, NotFound
from peerassist.platform.models import (
    AuditEvent,
    CommandRecord,
    OutboxEvent,
    TenantScope,
    WorkItem,
)

from . import postgres_schema as schema
from .postgres_core import (
    Clock,
    _audit,
    _command,
    _json,
    _outbox,
    _project_filter,
    _record_filter,
    _Repository,
    _require_project,
    _require_record,
    _work_item,
)


class _Commands(_Repository):
    @staticmethod
    def _identity(command: CommandRecord) -> tuple[object, ...]:
        return (
            command.organization_id,
            command.actor_id,
            command.operation,
            command.idempotency_key,
            command.payload_digest,
        )

    @staticmethod
    def _completion(command: CommandRecord) -> tuple[object, ...]:
        return command.response_status, command.response_body, command.completed_at

    def get(
        self, scope: TenantScope, actor_id: UUID, operation: str, idempotency_key: str
    ) -> CommandRecord | None:
        row = self._one(
            select(schema.commands).where(
                _record_filter(schema.commands, scope), schema.commands.c.actor_id == actor_id,
                schema.commands.c.operation == operation, schema.commands.c.idempotency_key == idempotency_key,
            )
        )
        return None if row is None else _command(row)

    def reserve(self, scope: TenantScope, command: CommandRecord) -> None:
        self._reserve(scope, command)

    def reserve_or_replay(self, scope: TenantScope, command: CommandRecord) -> CommandRecord:
        return self._reserve(scope, command)

    def _reserve(self, scope: TenantScope, command: CommandRecord) -> CommandRecord:
        _require_record(scope, command)
        values = {
            "id": command.id, "organization_id": command.organization_id, "project_id": command.project_id,
            "actor_id": command.actor_id, "operation": command.operation,
            "idempotency_key": command.idempotency_key, "payload_digest": command.payload_digest,
            "response_status": command.response_status,
            "response_body": None if command.response_status is None else _json(command.response_body),
            "created_at": command.created_at, "completed_at": command.completed_at,
        }
        self.connection.execute(
            pg_insert(schema.commands).values(**values).on_conflict_do_nothing(
                index_elements=[
                    schema.commands.c.organization_id, schema.commands.c.actor_id,
                    schema.commands.c.operation, schema.commands.c.idempotency_key,
                ]
            )
        )
        row = self._one(
            select(schema.commands)
            .where(
                _record_filter(schema.commands, scope),
                schema.commands.c.actor_id == command.actor_id,
                schema.commands.c.operation == command.operation,
                schema.commands.c.idempotency_key == command.idempotency_key,
            )
            .with_for_update()
        )
        if row is None:
            raise IdempotencyConflict()
        existing = _command(row)
        if (
            existing.project_id != command.project_id
            or self._identity(existing) != self._identity(command)
        ):
            raise IdempotencyConflict()
        return existing

    def complete(self, scope: TenantScope, command: CommandRecord) -> None:
        _require_record(scope, command)
        row = self._one(
            select(schema.commands)
            .where(
                _record_filter(schema.commands, scope),
                schema.commands.c.actor_id == command.actor_id,
                schema.commands.c.operation == command.operation,
                schema.commands.c.idempotency_key == command.idempotency_key,
            )
            .with_for_update()
        )
        if row is None:
            raise IdempotencyConflict()
        current = _command(row)
        if self._identity(current) != self._identity(command):
            raise IdempotencyConflict()
        if current.completed_at is not None:
            if self._completion(current) == self._completion(command):
                return
            raise IdempotencyConflict()
        if command.completed_at is None or command.response_status is None:
            raise IdempotencyConflict()
        self.connection.execute(
            update(schema.commands)
            .where(
                _record_filter(schema.commands, scope),
                schema.commands.c.id == current.id,
                schema.commands.c.completed_at.is_(None),
            )
            .values(
                response_status=command.response_status, response_body=_json(command.response_body),
                completed_at=command.completed_at,
            )
        )


class _WorkItems(_Repository):
    def __init__(self, uow: Any, clock: Clock) -> None:
        super().__init__(uow)
        self._clock = clock

    def enqueue(self, scope: TenantScope, item: WorkItem) -> None:
        _require_project(scope, item)
        current = self._one(
            select(schema.work_items).where(
                _project_filter(schema.work_items, scope), schema.work_items.c.id == item.id
            )
        )
        if current is not None:
            if _work_item(current) == item:
                return
            raise ValueError("work item already exists")
        values = {
            "id": item.id, "organization_id": item.organization_id, "project_id": item.project_id,
            "job_id": item.job_id, "attempt_id": item.attempt_id, "stage": item.stage,
            "input_revision": item.input_revision, "attempt_count": item.attempt_count,
            "max_attempts": item.max_attempts, "available_at": item.available_at,
            "lease_owner": item.lease_owner, "lease_expires_at": item.lease_expires_at,
            "dead_lettered_at": item.dead_lettered_at, "safe_error_code": item.safe_error_code,
            "created_at": item.created_at, "updated_at": item.created_at,
        }
        self._integrity(
            lambda: self.connection.execute(
                pg_insert(schema.work_items).values(**values).on_conflict_do_nothing(
                    index_elements=[
                        schema.work_items.c.job_id, schema.work_items.c.attempt_id,
                        schema.work_items.c.stage, schema.work_items.c.input_revision,
                    ]
                )
            ),
            "work item already exists or is invalid",
        )

    def get(self, scope: TenantScope, item_id: UUID) -> WorkItem | None:
        row = self._one(
            select(schema.work_items).where(
                _project_filter(schema.work_items, scope), schema.work_items.c.id == item_id
            )
        )
        return None if row is None else _work_item(row)

    def claim(self, scope: TenantScope, worker_id: str, lease_seconds: int) -> WorkItem | None:
        if scope.project_id is None or lease_seconds <= 0 or not worker_id.strip():
            return None
        now = self._clock()
        while True:
            row = self._one(
                select(schema.work_items)
                .where(
                    _project_filter(schema.work_items, scope),
                    schema.work_items.c.dead_lettered_at.is_(None),
                    schema.work_items.c.available_at <= now,
                    or_(
                        schema.work_items.c.lease_owner.is_(None),
                        schema.work_items.c.lease_expires_at <= now,
                    ),
                )
                .order_by(schema.work_items.c.available_at, schema.work_items.c.created_at, schema.work_items.c.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            item = _work_item(row)
            if item.lease_expires_at is not None and item.attempt_count >= item.max_attempts:
                self.connection.execute(
                    update(schema.work_items)
                    .where(_project_filter(schema.work_items, scope), schema.work_items.c.id == item.id)
                    .values(
                        lease_owner=None, lease_expires_at=None, dead_lettered_at=now,
                        safe_error_code="lease_expired", updated_at=now,
                    )
                )
                continue
            result = self.connection.execute(
                update(schema.work_items)
                .where(_project_filter(schema.work_items, scope), schema.work_items.c.id == item.id)
                .values(
                    attempt_count=item.attempt_count + 1, lease_owner=worker_id,
                    lease_expires_at=now + timedelta(seconds=lease_seconds), updated_at=now,
                )
                .returning(schema.work_items)
            ).mappings().one()
            return _work_item(result)

    def renew(self, scope: TenantScope, item_id: UUID, lease_owner: str, lease_seconds: int) -> WorkItem:
        item = self._active(scope, item_id, lease_owner)
        now = self._clock()
        row = self.connection.execute(
            update(schema.work_items)
            .where(_project_filter(schema.work_items, scope), schema.work_items.c.id == item.id)
            .values(lease_expires_at=now + timedelta(seconds=lease_seconds), updated_at=now)
            .returning(schema.work_items)
        ).mappings().one()
        return _work_item(row)

    def complete(self, scope: TenantScope, item_id: UUID, lease_owner: str) -> None:
        item = self._active(scope, item_id, lease_owner)
        self.connection.execute(
            delete(schema.work_items).where(
                _project_filter(schema.work_items, scope), schema.work_items.c.id == item.id
            )
        )

    def fail(self, scope: TenantScope, item: WorkItem, lease_owner: str) -> None:
        current = self._active(scope, item.id, lease_owner)
        now = self._clock()
        exhausted = current.attempt_count >= current.max_attempts
        self.connection.execute(
            update(schema.work_items)
            .where(_project_filter(schema.work_items, scope), schema.work_items.c.id == current.id)
            .values(
                lease_owner=None, lease_expires_at=None, available_at=now,
                dead_lettered_at=now if exhausted else None,
                safe_error_code="attempts_exhausted" if exhausted else current.safe_error_code,
                updated_at=now,
            )
        )

    def _active(self, scope: TenantScope, item_id: UUID, lease_owner: str) -> WorkItem:
        row = self._one(
            select(schema.work_items)
            .where(_project_filter(schema.work_items, scope), schema.work_items.c.id == item_id)
            .with_for_update()
        )
        item = None if row is None else _work_item(row)
        if (
            item is None or item.lease_owner != lease_owner or item.lease_expires_at is None
            or item.lease_expires_at <= self._clock()
        ):
            raise ValueError("work item lease is absent, expired, or owned by another worker")
        return item


class _Outbox(_Repository):
    def __init__(self, uow: Any, clock: Clock) -> None:
        super().__init__(uow)
        self._clock = clock

    def append(self, scope: TenantScope, event: OutboxEvent) -> None:
        _require_record(scope, event)
        current = self._one(
            select(schema.outbox_events).where(
                _record_filter(schema.outbox_events, scope), schema.outbox_events.c.id == event.id
            )
        )
        if current is not None:
            raise ValueError("outbox event already exists")
        self._integrity(
            lambda: self.connection.execute(
                insert(schema.outbox_events).values(
                    id=event.id, organization_id=event.organization_id, project_id=event.project_id,
                    aggregate_type=event.aggregate_type, aggregate_id=event.aggregate_id,
                    aggregate_sequence=event.aggregate_sequence, event_type=event.event_type,
                    schema_version=event.schema_version, payload=_json(event.payload),
                    publication_attempts=event.publication_attempts, published_at=event.published_at,
                    created_at=event.created_at,
                )
            ),
            "outbox event already exists or is invalid",
        )

    def claim_batch(self, scope: TenantScope, limit: int) -> tuple[OutboxEvent, ...]:
        if limit <= 0:
            return ()
        candidate = schema.outbox_events.alias("outbox_candidate")
        lower = schema.outbox_events.alias("outbox_lower")
        lower_unpublished_exists = exists(
            select(1).select_from(lower).where(
                lower.c.organization_id == candidate.c.organization_id,
                lower.c.project_id.is_not_distinct_from(candidate.c.project_id),
                lower.c.aggregate_type == candidate.c.aggregate_type,
                lower.c.aggregate_id == candidate.c.aggregate_id,
                lower.c.aggregate_sequence < candidate.c.aggregate_sequence,
                lower.c.published_at.is_(None),
            )
        )
        rows = tuple(
            self.connection.execute(
                select(candidate)
                .where(
                    _record_filter(candidate, scope),
                    candidate.c.published_at.is_(None),
                    ~lower_unpublished_exists,
                )
                .order_by(
                    candidate.c.created_at,
                    candidate.c.aggregate_id,
                    candidate.c.aggregate_sequence,
                    candidate.c.id,
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).mappings()
        )
        result = []
        for row in rows:
            updated = self.connection.execute(
                update(schema.outbox_events)
                .where(_record_filter(schema.outbox_events, scope), schema.outbox_events.c.id == row["id"])
                .values(publication_attempts=row["publication_attempts"] + 1)
                .returning(schema.outbox_events)
            ).mappings().one()
            result.append(_outbox(updated))
        return tuple(result)

    def mark_published(self, scope: TenantScope, event_id: UUID) -> None:
        row = self._one(
            select(schema.outbox_events)
            .where(_record_filter(schema.outbox_events, scope), schema.outbox_events.c.id == event_id)
            .with_for_update()
        )
        if row is None:
            raise NotFound()
        if row["published_at"] is None:
            self.connection.execute(
                update(schema.outbox_events)
                .where(_record_filter(schema.outbox_events, scope), schema.outbox_events.c.id == event_id)
                .values(published_at=self._clock())
            )


class _Audit(_Repository):
    def append(self, scope: TenantScope, event: AuditEvent) -> None:
        _require_record(scope, event)
        if self._one(
            select(schema.audit_events.c.id).where(
                _record_filter(schema.audit_events, scope), schema.audit_events.c.id == event.id
            )
        ) is not None:
            raise ValueError("audit records are append-only")
        self._integrity(
            lambda: self.connection.execute(
                insert(schema.audit_events).values(
                    id=event.id, actor_id=event.actor_id, actor_kind=self._uow.actor.kind.value,
                    identity_id=event.identity_id, organization_id=event.organization_id,
                    project_id=event.project_id, command_id=event.command_id, action=event.action.value,
                    resource_type=event.resource_type, resource_id=str(event.resource_id), outcome=event.outcome,
                    request_id=event.request_id, metadata=_json(event.safe_metadata), created_at=event.created_at,
                )
            ),
            "audit records are append-only or invalid",
        )

    def list(self, scope: TenantScope) -> tuple[AuditEvent, ...]:
        rows = self.connection.execute(
            select(schema.audit_events)
            .where(_record_filter(schema.audit_events, scope))
            .order_by(schema.audit_events.c.created_at, schema.audit_events.c.id)
        ).mappings()
        return tuple(_audit(row) for row in rows)

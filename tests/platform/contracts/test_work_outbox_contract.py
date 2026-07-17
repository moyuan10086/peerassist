from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from peerassist.platform.models import (
    Action,
    Actor,
    ActorKind,
    AuditEvent,
    OutboxEvent,
    TenantScope,
    WorkItem,
)


def actor() -> Actor:
    return Actor(uuid4(), ActorKind.SERVICE)


def work(scope: TenantScope, now) -> WorkItem:
    return WorkItem(
        uuid4(),
        scope.organization_id,
        scope.project_id,
        uuid4(),
        uuid4(),
        "parse",
        1,
        0,
        3,
        now,
        now,
    )


def outbox(scope: TenantScope, now, sequence: int = 1) -> OutboxEvent:
    return OutboxEvent(
        uuid4(),
        scope.organization_id,
        "review_job",
        uuid4(),
        sequence,
        "job.queued",
        1,
        {},
        now,
        project_id=scope.project_id,
    )


def test_work_dedupe_claim_renew_ack_and_nack(uow_factory, clock) -> None:
    principal = actor()
    scope = TenantScope(uuid4(), uuid4())
    item = work(scope, clock())
    with uow_factory(principal) as uow:
        uow.work_items.enqueue(scope, item)
        uow.work_items.enqueue(scope, replace(item, id=uuid4()))
        uow.commit()
    with uow_factory(principal) as uow:
        claimed = uow.work_items.claim(scope, "worker-1", 30)
        assert claimed is not None and claimed.attempt_count == 1
        renewed = uow.work_items.renew(scope, claimed.id, "worker-1", 60)
        assert renewed.lease_expires_at == clock() + timedelta(seconds=60)
        uow.work_items.fail(scope, renewed, "worker-1")
        uow.commit()
    with uow_factory(principal) as uow:
        redelivered = uow.work_items.claim(scope, "worker-2", 30)
        assert redelivered is not None and redelivered.id == item.id
        assert redelivered.attempt_count == 2
        uow.work_items.complete(scope, redelivered.id, "worker-2")
        uow.commit()
    with uow_factory(principal) as uow:
        assert uow.work_items.claim(scope, "worker-3", 30) is None


def test_three_lease_expiries_dead_letter_work(uow_factory, clock) -> None:
    principal = actor()
    scope = TenantScope(uuid4(), uuid4())
    item = work(scope, clock())
    with uow_factory(principal) as uow:
        uow.work_items.enqueue(scope, item)
        uow.commit()
    for attempt in range(3):
        with uow_factory(principal) as uow:
            claimed = uow.work_items.claim(scope, f"worker-{attempt}", 10)
            assert claimed is not None
            uow.commit()
        clock.now += timedelta(seconds=11)
    with uow_factory(principal) as uow:
        assert uow.work_items.claim(scope, "worker-after-third-expiry", 10) is None
        stored = uow.work_items.get(scope, item.id)
        assert stored.dead_lettered_at is not None
        assert stored.attempt_count == 3


def test_wrong_lease_owner_cannot_renew_ack_or_nack(uow_factory, clock) -> None:
    principal = actor()
    scope = TenantScope(uuid4(), uuid4())
    item = work(scope, clock())
    with uow_factory(principal) as uow:
        uow.work_items.enqueue(scope, item)
        claimed = uow.work_items.claim(scope, "owner", 30)
        for operation in (
            lambda: uow.work_items.renew(scope, claimed.id, "intruder", 60),
            lambda: uow.work_items.complete(scope, claimed.id, "intruder"),
            lambda: uow.work_items.fail(scope, claimed, "intruder"),
        ):
            with pytest.raises(ValueError, match="lease"):
                operation()


def test_outbox_is_atomic_ordered_and_publication_is_idempotent(uow_factory, clock) -> None:
    principal = actor()
    scope = TenantScope(uuid4(), uuid4())
    first = outbox(scope, clock(), 1)
    second = replace(first, id=uuid4(), aggregate_sequence=2)
    with uow_factory(principal) as uow:
        uow.outbox.append(scope, second)
        uow.outbox.append(scope, first)
        uow.rollback()
    with uow_factory(principal) as uow:
        assert uow.outbox.claim_batch(scope, 10) == ()
        uow.outbox.append(scope, second)
        uow.outbox.append(scope, first)
        uow.commit()
    with uow_factory(principal) as uow:
        claimed = uow.outbox.claim_batch(scope, 10)
        assert [event.aggregate_sequence for event in claimed] == [1, 2]
        uow.outbox.mark_published(scope, first.id)
        uow.outbox.mark_published(scope, first.id)
        uow.commit()
    with uow_factory(principal) as uow:
        assert uow.outbox.claim_batch(scope, 10) == (replace(second, publication_attempts=2),)


def test_audit_is_append_only_and_tenant_scoped(uow_factory, clock) -> None:
    principal = actor()
    scope = TenantScope(uuid4(), uuid4())
    event = AuditEvent(
        uuid4(),
        principal.actor_id,
        scope.organization_id,
        Action.REVIEW_JOB_CREATE,
        "review_job",
        uuid4(),
        "allowed",
        "request-1",
        clock(),
        project_id=scope.project_id,
    )
    with uow_factory(principal) as uow:
        uow.audit.append(scope, event)
        uow.commit()
    with uow_factory(principal) as uow:
        assert tuple(uow.audit.list(scope)) == (event,)
        assert tuple(uow.audit.list(TenantScope(uuid4(), scope.project_id))) == ()
        assert not hasattr(uow.audit, "delete")
        with pytest.raises(ValueError, match="append-only"):
            uow.audit.append(scope, event)

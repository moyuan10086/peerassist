from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from peerassist.platform.errors import NotFound
from peerassist.platform.models import (
    Action,
    Actor,
    ActorKind,
    Artifact,
    AuditEvent,
    ObjectDescriptor,
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


def test_three_explicit_nacks_dead_letter_without_a_fourth_attempt(uow_factory, clock) -> None:
    principal = actor()
    scope = TenantScope(uuid4(), uuid4())
    item = work(scope, clock())
    with uow_factory(principal) as uow:
        uow.work_items.enqueue(scope, item)
        uow.commit()
    for attempt in range(1, 4):
        with uow_factory(principal) as uow:
            claimed = uow.work_items.claim(scope, f"worker-{attempt}", 30)
            assert claimed is not None and claimed.attempt_count == attempt
            uow.work_items.fail(scope, claimed, f"worker-{attempt}")
            uow.commit()
    with uow_factory(principal) as uow:
        stored = uow.work_items.get(scope, item.id)
        assert stored.attempt_count == stored.max_attempts == 3
        assert stored.dead_lettered_at == clock()
        assert stored.safe_error_code == "attempts_exhausted"
        assert stored.lease_owner is None
        assert stored.lease_expires_at is None
        assert stored.available_at == clock()
        assert uow.work_items.claim(scope, "worker-4", 30) is None


def test_work_enqueue_rejects_duplicate_id_with_different_identity(uow_factory, clock) -> None:
    principal = actor()
    scope = TenantScope(uuid4(), uuid4())
    item = work(scope, clock())
    with uow_factory(principal) as uow:
        uow.work_items.enqueue(scope, item)
        with pytest.raises(ValueError, match="already exists"):
            uow.work_items.enqueue(scope, replace(item, stage="review"))


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
        assert [event.aggregate_sequence for event in claimed] == [1]
        uow.outbox.mark_published(scope, first.id)
        uow.outbox.mark_published(scope, first.id)
        uow.commit()
    with uow_factory(principal) as uow:
        assert uow.outbox.claim_batch(scope, 10) == (replace(second, publication_attempts=1),)


def test_outbox_claims_only_the_minimum_unpublished_sequence_per_aggregate(
    uow_factory,
    clock,
) -> None:
    principal = actor()
    scope = TenantScope(uuid4(), uuid4())
    first = outbox(scope, clock(), 1)
    second = replace(
        first,
        id=uuid4(),
        aggregate_sequence=2,
        created_at=clock() - timedelta(seconds=1),
    )
    with uow_factory(principal) as uow:
        uow.outbox.append(scope, second)
        uow.outbox.append(scope, first)
        uow.commit()
    with uow_factory(principal) as uow:
        assert uow.outbox.claim_batch(scope, 10) == (replace(first, publication_attempts=1),)
        uow.outbox.mark_published(scope, first.id)
        uow.commit()
    with uow_factory(principal) as uow:
        assert uow.outbox.claim_batch(scope, 10) == (replace(second, publication_attempts=1),)


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
        "succeeded",
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


def test_project_work_outbox_artifacts_and_audit_reject_organization_only_scope(uow_factory, clock) -> None:
    principal = actor()
    scope = TenantScope(uuid4(), uuid4())
    organization_only = TenantScope(scope.organization_id)
    other_project = TenantScope(scope.organization_id, uuid4())
    other_organization = TenantScope(uuid4(), scope.project_id)
    item = work(scope, clock())
    event = outbox(scope, clock())
    artifact = Artifact(
        uuid4(),
        scope.organization_id,
        scope.project_id,
        item.job_id,
        "report",
        ObjectDescriptor("object-1", 3, "a" * 64, "application/pdf"),
        "available",
        clock(),
    )
    audit = AuditEvent(
        uuid4(),
        principal.actor_id,
        scope.organization_id,
        Action.REVIEW_JOB_CREATE,
        "review_job",
        item.job_id,
        "succeeded",
        "request-2",
        clock(),
        project_id=scope.project_id,
    )
    with uow_factory(principal) as uow:
        uow.work_items.enqueue(scope, item)
        uow.outbox.append(scope, event)
        uow.artifacts.add(scope, artifact)
        uow.audit.append(scope, audit)
        uow.commit()

    for hidden_scope in (organization_only, other_project, other_organization):
        with uow_factory(principal) as uow:
            assert uow.work_items.get(hidden_scope, item.id) is None
            assert uow.work_items.claim(hidden_scope, "worker", 30) is None
            assert uow.outbox.claim_batch(hidden_scope, 10) == ()
            assert uow.artifacts.get(hidden_scope, artifact.id) is None
            assert tuple(uow.artifacts.list_for_job(hidden_scope, item.job_id)) == ()
            assert tuple(uow.audit.list(hidden_scope)) == ()

    with uow_factory(principal) as uow:
        for operation in (
            lambda: uow.work_items.enqueue(organization_only, replace(item, id=uuid4())),
            lambda: uow.outbox.append(organization_only, replace(event, id=uuid4())),
            lambda: uow.artifacts.add(organization_only, replace(artifact, id=uuid4())),
            lambda: uow.audit.append(organization_only, replace(audit, id=uuid4())),
        ):
            with pytest.raises(NotFound):
                operation()


def test_organization_scoped_outbox_and_audit_are_visible_only_to_exact_org_scope(uow_factory, clock) -> None:
    principal = actor()
    scope = TenantScope(uuid4())
    event = OutboxEvent(
        uuid4(),
        scope.organization_id,
        "organization",
        scope.organization_id,
        1,
        "organization.created",
        1,
        {},
        clock(),
    )
    audit = AuditEvent(
        uuid4(),
        principal.actor_id,
        scope.organization_id,
        Action.ORGANIZATION_MANAGE_MEMBERS,
        "organization",
        scope.organization_id,
        "succeeded",
        "request-org",
        clock(),
    )
    with uow_factory(principal) as uow:
        uow.outbox.append(scope, event)
        uow.audit.append(scope, audit)
        uow.commit()
    with uow_factory(principal) as uow:
        assert uow.outbox.claim_batch(scope, 10) == (replace(event, publication_attempts=1),)
        assert tuple(uow.audit.list(scope)) == (audit,)
        project_scope = TenantScope(scope.organization_id, uuid4())
        assert uow.outbox.claim_batch(project_scope, 10) == ()
        assert tuple(uow.audit.list(project_scope)) == ()

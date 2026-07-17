from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from peerassist.platform.errors import IdempotencyConflict, StaleVersion
from peerassist.platform.idempotency import canonical_json_digest
from peerassist.platform.models import (
    Actor,
    ActorKind,
    BrowserSession,
    CommandRecord,
    ExternalIdentity,
    OidcTransaction,
    Project,
    ReviewEvent,
    ReviewJob,
    TenantScope,
    User,
)


def actor() -> Actor:
    return Actor(uuid4(), ActorKind.USER)


def project(scope: TenantScope, now, *, version: int = 1) -> Project:
    assert scope.project_id is not None
    return Project(scope.project_id, scope.organization_id, "Alpha", "active", version, now, now)


def job(scope: TenantScope, now, *, version: int = 1) -> ReviewJob:
    assert scope.project_id is not None
    return ReviewJob(
        uuid4(),
        scope.organization_id,
        scope.project_id,
        uuid4(),
        "standard",
        "queued",
        "queued",
        version,
        0,
        uuid4(),
        now,
        now,
    )


def test_commit_persists_and_rollback_discards_all_transactional_state(uow_factory, clock) -> None:
    principal = actor()
    scope = TenantScope(uuid4(), uuid4())
    committed = project(scope, clock())
    discarded = replace(committed, id=uuid4(), name="Discarded")

    with uow_factory(principal) as uow:
        uow.projects.add(scope, committed)
        uow.commit()
    with uow_factory(principal) as uow:
        uow.projects.add(TenantScope(scope.organization_id, discarded.id), discarded)
        uow.rollback()
    with uow_factory(principal) as uow:
        assert uow.projects.get(scope) == committed
        assert uow.projects.get(TenantScope(scope.organization_id, discarded.id)) is None


def test_exception_rolls_back_state_events_work_and_outbox(uow_factory, clock) -> None:
    principal = actor()
    scope = TenantScope(uuid4(), uuid4())
    aggregate = job(scope, clock())
    event = ReviewEvent(
        uuid4(), scope.organization_id, scope.project_id, aggregate.id, 1, "queued", 1, {}, clock()
    )

    with pytest.raises(RuntimeError):
        with uow_factory(principal) as uow:
            uow.review_jobs.add(scope, aggregate)
            uow.review_jobs.append_event(scope, event)
            raise RuntimeError("force rollback")
    with uow_factory(principal) as uow:
        assert uow.review_jobs.get(scope, aggregate.id) is None
        assert uow.review_jobs.list_events(scope, aggregate.id) == ()


def test_tenant_predicates_hide_cross_tenant_rows_and_lists(uow_factory, clock) -> None:
    principal = actor()
    visible_scope = TenantScope(uuid4(), uuid4())
    hidden_scope = TenantScope(uuid4(), visible_scope.project_id)
    aggregate = project(visible_scope, clock())
    with uow_factory(principal) as uow:
        uow.projects.add(visible_scope, aggregate)
        uow.commit()
    with uow_factory(principal) as uow:
        assert uow.projects.get(hidden_scope) is None
        assert tuple(uow.projects.list(hidden_scope)) == ()
        assert uow.projects.get(visible_scope) == aggregate


def test_optimistic_save_is_compare_and_swap(uow_factory, clock) -> None:
    principal = actor()
    scope = TenantScope(uuid4(), uuid4())
    original = project(scope, clock())
    updated = replace(original, name="Beta", version=2)
    with uow_factory(principal) as uow:
        uow.projects.add(scope, original)
        uow.commit()
    with uow_factory(principal) as uow:
        with pytest.raises(StaleVersion):
            uow.projects.save(scope, updated, expected_version=7)
        uow.projects.save(scope, updated, expected_version=1)
        uow.commit()
    with uow_factory(principal) as uow:
        assert uow.projects.get(scope) == updated


def test_concurrent_command_replay_and_changed_payload_conflict(uow_factory, clock) -> None:
    principal = actor()
    scope = TenantScope(uuid4(), uuid4())
    command_id = uuid4()
    payload = {"name": "Alpha"}
    record = CommandRecord(
        command_id,
        scope.organization_id,
        principal.actor_id,
        "project.create",
        "same-key",
        canonical_json_digest(payload),
        clock(),
        project_id=scope.project_id,
    )
    completed = replace(
        record, response_status=201, response_body={"id": str(command_id)}, completed_at=clock()
    )

    def execute_or_replay() -> CommandRecord:
        with uow_factory(principal) as uow:
            existing = uow.commands.reserve_or_replay(scope, record)
            if existing.completed_at is None:
                uow.commands.complete(scope, completed)
                existing = completed
            uow.commit()
            return existing

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = tuple(executor.map(lambda _: execute_or_replay(), range(16)))
    assert results == (completed,) * 16

    with uow_factory(principal) as uow:
        changed = replace(completed, id=uuid4(), payload_digest=canonical_json_digest({"name": "Beta"}))
        with pytest.raises(IdempotencyConflict):
            uow.commands.reserve_or_replay(scope, changed)


def test_aggregate_events_are_strictly_ordered_and_unique(uow_factory, clock) -> None:
    principal = actor()
    scope = TenantScope(uuid4(), uuid4())
    aggregate = job(scope, clock())
    first = ReviewEvent(
        uuid4(), scope.organization_id, scope.project_id, aggregate.id, 1, "queued", 1, {}, clock()
    )
    second = replace(first, id=uuid4(), aggregate_sequence=2, event_type="started")
    with uow_factory(principal) as uow:
        uow.review_jobs.add(scope, aggregate)
        uow.review_jobs.append_event(scope, first)
        uow.review_jobs.append_event(scope, second)
        with pytest.raises(ValueError, match="sequence"):
            uow.review_jobs.append_event(scope, replace(second, id=uuid4()))
        uow.commit()
    with uow_factory(principal) as uow:
        assert uow.review_jobs.list_events(scope, aggregate.id) == (first, second)


def test_identity_sessions_and_oidc_transactions_obey_lifecycle(uow_factory, clock) -> None:
    principal = actor()
    user = User(uuid4(), "active", "Ada", clock(), clock())
    identity = ExternalIdentity(
        uuid4(), user.id, "https://issuer.example", "ada", {"sub": "ada"}, clock(), clock()
    )
    session = BrowserSession(
        uuid4(),
        user.id,
        identity.id,
        "a" * 64,
        "b" * 64,
        "credential-ref",
        clock() + timedelta(hours=2),
        clock() + timedelta(hours=1),
        clock(),
    )
    transaction = OidcTransaction(
        uuid4(),
        "c" * 64,
        "d" * 64,
        b"ciphertext",
        "key-1",
        "/compat/",
        clock() + timedelta(minutes=10),
        clock(),
    )
    with uow_factory(principal) as uow:
        uow.users.add(user)
        uow.users.add_identity(identity)
        uow.browser_sessions.save(session)
        uow.oidc_transactions.add(transaction)
        uow.commit()
    with uow_factory(principal) as uow:
        assert uow.users.get_identity(identity.issuer, identity.subject) == identity
        assert uow.browser_sessions.get_by_digest(session.session_digest) == session
        assert uow.oidc_transactions.get_for_update(transaction.id) == transaction
        uow.oidc_transactions.consume(replace(transaction, consumed_at=clock()))
        uow.browser_sessions.revoke_for_user(user.id)
        uow.commit()
    with uow_factory(principal) as uow:
        assert uow.oidc_transactions.get_for_update(transaction.id).consumed_at == clock()
        assert uow.browser_sessions.get_by_digest(session.session_digest).revoked_at == clock()

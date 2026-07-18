from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from peerassist.platform.adapters.postgres import PostgresUnitOfWorkFactory
from peerassist.platform.adapters.postgres_schema import metadata
from peerassist.platform.errors import IdempotencyConflict
from peerassist.platform.models import (
    Actor,
    ActorKind,
    CommandRecord,
    OidcTransaction,
    OutboxEvent,
    TenantScope,
    WorkItem,
)

pytestmark = pytest.mark.requires_docker


@pytest.fixture
def seeded_factory(request: pytest.FixtureRequest) -> tuple[PostgresUnitOfWorkFactory, Actor, TenantScope, UUID, UUID]:
    raw_url = os.environ.get("PEERASSIST_TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("PEERASSIST_TEST_DATABASE_URL is not configured")
    database_url = raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
    config = Config(request.config.rootpath / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")
    engine = create_engine(database_url, pool_pre_ping=True, pool_size=20, max_overflow=4)
    with engine.begin() as connection:
        tables = ", ".join(
            f'"{table.name}"'
            for table in reversed(metadata.sorted_tables)
            if table.name != "schema_metadata"
        )
        connection.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
    actor = Actor(uuid4(), ActorKind.USER)
    scope = TenantScope(uuid4(), uuid4())
    job_id, attempt_id = uuid4(), uuid4()
    now = datetime(2026, 7, 18, 8, tzinfo=UTC)
    ids = {
        "actor": actor.actor_id,
        "org": scope.organization_id,
        "project": scope.project_id,
        "paper": uuid4(),
        "version": uuid4(),
        "job": job_id,
        "attempt": attempt_id,
        "now": now,
        "slug": f"org-{scope.organization_id}",
    }
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO users VALUES (:actor, 'active', 'Actor', 1, :now, :now)"), ids)
        connection.execute(
            text("INSERT INTO organizations VALUES (:org, :slug, 'Org', 'active', 1, :now, :now)"), ids
        )
        connection.execute(
            text("INSERT INTO projects VALUES (:project, :org, 'Project', 'active', 1, :now, :now)"), ids
        )
        connection.execute(
            text(
                "INSERT INTO papers VALUES (:paper, :org, :project, :digest, NULL, 'active', 1, :now, :now)"
            ),
            ids | {"digest": "a" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO paper_versions VALUES (:version, :org, :project, :paper, 'source-1', "
                "'paper.pdf', 'application/pdf', 1, :digest, :actor, 1, :now)"
            ),
            ids | {"digest": "a" * 64},
        )
        connection.execute(text("UPDATE papers SET current_version_id=:version WHERE id=:paper"), ids)
        connection.execute(
            text(
                "INSERT INTO review_jobs VALUES (:job, :org, :project, :version, 'fast', 'queued', "
                "'queued', 1, 1, :actor, :now, :now, NULL, NULL)"
            ),
            ids,
        )
        connection.execute(
            text(
                "INSERT INTO review_attempts VALUES (:attempt, :org, :project, :job, 1, 'queued', "
                "NULL, NULL, :now)"
            ),
            ids,
        )
    factory = PostgresUnitOfWorkFactory(engine, clock=lambda: now)
    yield factory, actor, scope, job_id, attempt_id
    engine.dispose()


def test_sixteen_concurrent_identical_commands_converge_and_changed_payload_conflicts(
    seeded_factory: tuple[PostgresUnitOfWorkFactory, Actor, TenantScope, UUID, UUID],
) -> None:
    factory, actor, scope, _, _ = seeded_factory
    now = datetime(2026, 7, 18, 8, tzinfo=UTC)
    record = CommandRecord(
        uuid4(), scope.organization_id, actor.actor_id, "project.create", "same-key", "a" * 64, now,
        project_id=scope.project_id,
    )
    completed = replace(record, response_status=201, response_body={"winner": "same"}, completed_at=now)

    def execute(index: int) -> CommandRecord:
        candidate = replace(record, id=uuid4(), created_at=now.replace(microsecond=index + 1))
        with factory(actor) as uow:
            existing = uow.commands.reserve_or_replay(scope, candidate)
            if existing.completed_at is None:
                uow.commands.complete(
                    scope,
                    replace(
                        candidate,
                        response_status=201,
                        response_body={"winner": "same"},
                        completed_at=now,
                    ),
                )
                existing = uow.commands.reserve_or_replay(scope, candidate)
            uow.commit()
            return existing

    with ThreadPoolExecutor(max_workers=16) as executor:
        results = tuple(executor.map(execute, range(16)))
    assert all(result == results[0] for result in results)
    assert results[0].response_status == completed.response_status
    assert results[0].response_body == completed.response_body
    with factory(actor) as uow:
        with pytest.raises(IdempotencyConflict):
            uow.commands.reserve_or_replay(scope, replace(record, payload_digest="b" * 64))


def test_command_replay_cannot_cross_project_scope_or_expose_stored_response(
    seeded_factory: tuple[PostgresUnitOfWorkFactory, Actor, TenantScope, UUID, UUID],
) -> None:
    factory, actor, project_a, _, _ = seeded_factory
    project_b = TenantScope(project_a.organization_id, uuid4())
    now = datetime(2026, 7, 18, 8, tzinfo=UTC)
    with factory.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO projects (id, organization_id, name, status, version, created_at, updated_at) "
                "VALUES (:project, :organization, 'Project B', 'active', 1, :now, :now)"
            ),
            {
                "project": project_b.project_id,
                "organization": project_b.organization_id,
                "now": now,
            },
        )
    record = CommandRecord(
        uuid4(),
        project_a.organization_id,
        actor.actor_id,
        "project.update",
        "cross-project-key",
        "a" * 64,
        now,
        project_id=project_a.project_id,
    )
    completed = replace(
        record,
        response_status=200,
        response_body={"private_result": "project-a"},
        completed_at=now,
    )
    with factory(actor) as uow:
        uow.commands.reserve(project_a, record)
        uow.commands.complete(project_a, completed)
        uow.commit()
    project_b_attempt = replace(
        record,
        id=uuid4(),
        project_id=project_b.project_id,
        created_at=now.replace(microsecond=1),
    )
    with factory(actor) as uow:
        with pytest.raises(IdempotencyConflict):
            uow.commands.reserve_or_replay(project_b, project_b_attempt)
        assert uow.commands.get(project_b, actor.actor_id, record.operation, record.idempotency_key) is None
    with factory(actor) as uow:
        assert uow.commands.reserve_or_replay(
            project_a,
            replace(record, id=uuid4(), created_at=now.replace(microsecond=2)),
        ) == completed


def test_competing_workers_never_receive_the_same_active_claim(
    seeded_factory: tuple[PostgresUnitOfWorkFactory, Actor, TenantScope, UUID, UUID],
) -> None:
    factory, actor, scope, job_id, attempt_id = seeded_factory
    now = datetime(2026, 7, 18, 8, tzinfo=UTC)
    items = tuple(
        WorkItem(
            uuid4(), scope.organization_id, scope.project_id, job_id, attempt_id, f"stage-{index}",
            1, 0, 3, now, now,
        )
        for index in range(8)
    )
    with factory(actor) as uow:
        for item in items:
            uow.work_items.enqueue(scope, item)
        uow.commit()

    def claim(worker: int) -> UUID | None:
        with factory(actor) as uow:
            item = uow.work_items.claim(scope, f"worker-{worker}", 60)
            uow.commit()
            return None if item is None else item.id

    with ThreadPoolExecutor(max_workers=16) as executor:
        claims = tuple(executor.map(claim, range(16)))
    claimed = tuple(item_id for item_id in claims if item_id is not None)
    assert len(claimed) == len(items)
    assert len(set(claimed)) == len(items)


def test_outbox_locked_head_blocks_next_sequence_but_not_another_aggregate(
    seeded_factory: tuple[PostgresUnitOfWorkFactory, Actor, TenantScope, UUID, UUID],
) -> None:
    factory, actor, scope, _, _ = seeded_factory
    now = datetime(2026, 7, 18, 8, tzinfo=UTC)
    aggregate_a, aggregate_b = uuid4(), uuid4()
    first = OutboxEvent(
        uuid4(), scope.organization_id, "review_job", aggregate_a, 1, "started", 1, {}, now,
        project_id=scope.project_id,
    )
    second = replace(first, id=uuid4(), aggregate_sequence=2, created_at=now - timedelta(seconds=1))
    other = replace(
        first,
        id=uuid4(),
        aggregate_id=aggregate_b,
        aggregate_sequence=1,
        created_at=now + timedelta(seconds=1),
    )
    with factory(actor) as uow:
        for event in (second, other, first):
            uow.outbox.append(scope, event)
        uow.commit()

    publisher_a = factory(actor).__enter__()
    try:
        assert publisher_a.outbox.claim_batch(scope, 1) == (replace(first, publication_attempts=1),)
        with factory(actor) as publisher_b:
            assert publisher_b.outbox.claim_batch(scope, 10) == (
                replace(other, publication_attempts=1),
            )
            publisher_b.outbox.mark_published(scope, other.id)
            publisher_b.commit()
        publisher_a.outbox.mark_published(scope, first.id)
        publisher_a.commit()
    finally:
        publisher_a.__exit__(None, None, None)

    with factory(actor) as publisher_b:
        assert publisher_b.outbox.claim_batch(scope, 10) == (
            replace(second, publication_attempts=1),
        )


def test_concurrent_oidc_transactions_enforce_nonce_and_state_uniqueness(
    seeded_factory: tuple[PostgresUnitOfWorkFactory, Actor, TenantScope, UUID, UUID],
) -> None:
    factory, actor, _, _, _ = seeded_factory
    now = datetime(2026, 7, 18, 8, tzinfo=UTC)
    first = OidcTransaction(
        uuid4(), "a" * 64, "b" * 64, b"ciphertext-a", "key-1", "/compat/",
        now + timedelta(minutes=10), now,
    )
    same_nonce = replace(
        first,
        id=uuid4(),
        state_digest="c" * 64,
        encrypted_pkce_verifier=b"ciphertext-b",
    )

    def add(transaction: OidcTransaction) -> bool:
        try:
            with factory(actor) as uow:
                uow.oidc_transactions.add(transaction)
                uow.commit()
            return True
        except ValueError:
            return False

    candidates = (first, same_nonce)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(add, candidates))
    assert sum(results) == 1

    winner = next(
        transaction
        for transaction, inserted in zip(candidates, results, strict=True)
        if inserted
    )
    same_state = replace(
        winner,
        id=uuid4(),
        nonce_digest="d" * 64,
        encrypted_pkce_verifier=b"ciphertext-c",
    )
    with factory(actor) as uow:
        with pytest.raises(ValueError, match="already exists"):
            uow.oidc_transactions.add(same_state)

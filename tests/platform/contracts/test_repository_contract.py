from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from peerassist.platform.errors import IdempotencyConflict, NotFound, StaleVersion
from peerassist.platform.idempotency import canonical_json_digest
from peerassist.platform.models import (
    Actor,
    ActorKind,
    BrowserSession,
    CommandRecord,
    ExternalIdentity,
    OidcTransaction,
    Paper,
    PaperVersion,
    Project,
    ProjectMembership,
    ReviewEvent,
    ReviewJob,
    Role,
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


def paper(scope: TenantScope, now) -> tuple[Paper, PaperVersion]:
    assert scope.project_id is not None
    paper_id = uuid4()
    version_id = uuid4()
    digest = "a" * 64
    aggregate = Paper(
        paper_id,
        scope.organization_id,
        scope.project_id,
        digest,
        version_id,
        "active",
        1,
        now,
        now,
    )
    version = PaperVersion(
        version_id,
        scope.organization_id,
        scope.project_id,
        paper_id,
        "object-1",
        "paper.pdf",
        "application/pdf",
        3,
        digest,
        uuid4(),
        now,
    )
    return aggregate, version


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


def test_project_resources_require_exact_organization_and_project_scope(uow_factory, clock) -> None:
    principal = actor()
    scope = TenantScope(uuid4(), uuid4())
    other_project = TenantScope(scope.organization_id, uuid4())
    other_organization = TenantScope(uuid4(), scope.project_id)
    organization_only = TenantScope(scope.organization_id)
    aggregate = project(scope, clock())
    manuscript, manuscript_version = paper(scope, clock())
    review = job(scope, clock())
    event = ReviewEvent(
        uuid4(), scope.organization_id, scope.project_id, review.id, 1, "queued", 1, {}, clock()
    )
    with uow_factory(principal) as uow:
        uow.projects.add(scope, aggregate)
        uow.papers.add(scope, manuscript, manuscript_version)
        uow.review_jobs.add(scope, review)
        uow.review_jobs.append_event(scope, event)
        uow.commit()

    for hidden_scope in (organization_only, other_project, other_organization):
        with uow_factory(principal) as uow:
            assert uow.projects.get(hidden_scope) is None
            assert tuple(uow.projects.list(hidden_scope)) == ()
            assert uow.papers.get(hidden_scope, manuscript.id) is None
            assert tuple(uow.papers.list(hidden_scope)) == ()
            assert uow.papers.find_by_content_digest(hidden_scope, manuscript.content_sha256) is None
            assert uow.review_jobs.get(hidden_scope, review.id) is None
            assert tuple(uow.review_jobs.list(hidden_scope)) == ()
            assert uow.review_jobs.list_events(hidden_scope, review.id) == ()

    with uow_factory(principal) as uow:
        with pytest.raises(NotFound):
            uow.review_jobs.add(organization_only, replace(review, id=uuid4()))


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


@pytest.mark.parametrize("replacement_version", [1, 3, 9])
def test_optimistic_save_requires_exactly_one_version_increment(
    uow_factory, clock, replacement_version
) -> None:
    principal = actor()
    scope = TenantScope(uuid4(), uuid4())
    original_project = project(scope, clock())
    original_job = job(scope, clock())
    membership = ProjectMembership(
        uuid4(),
        scope.organization_id,
        scope.project_id,
        uuid4(),
        Role.REVIEWER,
        "active",
        1,
        clock(),
        clock(),
    )
    with uow_factory(principal) as uow:
        uow.projects.add(scope, original_project)
        uow.review_jobs.add(scope, original_job)
        uow.projects.save_membership(scope, membership, expected_version=None)
        uow.commit()

    with uow_factory(principal) as uow:
        with pytest.raises(StaleVersion):
            uow.projects.save(
                scope, replace(original_project, version=replacement_version), expected_version=1
            )
        with pytest.raises(StaleVersion):
            uow.review_jobs.save(
                scope, replace(original_job, version=replacement_version), expected_version=1
            )
        with pytest.raises(StaleVersion):
            uow.projects.save_membership(
                scope, replace(membership, version=replacement_version), expected_version=1
            )


def test_creates_require_initial_version_and_paper_revision_progresses_one_step(uow_factory, clock) -> None:
    principal = actor()
    scope = TenantScope(uuid4(), uuid4())
    manuscript, initial_version = paper(scope, clock())
    with uow_factory(principal) as uow:
        with pytest.raises(StaleVersion):
            uow.projects.add(scope, project(scope, clock(), version=2))
        with pytest.raises(StaleVersion):
            uow.review_jobs.add(scope, job(scope, clock(), version=2))
        uow.papers.add(scope, manuscript, initial_version)
        with pytest.raises(StaleVersion):
            uow.papers.add_version(
                scope, replace(initial_version, id=uuid4(), revision=3), expected_paper_version=1
            )


def test_paper_versions_advance_aggregate_pointer_version_and_timestamp(uow_factory, clock) -> None:
    principal = actor()
    scope = TenantScope(uuid4(), uuid4())
    manuscript, initial_version = paper(scope, clock())
    with uow_factory(principal) as uow:
        uow.papers.add(scope, manuscript, initial_version)
        uow.commit()

    clock.now += timedelta(seconds=1)
    revision_two = replace(initial_version, id=uuid4(), revision=2, created_at=clock())
    with uow_factory(principal) as uow:
        uow.papers.add_version(scope, revision_two, expected_paper_version=1)
        uow.commit()
    with uow_factory(principal) as uow:
        assert uow.papers.get(scope, manuscript.id) == replace(
            manuscript, current_version_id=revision_two.id, version=2, updated_at=revision_two.created_at
        )

    clock.now += timedelta(seconds=1)
    revision_three = replace(revision_two, id=uuid4(), revision=3, created_at=clock())
    with uow_factory(principal) as uow:
        uow.papers.add_version(scope, revision_three, expected_paper_version=2)
        uow.commit()
    with uow_factory(principal) as uow:
        assert uow.papers.get(scope, manuscript.id) == replace(
            manuscript,
            current_version_id=revision_three.id,
            version=3,
            updated_at=revision_three.created_at,
        )


def test_failed_paper_version_additions_leave_aggregate_and_versions_unchanged(uow_factory, clock) -> None:
    principal = actor()
    scope = TenantScope(uuid4(), uuid4())
    manuscript, initial_version = paper(scope, clock())
    with uow_factory(principal) as uow:
        uow.papers.add(scope, manuscript, initial_version)
        uow.commit()

    candidates = (
        (replace(initial_version, id=uuid4(), revision=2), 0),
        (replace(initial_version, id=uuid4(), revision=3), 1),
        (replace(initial_version, id=uuid4(), revision=2), 2),
    )
    for candidate, expected_version in candidates:
        with uow_factory(principal) as uow:
            with pytest.raises(StaleVersion):
                uow.papers.add_version(scope, candidate, expected_paper_version=expected_version)
            uow.commit()
        with uow_factory(principal) as uow:
            assert uow.papers.get(scope, manuscript.id) == manuscript
            assert uow.papers.get_version(scope, candidate.id) is None

    cross_scope = TenantScope(uuid4(), scope.project_id)
    candidate = replace(initial_version, id=uuid4(), revision=2)
    with uow_factory(principal) as uow:
        with pytest.raises(NotFound):
            uow.papers.add_version(cross_scope, candidate, expected_paper_version=1)
        uow.commit()
    with uow_factory(principal) as uow:
        assert uow.papers.get(scope, manuscript.id) == manuscript
        assert uow.papers.get_version(scope, candidate.id) is None


def test_optimistic_save_rejects_a_lower_replacement_version(uow_factory, clock) -> None:
    principal = actor()
    scope = TenantScope(uuid4(), uuid4())
    original = project(scope, clock())
    version_two = replace(original, version=2)
    with uow_factory(principal) as uow:
        uow.projects.add(scope, original)
        uow.projects.save(scope, version_two, expected_version=1)
        uow.commit()
    with uow_factory(principal) as uow:
        with pytest.raises(StaleVersion):
            uow.projects.save(scope, original, expected_version=2)


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


@pytest.mark.parametrize(
    "replacement",
    [
        {"id": uuid4()},
        {"actor_id": uuid4()},
        {"operation": "project.delete"},
        {"idempotency_key": "changed-key"},
        {"payload_digest": "f" * 64},
        {"created_at": "changed"},
    ],
)
def test_command_completion_cannot_replace_reserved_identity(uow_factory, clock, replacement) -> None:
    principal = actor()
    scope = TenantScope(uuid4(), uuid4())
    record = CommandRecord(
        uuid4(),
        scope.organization_id,
        principal.actor_id,
        "project.create",
        "same-key",
        "a" * 64,
        clock(),
        project_id=scope.project_id,
    )
    completed = replace(record, response_status=201, response_body={}, completed_at=clock())
    if replacement.get("created_at") == "changed":
        replacement = {"created_at": clock() + timedelta(seconds=1)}
    with uow_factory(principal) as uow:
        uow.commands.reserve(scope, record)
        with pytest.raises(IdempotencyConflict):
            uow.commands.complete(scope, replace(completed, **replacement))
        uow.commit()
    with uow_factory(principal) as uow:
        assert uow.commands.get(scope, principal.actor_id, record.operation, record.idempotency_key) == record
        uow.commands.complete(scope, completed)
        uow.rollback()
    with uow_factory(principal) as uow:
        assert uow.commands.get(scope, principal.actor_id, record.operation, record.idempotency_key) == record


def test_command_completion_requires_exact_scope_and_rollbacks_remain_invisible(uow_factory, clock) -> None:
    principal = actor()
    scope = TenantScope(uuid4(), uuid4())
    record = CommandRecord(
        uuid4(),
        scope.organization_id,
        principal.actor_id,
        "project.create",
        "same-key",
        "a" * 64,
        clock(),
        project_id=scope.project_id,
    )
    completed = replace(record, response_status=201, response_body={}, completed_at=clock())
    with uow_factory(principal) as uow:
        uow.commands.reserve(scope, record)
        uow.rollback()
    with uow_factory(principal) as uow:
        assert uow.commands.get(scope, principal.actor_id, record.operation, record.idempotency_key) is None
        uow.commands.reserve(scope, record)
        with pytest.raises(NotFound):
            uow.commands.complete(TenantScope(scope.organization_id, uuid4()), completed)
        uow.commit()
    with uow_factory(principal) as uow:
        assert uow.commands.get(scope, principal.actor_id, record.operation, record.idempotency_key) == record


def test_command_reservation_cannot_replace_reserved_identity(uow_factory, clock) -> None:
    principal = actor()
    scope = TenantScope(uuid4(), uuid4())
    record = CommandRecord(
        uuid4(),
        scope.organization_id,
        principal.actor_id,
        "project.create",
        "same-key",
        "a" * 64,
        clock(),
        project_id=scope.project_id,
    )
    with uow_factory(principal) as uow:
        uow.commands.reserve(scope, record)
        with pytest.raises(IdempotencyConflict):
            uow.commands.reserve(scope, replace(record, id=uuid4()))
        with pytest.raises(IdempotencyConflict):
            uow.commands.reserve_or_replay(scope, replace(record, id=uuid4()))
        uow.commit()
    with uow_factory(principal) as uow:
        assert uow.commands.get(scope, principal.actor_id, record.operation, record.idempotency_key) == record


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

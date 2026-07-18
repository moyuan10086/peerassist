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
    Artifact,
    BrowserSession,
    CommandRecord,
    ExternalIdentity,
    LegacyRegistration,
    ObjectDescriptor,
    OidcTransaction,
    Organization,
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
        "fast",
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
        f"object-{version_id}",
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
    revision_two = replace(
        initial_version,
        id=uuid4(),
        source_object_id=f"object-{uuid4()}",
        revision=2,
        created_at=clock(),
    )
    with uow_factory(principal) as uow:
        uow.papers.add_version(scope, revision_two, expected_paper_version=1)
        uow.commit()
    with uow_factory(principal) as uow:
        assert uow.papers.get(scope, manuscript.id) == replace(
            manuscript, current_version_id=revision_two.id, version=2, updated_at=revision_two.created_at
        )

    clock.now += timedelta(seconds=1)
    revision_three = replace(
        revision_two,
        id=uuid4(),
        source_object_id=f"object-{uuid4()}",
        revision=3,
        created_at=clock(),
    )
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
        candidate = replace(record, id=uuid4(), created_at=clock() + timedelta(microseconds=uuid4().int % 1000))
        with uow_factory(principal) as uow:
            existing = uow.commands.reserve_or_replay(scope, candidate)
            if existing.completed_at is None:
                uow.commands.complete(
                    scope,
                    replace(
                        candidate,
                        response_status=201,
                        response_body={"id": str(command_id)},
                        completed_at=clock(),
                    ),
                )
                existing = uow.commands.reserve_or_replay(scope, candidate)
            uow.commit()
            return existing

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = tuple(executor.map(lambda _: execute_or_replay(), range(16)))
    assert all(result == results[0] for result in results)
    winner = results[0]
    assert winner.response_status == completed.response_status
    assert winner.response_body == completed.response_body
    assert winner.completed_at == completed.completed_at

    with uow_factory(principal) as uow:
        changed = replace(
            completed,
            id=uuid4(),
            created_at=clock() + timedelta(seconds=1),
            payload_digest=canonical_json_digest({"name": "Beta"}),
        )
        with pytest.raises(IdempotencyConflict):
            uow.commands.reserve_or_replay(scope, changed)


def test_completed_command_result_is_frozen_but_identical_retry_is_idempotent(uow_factory, clock) -> None:
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
    completed = replace(record, response_status=201, response_body={"result": "first"}, completed_at=clock())
    with uow_factory(principal) as uow:
        uow.commands.reserve(scope, record)
        uow.commands.complete(scope, completed)
        uow.commands.complete(scope, completed)
        for changed in (
            replace(completed, response_status=202),
            replace(completed, response_body={"result": "second"}),
            replace(completed, completed_at=clock() + timedelta(seconds=1)),
        ):
            with pytest.raises(IdempotencyConflict):
                uow.commands.complete(scope, changed)
        uow.commit()
    with uow_factory(principal) as uow:
        assert uow.commands.reserve_or_replay(scope, record) == completed


def test_concurrent_different_command_completions_only_first_result_wins(uow_factory, clock) -> None:
    principal = actor()
    scope = TenantScope(uuid4(), uuid4())
    record = CommandRecord(
        uuid4(),
        scope.organization_id,
        principal.actor_id,
        "project.create",
        "race-key",
        "a" * 64,
        clock(),
        project_id=scope.project_id,
    )
    first = replace(record, response_status=201, response_body={"winner": "first"}, completed_at=clock())
    second = replace(record, response_status=202, response_body={"winner": "second"}, completed_at=clock())
    with uow_factory(principal) as uow:
        uow.commands.reserve(scope, record)
        uow.commit()

    def complete(candidate: CommandRecord) -> CommandRecord | None:
        try:
            with uow_factory(principal) as uow:
                uow.commands.complete(scope, candidate)
                uow.commit()
                return candidate
        except IdempotencyConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(complete, (first, second)))
    assert sum(outcome is not None for outcome in outcomes) == 1
    winner = next(outcome for outcome in outcomes if outcome is not None)
    with uow_factory(principal) as uow:
        assert uow.commands.reserve_or_replay(scope, record) == winner


@pytest.mark.parametrize(
    "replacement",
    [
        {"actor_id": uuid4()},
        {"operation": "project.delete"},
        {"idempotency_key": "changed-key"},
        {"payload_digest": "f" * 64},
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


def test_command_retry_ignores_generated_presentation_fields(uow_factory, clock) -> None:
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
    retry = replace(record, id=uuid4(), created_at=clock() + timedelta(seconds=1))
    with uow_factory(principal) as uow:
        uow.commands.reserve(scope, record)
        uow.commands.reserve(scope, retry)
        assert uow.commands.reserve_or_replay(scope, retry) == record
        uow.commands.complete(
            scope,
            replace(retry, response_status=201, response_body={"winner": "stored"}, completed_at=clock()),
        )
        uow.commit()
    with uow_factory(principal) as uow:
        stored = uow.commands.get(scope, principal.actor_id, record.operation, record.idempotency_key)
        assert stored == replace(
            record,
            response_status=201,
            response_body={"winner": "stored"},
            completed_at=clock(),
        )


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


def test_primary_create_paths_reject_conflicting_duplicate_ids(uow_factory, clock) -> None:
    principal = actor()
    scope = TenantScope(uuid4(), uuid4())
    user = User(uuid4(), "active", "Ada", clock(), clock())
    aggregate = project(scope, clock())
    manuscript, version = paper(scope, clock())
    review = job(scope, clock())
    artifact = Artifact(
        uuid4(),
        scope.organization_id,
        scope.project_id,
        review.id,
        "report",
        ObjectDescriptor("object-1", 3, "a" * 64, "application/pdf"),
        "available",
        clock(),
    )
    registration = LegacyRegistration(
        uuid4(),
        scope.organization_id,
        scope.project_id,
        "review_job",
        "legacy-1",
        "b" * 64,
        "read_only",
        1,
        clock(),
    )
    organization_scope = TenantScope(scope.organization_id)
    organization = Organization(scope.organization_id, "alpha", "Alpha", "active", 1, clock(), clock())
    other_org_id = uuid4()
    with uow_factory(principal) as uow:
        uow.users.add(user)
        uow.organizations.add(organization_scope, organization)
        uow.projects.add(scope, aggregate)
        uow.papers.add(scope, manuscript, version)
        uow.review_jobs.add(scope, review)
        uow.artifacts.add(scope, artifact)
        uow.legacy_registrations.add(scope, registration)
        for operation in (
            lambda: uow.users.add(replace(user, display_name="Grace")),
            lambda: uow.organizations.add(organization_scope, replace(organization, name="Changed")),
            lambda: uow.organizations.add(
                TenantScope(other_org_id),
                replace(organization, id=other_org_id, name="Other"),
            ),
            lambda: uow.projects.add(scope, replace(aggregate, name="Changed")),
            lambda: uow.papers.add(scope, replace(manuscript, status="changed"), version),
            lambda: uow.review_jobs.add(scope, replace(review, status="changed")),
            lambda: uow.artifacts.add(scope, replace(artifact, status="changed")),
            lambda: uow.artifacts.add(scope, replace(artifact, id=uuid4())),
            lambda: uow.legacy_registrations.add(scope, replace(registration, opaque_locator="legacy-2")),
        ):
            with pytest.raises(ValueError, match="already exists"):
                operation()


def test_paper_business_and_version_keys_cannot_overwrite_existing_records(uow_factory, clock) -> None:
    principal = actor()
    scope = TenantScope(uuid4(), uuid4())
    first_paper, first_version = paper(scope, clock())
    second_paper, second_version = paper(scope, clock())
    second_paper = replace(second_paper, content_sha256="c" * 64)
    second_version = replace(second_version, sha256="c" * 64)
    with uow_factory(principal) as uow:
        uow.papers.add(scope, first_paper, first_version)
        with pytest.raises(ValueError, match="already exists"):
            uow.papers.add(
                scope,
                replace(second_paper, content_sha256=first_paper.content_sha256),
                second_version,
            )
        uow.papers.add(scope, second_paper, second_version)
        uow.commit()
    conflicting = replace(second_version, id=first_version.id, revision=2)
    with uow_factory(principal) as uow:
        with pytest.raises(ValueError, match="already exists"):
            uow.papers.add_version(scope, conflicting, expected_paper_version=1)
        uow.commit()
    with uow_factory(principal) as uow:
        assert uow.papers.get(scope, second_paper.id) == second_paper
        assert uow.papers.get_version(scope, first_version.id) == first_version


def test_identity_session_and_oidc_create_paths_reject_conflicting_keys(uow_factory, clock) -> None:
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
        uow.users.add_identity(identity)
        with pytest.raises(ValueError, match="already exists"):
            uow.users.add_identity(replace(identity, id=uuid4()))
        with pytest.raises(ValueError, match="already exists"):
            uow.browser_sessions.save(replace(session, id=uuid4()))
        with pytest.raises(ValueError, match="already exists"):
            uow.browser_sessions.save(replace(session, session_digest="e" * 64))
        with pytest.raises(ValueError, match="already exists"):
            uow.oidc_transactions.add(replace(transaction, return_path="/other"))
        with pytest.raises(ValueError, match="already exists"):
            uow.oidc_transactions.add(replace(transaction, id=uuid4()))


def test_oidc_consume_rejects_forged_replacement_without_partial_write(uow_factory, clock) -> None:
    principal = actor()
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
        uow.oidc_transactions.add(transaction)
        uow.commit()
    forged = replace(transaction, state_digest="e" * 64, consumed_at=clock())
    with uow_factory(principal) as uow:
        with pytest.raises(ValueError, match="identity"):
            uow.oidc_transactions.consume(forged)
        uow.commit()
    with uow_factory(principal) as uow:
        assert uow.oidc_transactions.get_for_update(transaction.id) == transaction

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from peerassist.platform.adapters.memory import MemoryUnitOfWorkFactory
from peerassist.platform.errors import Forbidden, IdempotencyConflict, NotFound
from peerassist.platform.models import (
    Actor,
    ActorKind,
    Organization,
    Paper,
    PaperVersion,
    Project,
    ProjectMembership,
    Role,
    TenantScope,
    User,
)
from peerassist.platform.services.reviews import (
    ChangeReviewJob,
    CreateReviewJob,
    RecordReviewDecision,
    ReviewService,
)


def _seed() -> tuple[ReviewService, MemoryUnitOfWorkFactory, dict[str, Actor], Project, PaperVersion]:
    now = datetime(2026, 7, 18, 16, 0, tzinfo=UTC)
    factory = MemoryUnitOfWorkFactory(clock=lambda: now)
    actors = {
        name: Actor(uuid4(), ActorKind.USER)
        for name in ("owner", "reviewer", "viewer", "outsider")
    }
    organization = Organization(uuid4(), "reviews", "Reviews", "active", 1, now, now)
    project = Project(uuid4(), organization.id, "Review project", "active", 1, now, now)
    paper_id = uuid4()
    version = PaperVersion(
        uuid4(), organization.id, project.id, paper_id, "paper/source", "paper.pdf",
        "application/pdf", 9, "a" * 64, actors["reviewer"].actor_id, now,
    )
    paper = Paper(
        paper_id, organization.id, project.id, version.sha256, version.id,
        "active", 1, now, now,
    )
    with factory(actors["owner"]) as uow:
        for name, actor in actors.items():
            uow.users.add(User(actor.actor_id, "active", name, now, now))
        uow.organizations.add(TenantScope(organization.id), organization)
        uow.projects.add(project.scope, project)
        for name, role in (
            ("owner", Role.PROJECT_OWNER),
            ("reviewer", Role.REVIEWER),
            ("viewer", Role.VIEWER),
        ):
            uow.projects.save_membership(
                project.scope,
                ProjectMembership(
                    uuid4(), organization.id, project.id, actors[name].actor_id,
                    role, "active", 1, now, now,
                ),
                None,
            )
        uow.papers.add(project.scope, paper, version)
        uow.commit()
    return ReviewService(factory, clock=lambda: now), factory, actors, project, version


def _create(project: Project, version: PaperVersion, *, key: str = "create") -> CreateReviewJob:
    return CreateReviewJob(project.id, version.id, "full", key, f"request-{key}")


def test_create_review_is_idempotent_and_atomically_enqueues_first_stage() -> None:
    service, factory, actors, project, paper_version = _seed()

    created = service.create(actors["reviewer"], _create(project, paper_version))
    replay = service.create(actors["reviewer"], _create(project, paper_version))

    assert replay == created
    assert created.status == "queued" and created.stage == "queued" and created.attempt == 1
    assert [event.event_type for event in service.events(actors["viewer"], project.id, created.id)] == [
        "review_job.created"
    ]
    with factory(actors["reviewer"]) as uow:
        work = tuple(uow.work_items._state.work_items.values())
        assert len(work) == 1 and work[0].job_id == created.id
        assert len(tuple(uow.outbox.claim_batch(project.scope, 10))) == 1


def test_review_permissions_and_changed_idempotency_payload_fail_closed() -> None:
    service, _, actors, project, paper_version = _seed()
    service.create(actors["reviewer"], _create(project, paper_version, key="fixed"))

    with pytest.raises(IdempotencyConflict):
        service.create(
            actors["reviewer"],
            CreateReviewJob(project.id, paper_version.id, "fast", "fixed", "request-fixed"),
        )
    with pytest.raises(Forbidden):
        service.create(actors["viewer"], _create(project, paper_version, key="viewer"))
    with pytest.raises(NotFound):
        service.list(actors["outsider"], project.id)


def test_cancel_then_retry_records_ordered_events_and_new_work() -> None:
    service, factory, actors, project, paper_version = _seed()
    created = service.create(actors["reviewer"], _create(project, paper_version))

    cancelled = service.cancel(
        actors["reviewer"],
        ChangeReviewJob(project.id, created.id, created.version, "cancel", "cancel-request"),
    )
    retried = service.retry(
        actors["reviewer"],
        ChangeReviewJob(project.id, created.id, cancelled.version, "retry", "retry-request"),
    )

    assert cancelled.status == "cancelled"
    assert retried.status == "queued" and retried.attempt == 2
    assert [event.event_type for event in service.events(actors["viewer"], project.id, created.id)] == [
        "review_job.created",
        "review_job.cancelled",
        "review_job.retried",
    ]
    with factory(actors["reviewer"]) as uow:
        work = tuple(uow.work_items._state.work_items.values())
        assert len(work) == 2


def test_reviewer_records_bound_decision_but_only_owner_finalizes() -> None:
    service, _, actors, project, paper_version = _seed()
    created = service.create(actors["reviewer"], _create(project, paper_version))

    decided = service.record_decision(
        actors["reviewer"],
        RecordReviewDecision(
            project.id, created.id, "concern", "concern-1", "confirm",
            created.version, "decision", "decision-request",
        ),
    )
    with pytest.raises(Forbidden):
        service.finalize(
            actors["reviewer"],
            ChangeReviewJob(project.id, created.id, decided.version, "finalize-r", "finalize-r"),
        )
    finalized = service.finalize(
        actors["owner"],
        ChangeReviewJob(project.id, created.id, decided.version, "finalize-o", "finalize-o"),
    )

    assert finalized.status == "completed" and finalized.stage == "completed"
    assert [event.event_type for event in service.events(actors["viewer"], project.id, created.id)][
        -2:
    ] == ["review_job.decision_recorded", "review_job.finalized"]

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from peerassist.platform.adapters.postgres import PostgresUnitOfWorkFactory
from peerassist.platform.adapters.postgres_schema import metadata
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
from peerassist.platform.services.reviews import ChangeReviewJob, CreateReviewJob, ReviewService

pytestmark = pytest.mark.requires_docker


def test_postgres_review_create_cancel_retry_is_atomic(request: pytest.FixtureRequest) -> None:
    raw_url = os.environ.get("PEERASSIST_TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("PEERASSIST_TEST_DATABASE_URL is not configured")
    database_url = raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
    config = Config(request.config.rootpath / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")
    engine = create_engine(database_url, pool_pre_ping=True)
    with engine.begin() as connection:
        tables = ", ".join(
            f'"{table.name}"'
            for table in reversed(metadata.sorted_tables)
            if table.name != "schema_metadata"
        )
        connection.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
    now = datetime(2026, 7, 18, 17, 0, tzinfo=UTC)
    factory = PostgresUnitOfWorkFactory(engine, clock=lambda: now)
    actor = Actor(uuid4(), ActorKind.USER)
    organization = Organization(uuid4(), "review-command", "Review command", "active", 1, now, now)
    project = Project(uuid4(), organization.id, "Review", "active", 1, now, now)
    paper_id = uuid4()
    version = PaperVersion(
        uuid4(), organization.id, project.id, paper_id, "source", "paper.pdf",
        "application/pdf", 1, "a" * 64, actor.actor_id, now,
    )
    paper = Paper(
        paper_id, organization.id, project.id, version.sha256, version.id,
        "active", 1, now, now,
    )
    with factory(actor) as uow:
        uow.users.add(User(actor.actor_id, "active", "Reviewer", now, now))
        uow.organizations.add(TenantScope(organization.id), organization)
        uow.projects.add(project.scope, project)
        uow.projects.save_membership(
            project.scope,
            ProjectMembership(
                uuid4(), organization.id, project.id, actor.actor_id,
                Role.REVIEWER, "active", 1, now, now,
            ),
            None,
        )
        uow.papers.add(project.scope, paper, version)
        uow.commit()
    service = ReviewService(factory, clock=lambda: now)
    try:
        created = service.create(
            actor,
            CreateReviewJob(project.id, version.id, "full", "create", "request-create"),
        )
        cancelled = service.cancel(
            actor,
            ChangeReviewJob(project.id, created.id, created.version, "cancel", "request-cancel"),
        )
        retried = service.retry(
            actor,
            ChangeReviewJob(project.id, created.id, cancelled.version, "retry", "request-retry"),
        )
        with engine.connect() as connection:
            counts = {
                table: connection.scalar(text(f"SELECT count(*) FROM {table}"))
                for table in (
                    "review_jobs", "review_attempts", "review_events", "work_items",
                    "commands", "audit_events", "outbox_events",
                )
            }
        assert retried.attempt == 2
        assert counts == {
            "review_jobs": 1,
            "review_attempts": 2,
            "review_events": 3,
            "work_items": 2,
            "commands": 3,
            "audit_events": 3,
            "outbox_events": 3,
        }
    finally:
        engine.dispose()

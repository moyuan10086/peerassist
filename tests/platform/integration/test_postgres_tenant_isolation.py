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
from peerassist.platform.errors import NotFound
from peerassist.platform.models import (
    Actor,
    ActorKind,
    Organization,
    OrganizationMembership,
    Project,
    ProjectMembership,
    Role,
    TenantScope,
    User,
)
from peerassist.platform.services.memberships import (
    MembershipService,
    UpdateProjectMembership,
)

pytestmark = pytest.mark.requires_docker


@pytest.fixture
def tenant_matrix(request: pytest.FixtureRequest):
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
    factory = PostgresUnitOfWorkFactory(engine)
    now = datetime(2026, 7, 18, 14, 0, tzinfo=UTC)
    admin_a = Actor(uuid4(), ActorKind.USER)
    reviewer_a = Actor(uuid4(), ActorKind.USER)
    admin_b = Actor(uuid4(), ActorKind.USER)
    organization_a = Organization(uuid4(), "postgres-a", "Postgres A", "active", 1, now, now)
    organization_b = Organization(uuid4(), "postgres-b", "Postgres B", "active", 1, now, now)
    project_a = Project(uuid4(), organization_a.id, "Review", "active", 1, now, now)
    project_b = Project(uuid4(), organization_b.id, "Review", "active", 1, now, now)
    reviewer_membership = ProjectMembership(
        uuid4(), organization_a.id, project_a.id, reviewer_a.actor_id,
        Role.REVIEWER, "active", 1, now, now,
    )
    with factory(admin_a) as uow:
        for name, actor in (("admin-a", admin_a), ("reviewer-a", reviewer_a), ("admin-b", admin_b)):
            uow.users.add(User(actor.actor_id, "active", name, now, now))
        for organization, admin in ((organization_a, admin_a), (organization_b, admin_b)):
            scope = TenantScope(organization.id)
            uow.organizations.add(scope, organization)
            uow.organizations.save_membership(
                scope,
                OrganizationMembership(
                    uuid4(), organization.id, admin.actor_id,
                    Role.ORGANIZATION_ADMIN, "active", 1, now, now,
                ),
                None,
            )
        uow.projects.add(project_a.scope, project_a)
        uow.projects.add(project_b.scope, project_b)
        uow.projects.save_membership(project_a.scope, reviewer_membership, None)
        uow.commit()
    yield factory, admin_a, reviewer_a, admin_b, organization_a, organization_b, project_a, project_b, reviewer_membership
    engine.dispose()


def test_postgres_scope_predicates_and_revocation_are_authoritative(tenant_matrix) -> None:
    (
        factory, admin_a, reviewer_a, admin_b, _organization_a, organization_b,
        project_a, project_b, reviewer_membership,
    ) = tenant_matrix
    with factory(admin_b) as uow:
        assert uow.projects.get(TenantScope(organization_b.id, project_a.id)) is None
        assert uow.projects.list(TenantScope(organization_b.id, project_a.id)) == ()
        assert uow.projects.list_memberships(
            TenantScope(organization_b.id, project_a.id)
        ) == ()

    service = MembershipService(factory)
    assert service.get_project(reviewer_a, project_a.id) == project_a
    with pytest.raises(NotFound):
        service.get_project(admin_b, project_a.id)
    with pytest.raises(NotFound):
        service.get_project(admin_a, project_b.id)
    service.update_project_membership(
        admin_a,
        UpdateProjectMembership(
            project_a.id,
            reviewer_membership.id,
            reviewer_membership.role,
            "revoked",
            reviewer_membership.version,
            "postgres-revoke-reviewer",
            "request-postgres-revoke-reviewer",
        ),
    )
    with pytest.raises(NotFound):
        service.get_project(reviewer_a, project_a.id)

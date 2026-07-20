from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from services.api.app import create_app
from services.api.composition import PlatformDependencies
from services.api.routes.organizations import require_management_actor

from common.config import PlatformSettings
from peerassist.platform.models import (
    Actor,
    ActorKind,
    Organization,
    OrganizationMembership,
    Role,
    TenantScope,
    User,
)


def _settings() -> PlatformSettings:
    return PlatformSettings(
        environment="test",
        database_url="postgresql+psycopg://test:test@db/peerassist",
        oidc_issuer="http://identity.test/realms/peerassist",
        oidc_audience="peerassist-api",
        s3_endpoint="http://objects.test",
        s3_bucket="peerassist-test",
        public_base_url="http://api.test",
        allowed_origins=["http://api.test"],
        internal_legacy_audience="peerassist-legacy",
    )


def test_management_routes_are_thin_safe_and_exclude_operator_operations() -> None:
    now = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
    dependencies = PlatformDependencies.for_test()
    admin = Actor(uuid4(), ActorKind.USER)
    target = Actor(uuid4(), ActorKind.USER)
    organization = Organization(uuid4(), "api-org", "API Org", "active", 1, now, now)
    with dependencies.uow_factory(admin) as uow:
        uow.users.add(User(admin.actor_id, "active", "Admin", now, now))
        uow.users.add(User(target.actor_id, "active", "Target", now, now))
        uow.organizations.add(TenantScope(organization.id), organization)
        uow.organizations.save_membership(
            TenantScope(organization.id),
            OrganizationMembership(
                uuid4(), organization.id, admin.actor_id, Role.ORGANIZATION_ADMIN, "active", 1, now, now
            ),
            None,
        )
        uow.commit()

    app = create_app(_settings(), dependencies)
    app.dependency_overrides[require_management_actor] = lambda: admin
    with TestClient(app) as client:
        organizations = client.get("/api/v1/organizations")
        blank_project = client.post(
            f"/api/v1/organizations/{organization.id}/projects",
            headers={"Idempotency-Key": "api-blank-project"},
            json={"name": " "},
        )
        oversized_project = client.post(
            f"/api/v1/organizations/{organization.id}/projects",
            headers={"Idempotency-Key": "api-oversized-project"},
            json={"name": "x" * 256},
        )
        project = client.post(
            f"/api/v1/organizations/{organization.id}/projects",
            headers={"Idempotency-Key": "api-create-project"},
            json={"name": "API Project"},
        )
        project_id = project.json()["id"]
        granted = client.post(
            f"/api/v1/projects/{project_id}/members",
            headers={"Idempotency-Key": "api-grant-member"},
            json={"user_id": str(target.actor_id), "role": "reviewer", "expected_version": None},
        )
        membership_id = granted.json()["id"]
        changed = client.patch(
            f"/api/v1/projects/{project_id}/members/{membership_id}",
            headers={"Idempotency-Key": "api-change-member"},
            json={"role": "viewer", "status": "active", "expected_version": 1},
        )
        stale = client.patch(
            f"/api/v1/projects/{project_id}/members/{membership_id}",
            headers={"Idempotency-Key": "api-stale-member"},
            json={"role": "reviewer", "status": "active", "expected_version": 1},
        )
        audit = client.get(
            f"/api/v1/organizations/{organization.id}/audit-events",
            params={"project_id": project_id},
        )
        absent = {
            path: client.post(path)
            for path in (
                "/api/v1/organizations",
                "/api/v1/identities/disable",
                "/api/v1/identities/unlink",
                "/api/v1/legacy/register",
            )
        }

    assert organizations.status_code == 200
    assert blank_project.status_code == 422
    assert oversized_project.status_code == 422
    assert organizations.json()[0]["id"] == str(organization.id)
    assert project.status_code == 201
    assert granted.status_code == 201
    assert changed.status_code == 200 and changed.json()["version"] == 2
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale_version"
    assert audit.status_code == 200
    assert all(event["project_id"] == project_id for event in audit.json())
    assert all(response.status_code in {404, 405} for response in absent.values())


def test_management_dependency_fails_closed_and_registry_is_deterministic() -> None:
    from services.api.routes import platform_routers

    app = create_app(_settings(), PlatformDependencies.for_test())
    with TestClient(app) as client:
        response = client.get("/api/v1/organizations", headers={"X-User-ID": str(uuid4())})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"
    assert tuple(router.tags for router in platform_routers()) == (
        ["system"],
        ["authentication"],
        ["organizations"],
        ["projects"],
        ["papers"],
        ["review-jobs"],
        ["artifacts"],
        ["model-settings"],
        ["legacy-compatibility"],
    )

    service_actor = Actor(admin_id := uuid4(), ActorKind.SERVICE)
    now = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
    dependencies = PlatformDependencies.for_test()
    organization = Organization(uuid4(), "service-collision", "Service Collision", "active", 1, now, now)
    with dependencies.uow_factory(Actor(admin_id, ActorKind.USER)) as uow:
        uow.users.add(User(admin_id, "active", "Admin", now, now))
        uow.organizations.add(TenantScope(organization.id), organization)
        uow.organizations.save_membership(
            TenantScope(organization.id),
            OrganizationMembership(
                uuid4(), organization.id, admin_id, Role.ORGANIZATION_ADMIN, "active", 1, now, now
            ),
            None,
        )
        uow.commit()
    app = create_app(_settings(), dependencies)

    @app.middleware("http")
    async def install_service_actor(request, call_next):
        request.state.management_actor = service_actor
        return await call_next(request)

    with TestClient(app) as client:
        collision = client.get(f"/api/v1/organizations/{organization.id}/members")
    assert collision.status_code == 401
    assert collision.json()["error"]["code"] == "authentication_required"

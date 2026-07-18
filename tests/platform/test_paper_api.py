from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from services.api.app import create_app
from services.api.composition import PlatformDependencies
from services.api.routes.organizations import require_management_actor
from services.api.routes.papers import _source_headers

from common.config import PlatformSettings
from peerassist.platform.models import (
    Actor,
    ActorKind,
    Organization,
    PaperVersion,
    Project,
    ProjectMembership,
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


def _app():
    now = datetime(2026, 7, 18, 15, 0, tzinfo=UTC)
    dependencies = PlatformDependencies.for_test()
    reviewer = Actor(uuid4(), ActorKind.USER)
    outsider = Actor(uuid4(), ActorKind.USER)
    organization = Organization(uuid4(), "paper-api", "Paper API", "active", 1, now, now)
    project = Project(uuid4(), organization.id, "Paper API", "active", 1, now, now)
    with dependencies.uow_factory(reviewer) as uow:
        for actor, name in ((reviewer, "reviewer"), (outsider, "outsider")):
            uow.users.add(User(actor.actor_id, "active", name, now, now))
        uow.organizations.add(TenantScope(organization.id), organization)
        uow.projects.add(project.scope, project)
        uow.projects.save_membership(
            project.scope,
            ProjectMembership(
                uuid4(), organization.id, project.id, reviewer.actor_id,
                Role.REVIEWER, "active", 1, now, now,
            ),
            None,
        )
        uow.commit()
    app = create_app(_settings(), dependencies)
    app.dependency_overrides[require_management_actor] = lambda: reviewer
    return app, reviewer, outsider, project


def test_paper_upload_list_head_and_range_are_project_scoped() -> None:
    app, _, outsider, project = _app()
    content = b"%PDF-1.7\nsynthetic api fixture\n%%EOF\n"
    with TestClient(app) as client:
        uploaded = client.post(
            f"/api/v1/projects/{project.id}/papers",
            headers={"Idempotency-Key": "paper-api-upload"},
            files={"file": ("paper.pdf", content, "application/pdf")},
        )
        paper_id = uploaded.json()["paper"]["id"]
        listed = client.get(f"/api/v1/projects/{project.id}/papers")
        head = client.head(f"/api/v1/projects/{project.id}/papers/{paper_id}/source")
        ranged = client.get(
            f"/api/v1/projects/{project.id}/papers/{paper_id}/source",
            headers={"Range": "bytes=0-4"},
        )
        app.dependency_overrides[require_management_actor] = lambda: outsider
        hidden = client.get(f"/api/v1/projects/{project.id}/papers/{paper_id}/source")

    assert uploaded.status_code == 201
    assert listed.status_code == 200 and len(listed.json()) == 1
    assert head.status_code == 200 and head.headers["accept-ranges"] == "bytes"
    assert head.headers["etag"] == f'"{uploaded.json()["version"]["sha256"]}"'
    assert ranged.status_code == 206
    assert ranged.content == b"%PDF-"
    assert ranged.headers["content-range"] == f"bytes 0-4/{len(content)}"
    assert hidden.status_code == 404


def test_invalid_pdf_and_oversized_upload_have_stable_errors() -> None:
    app, _, _, project = _app()
    with TestClient(app) as client:
        invalid = client.post(
            f"/api/v1/projects/{project.id}/papers",
            headers={"Idempotency-Key": "invalid-pdf"},
            files={"file": ("paper.pdf", b"not-pdf", "application/pdf")},
        )
        oversized = client.post(
            f"/api/v1/projects/{project.id}/papers?maximum_size_bytes=8",
            headers={"Idempotency-Key": "oversized-pdf"},
            files={"file": ("paper.pdf", b"%PDF-1.7\nlarge", "application/pdf")},
        )

    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_upload"
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "payload_too_large"


def test_source_headers_accept_equivalent_utc_timezone_from_postgres() -> None:
    now = datetime(2026, 7, 18, 13, 0, tzinfo=ZoneInfo("UTC"))
    version = PaperVersion(
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        "paper/source",
        "paper.pdf",
        "application/pdf",
        1,
        "a" * 64,
        uuid4(),
        now,
    )

    assert _source_headers(version)["Last-Modified"].endswith("GMT")

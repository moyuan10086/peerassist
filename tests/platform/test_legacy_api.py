from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from services.api.app import create_app
from services.api.composition import PlatformDependencies
from services.api.routes.organizations import require_management_actor
from tests.platform.test_paper_api import _app, _settings

from peerassist.platform.adapters.legacy import LocalLegacyReader
from peerassist.platform.models import LegacyRegistration


def test_authorized_legacy_job_is_read_only_and_cross_tenant_hidden(tmp_path) -> None:
    app, reviewer, outsider, project = _app()
    locator = str(uuid4())
    job_dir = tmp_path / locator
    job_dir.mkdir()
    payload = {
        "id": locator,
        "paper_id": "b" * 64,
        "mode": "fast",
        "stage": "await_confirmation",
        "status": "awaiting_human_confirmation",
        "revision": 3,
        "created_at": "2026-07-18T18:00:00Z",
        "updated_at": "2026-07-18T19:00:00Z",
    }
    manifest = json.dumps(payload).encode()
    (job_dir / "job.json").write_bytes(manifest)
    registration = LegacyRegistration(
        uuid4(), project.organization_id, project.id, "m0_review_job", locator,
        hashlib.sha256(manifest).hexdigest(), "read_only", 1,
        datetime(2026, 7, 18, 19, tzinfo=UTC),
    )
    with app.state.dependencies.uow_factory(reviewer) as uow:
        uow.legacy_registrations.add(project.scope, registration)
        uow.commit()
    dependencies = app.state.dependencies
    wired = PlatformDependencies(
        dependencies.uow_factory,
        dependencies.identity_provider,
        dependencies.object_store,
        dependencies.session_service,
        dependencies.readiness_checks,
        dependencies.lifecycle_resources,
        legacy_reader=LocalLegacyReader(tmp_path),
    )
    app = create_app(_settings(), wired)
    app.dependency_overrides[require_management_actor] = lambda: reviewer

    with TestClient(app) as client:
        visible = client.get(
            f"/api/v1/projects/{project.id}/compat/{registration.id}"
        )
        mutation = client.post(
            f"/api/v1/projects/{project.id}/compat/{registration.id}", json={}
        )
        app.dependency_overrides[require_management_actor] = lambda: outsider
        hidden = client.get(f"/api/v1/projects/{project.id}/compat/{registration.id}")

    assert visible.status_code == 200 and visible.json()["read_only"] is True
    assert mutation.status_code == 405
    assert hidden.status_code == 404

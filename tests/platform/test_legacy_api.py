from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from services.api.app import create_app
from services.api.composition import PlatformDependencies
from services.api.routes.organizations import require_management_actor
from tests.platform.test_paper_api import _app, _settings

from peerassist.model_settings import ModelSettingsInput, save_model_settings
from peerassist.platform.adapters.legacy import LocalLegacyReader
from peerassist.platform.models import LegacyRegistration, Role


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


def test_owner_can_migrate_complete_legacy_workspace_once(tmp_path, monkeypatch) -> None:
    settings_path = tmp_path / "model-settings.json"
    monkeypatch.setenv("PEERASSIST_MODEL_SETTINGS_PATH", str(settings_path))
    stored = save_model_settings(
        ModelSettingsInput(
            "openai-compatible",
            "https://provider.example/v1",
            "review-model",
            "test-api-key",
            policy_version="peerassist.model-policy.v1",
        )
    )
    app, reviewer, _, project = _app()
    with app.state.dependencies.uow_factory(reviewer) as uow:
        membership = uow.projects.get_membership(project.scope, reviewer.actor_id)
        uow.projects.save_membership(
            project.scope,
            replace(membership, role=Role.PROJECT_OWNER, version=2),
            1,
        )
        uow.commit()

    content = b"%PDF-1.7\nlegacy migration api fixture\n%%EOF\n"
    digest = hashlib.sha256(content).hexdigest()
    locator = str(uuid4())
    job_dir = tmp_path / locator
    job_dir.mkdir()
    payload = {
        "id": locator,
        "paper_id": digest,
        "mode": "full",
        "stage": "await_confirmation",
        "status": "awaiting_human_confirmation",
        "revision": 2,
        "created_at": "2026-07-24T10:00:00Z",
        "updated_at": "2026-07-24T10:30:00Z",
        "metadata": {
            "workspace_migration": {
                "consent": {
                    "service": "model-review",
                    "status": "granted",
                    "provider_config_revision": stored.revision,
                    "policy_version": stored.policy_version,
                    "data_scope": {"paper_text": True, "evidence": True},
                    "decided_by": str(reviewer.actor_id),
                    "decided_at": "2026-07-24T10:00:00Z",
                },
                "document_sections": {
                    "overall_assessment": "Overall",
                    "major_issues": "Major",
                    "minor_issues": "Minor",
                    "revision_suggestions": "Suggestions",
                },
            }
        },
    }
    manifest = json.dumps(payload).encode()
    (job_dir / "job.json").write_bytes(manifest)
    registration = LegacyRegistration(
        uuid4(), project.organization_id, project.id, "m0_review_job", locator,
        hashlib.sha256(manifest).hexdigest(), "read_only", 1,
        datetime(2026, 7, 24, 10, 30, tzinfo=UTC),
    )
    with app.state.dependencies.uow_factory(reviewer) as uow:
        uow.legacy_registrations.add(project.scope, registration)
        uow.commit()
    dependencies = app.state.dependencies
    app = create_app(
        _settings(),
        PlatformDependencies(
            dependencies.uow_factory,
            dependencies.identity_provider,
            dependencies.object_store,
            dependencies.session_service,
            dependencies.readiness_checks,
            dependencies.lifecycle_resources,
            legacy_reader=LocalLegacyReader(tmp_path),
        ),
    )
    app.dependency_overrides[require_management_actor] = lambda: reviewer

    with TestClient(app) as client:
        uploaded = client.post(
            f"/api/v1/projects/{project.id}/papers",
            headers={"Idempotency-Key": "legacy-migration-paper"},
            files={"file": ("paper.pdf", content, "application/pdf")},
        )
        migrated = client.post(
            f"/api/v1/projects/{project.id}/compat/{registration.id}/migrate"
        )
        replay = client.post(
            f"/api/v1/projects/{project.id}/compat/{registration.id}/migrate"
        )

    assert uploaded.status_code == 201
    assert migrated.status_code == 200 and migrated.json()["status"] == "migrated"
    assert replay.json() == migrated.json()

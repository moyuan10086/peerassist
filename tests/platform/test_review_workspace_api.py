from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from services.api.app import create_app
from services.api.composition import PlatformDependencies
from tests.platform.test_paper_api import _app, _settings

from peerassist.platform.models import Artifact, Project, ProjectMembership, Role


def test_teacher_workspace_facade_uploads_reviews_and_edits_document() -> None:
    app, _, _, project = _app()
    content = b"%PDF-1.7\nworkspace facade fixture\n%%EOF\n"
    with TestClient(app) as client:
        initial = client.get("/api/v1/workspace")
        uploaded = client.post(
            "/api/v1/workspace/papers",
            headers={"Idempotency-Key": "workspace-paper"},
            files={"file": ("paper.pdf", content, "application/pdf")},
        )
        version_id = uploaded.json()["version"]["id"]
        created = client.post(
            "/api/v1/workspace/reviews",
            headers={"Idempotency-Key": "workspace-review"},
            json={"paper_version_id": version_id, "mode": "full"},
        )
        job_id = created.json()["id"]
        document = client.get(f"/api/v1/workspace/reviews/{job_id}/document")
        saved = client.put(
            f"/api/v1/workspace/reviews/{job_id}/document",
            headers={"Idempotency-Key": "workspace-document"},
            json={
                "expected_document_version": document.json()["document_version"],
                "blocks": document.json()["blocks"],
            },
        )
        pending = client.post(
            f"/api/v1/workspace/reviews/{job_id}/consents/model-review",
            headers={"Idempotency-Key": "workspace-consent-apply"},
            json={
                "action": "reapply",
                "expected_consent_version": 0,
                "provider_config_revision": 1,
                "policy_version": "peerassist.model-policy.v1",
                "data_scope": {"paper_text": True, "evidence": True},
            },
        )
        granted = client.post(
            f"/api/v1/workspace/reviews/{job_id}/consents/model-review",
            headers={"Idempotency-Key": "workspace-consent-grant"},
            json={
                "action": "grant",
                "expected_consent_version": pending.json()["version"],
                "provider_config_revision": 1,
                "policy_version": "peerassist.model-policy.v1",
                "data_scope": {"paper_text": True, "evidence": True},
            },
        )
        aggregate = client.get("/api/v1/workspace")

    assert initial.status_code == 200
    assert initial.json()["status"] == "ready"
    assert initial.json()["project"]["id"] == str(project.id)
    assert uploaded.status_code == 201
    assert created.status_code == 201
    assert document.status_code == 200 and len(document.json()["blocks"]) == 4
    assert saved.status_code == 200 and saved.json()["document_version"] == 2
    assert pending.status_code == 200 and pending.json()["status"] == "pending"
    assert granted.status_code == 200 and granted.json()["status"] == "granted"
    assert len(aggregate.json()["papers"]) == 1
    assert len(aggregate.json()["reviews"]) == 1


def test_teacher_workspace_requires_authentication() -> None:
    app = create_app(_settings(), PlatformDependencies.for_test())

    with TestClient(app) as client:
        response = client.get("/api/v1/workspace")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_teacher_workspace_exposes_evidence_and_finding_decision() -> None:
    app, reviewer, _, project = _app()
    with TestClient(app) as client:
        uploaded = client.post(
            "/api/v1/workspace/papers",
            headers={"Idempotency-Key": "evidence-paper"},
            files={
                "file": (
                    "paper.pdf",
                    b"%PDF-1.7\nevidence facade fixture\n%%EOF\n",
                    "application/pdf",
                )
            },
        )
        created = client.post(
            "/api/v1/workspace/reviews",
            headers={"Idempotency-Key": "evidence-review"},
            json={"paper_version_id": uploaded.json()["version"]["id"], "mode": "full"},
        )
        job_id = UUID(created.json()["id"])
        document = client.get(f"/api/v1/workspace/reviews/{job_id}/document").json()

        payload = {
            "schema_version": "peerassist.review_result.v1",
            "concerns": [
                {
                    "finding_lineage_id": "lineage-method",
                    "finding_id": "finding-method-v1",
                    "revision": 1,
                    "level": "major_concern",
                    "title": "Sampling method",
                    "author_action": "Explain the sampling limitations.",
                    "evidence_ids": ["evidence-page-3"],
                    "evidence": [
                        {
                            "id": "evidence-page-3",
                            "locator": "pdf:page:3",
                            "page": 3,
                            "text": "Convenience sampling was used.",
                        }
                    ],
                }
            ],
        }
        raw = json.dumps(payload).encode()
        store = app.state.dependencies.object_store
        upload_id = store.create_temporary(project.scope, len(raw))
        temporary = store.write_temporary(project.scope, upload_id, io.BytesIO(raw))
        descriptor = store.publish(
            project.scope, temporary, f"review-jobs/{job_id}/review_result.json"
        )
        store.delete_temporary(project.scope, upload_id)
        with app.state.dependencies.uow_factory(reviewer) as uow:
            uow.artifacts.add(
                project.scope,
                Artifact(
                    uuid4(), project.organization_id, project.id, job_id,
                    "review_result.json", descriptor, "available",
                    datetime(2026, 7, 24, 10, tzinfo=UTC),
                ),
            )
            uow.commit()

        evidence = client.get(f"/api/v1/workspace/reviews/{job_id}/evidence")
        decided = client.post(
            f"/api/v1/workspace/reviews/{job_id}/findings/lineage-method/decision",
            headers={"Idempotency-Key": "finding-accept"},
            json={
                "finding_id": "finding-method-v1",
                "finding_revision": 1,
                "action": "accept",
                "expected_review_version": created.json()["version"],
                "expected_document_version": document["document_version"],
                "last_decision_event_id": document["base_decision_event_id"],
            },
        )
        updated = client.get(f"/api/v1/workspace/reviews/{job_id}/document")

    assert evidence.status_code == 200
    assert evidence.json()["concerns"][0]["evidence"][0]["page"] == 3
    assert decided.status_code == 200
    assert decided.json()["document_version"] == 2
    assert any(block["finding_lineage_id"] == "lineage-method" for block in updated.json()["blocks"])


def test_teacher_workspace_review_lifecycle_aliases_project_routes() -> None:
    app, _, _, _ = _app()
    with TestClient(app) as client:
        uploaded = client.post(
            "/api/v1/workspace/papers",
            headers={"Idempotency-Key": "lifecycle-paper"},
            files={
                "file": (
                    "paper.pdf",
                    b"%PDF-1.7\nlifecycle facade fixture\n%%EOF\n",
                    "application/pdf",
                )
            },
        )
        created = client.post(
            "/api/v1/workspace/reviews",
            headers={"Idempotency-Key": "lifecycle-review"},
            json={"paper_version_id": uploaded.json()["version"]["id"], "mode": "fast"},
        )
        job_id = created.json()["id"]
        fetched = client.get(f"/api/v1/workspace/reviews/{job_id}")
        cancelled = client.post(
            f"/api/v1/workspace/reviews/{job_id}/cancel",
            headers={"Idempotency-Key": "lifecycle-cancel"},
            json={"expected_version": fetched.json()["version"]},
        )
        retried = client.post(
            f"/api/v1/workspace/reviews/{job_id}/retry",
            headers={"Idempotency-Key": "lifecycle-retry"},
            json={"expected_version": cancelled.json()["version"]},
        )
        cancelled_again = client.post(
            f"/api/v1/workspace/reviews/{job_id}/cancel",
            headers={"Idempotency-Key": "lifecycle-cancel-again"},
            json={"expected_version": retried.json()["version"]},
        )
        deleted = client.delete(
            f"/api/v1/workspace/reviews/{job_id}",
            headers={"Idempotency-Key": "lifecycle-delete"},
        )

    assert fetched.status_code == 200
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert retried.status_code == 200 and retried.json()["status"] == "queued"
    assert cancelled_again.status_code == 200
    assert deleted.status_code == 204


def test_teacher_workspace_project_selection_is_server_owned() -> None:
    app, reviewer, _, first = _app()
    second = Project(
        uuid4(), first.organization_id, "Second review project", "active", 1,
        datetime(2026, 7, 24, 10, tzinfo=UTC),
        datetime(2026, 7, 24, 10, tzinfo=UTC),
    )
    with app.state.dependencies.uow_factory(reviewer) as uow:
        uow.projects.add(second.scope, second)
        uow.projects.save_membership(
            second.scope,
            ProjectMembership(
                uuid4(), second.organization_id, second.id, reviewer.actor_id,
                Role.REVIEWER, "active", 1,
                datetime(2026, 7, 24, 10, tzinfo=UTC),
                datetime(2026, 7, 24, 10, tzinfo=UTC),
            ),
            None,
        )
        uow.commit()

    with TestClient(app) as client:
        before = client.get("/api/v1/workspace")
        selected = client.put(
            "/api/v1/workspace/project",
            json={"project_id": str(second.id)},
        )
        after = client.get("/api/v1/workspace")

    assert before.json()["status"] == "workspace_selection_required"
    assert selected.status_code == 200 and selected.json()["project"]["id"] == str(second.id)
    assert after.json()["project"]["id"] == str(second.id)

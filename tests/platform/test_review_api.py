from __future__ import annotations

import hashlib
import io
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from services.api.routes.organizations import require_management_actor
from tests.platform.test_paper_api import _app

from peerassist.platform.models import Artifact


def test_review_create_list_get_events_cancel_and_retry_api() -> None:
    app, reviewer, _, project = _app()
    app.dependency_overrides[require_management_actor] = lambda: reviewer
    pdf = b"%PDF-1.7\nreview api\n%%EOF\n"

    with TestClient(app) as client:
        uploaded = client.post(
            f"/api/v1/projects/{project.id}/papers",
            headers={"Idempotency-Key": "paper-for-review"},
            files={"file": ("paper.pdf", pdf, "application/pdf")},
        ).json()
        created = client.post(
            f"/api/v1/projects/{project.id}/review-jobs",
            headers={"Idempotency-Key": "review-create"},
            json={"paper_version_id": uploaded["version"]["id"], "mode": "full"},
        )
        job = created.json()
        listed = client.get(f"/api/v1/projects/{project.id}/review-jobs")
        fetched = client.get(f"/api/v1/projects/{project.id}/review-jobs/{job['id']}")
        events = client.get(f"/api/v1/projects/{project.id}/review-jobs/{job['id']}/events")
        streamed = client.get(
            f"/api/v1/projects/{project.id}/review-jobs/{job['id']}/event-stream"
        )
        cancelled = client.post(
            f"/api/v1/projects/{project.id}/review-jobs/{job['id']}/cancel",
            headers={"Idempotency-Key": "review-cancel"},
            json={"expected_version": job["version"]},
        )
        retried = client.post(
            f"/api/v1/projects/{project.id}/review-jobs/{job['id']}/retry",
            headers={"Idempotency-Key": "review-retry"},
            json={"expected_version": cancelled.json()["version"]},
        )

    assert created.status_code == 201
    assert listed.status_code == 200 and len(listed.json()) == 1
    assert fetched.status_code == 200 and fetched.json()["id"] == job["id"]
    assert events.status_code == 200 and events.json()[0]["event_type"] == "review_job.created"
    assert streamed.status_code == 200 and "event: review_job.created" in streamed.text
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert retried.status_code == 200 and retried.json()["attempt"] == 2


def test_review_decision_and_immutable_artifact_download_api() -> None:
    app, reviewer, _, project = _app()
    app.dependency_overrides[require_management_actor] = lambda: reviewer
    pdf = b"%PDF-1.7\nartifact api\n%%EOF\n"
    with TestClient(app) as client:
        uploaded = client.post(
            f"/api/v1/projects/{project.id}/papers",
            headers={"Idempotency-Key": "artifact-paper"},
            files={"file": ("paper.pdf", pdf, "application/pdf")},
        ).json()
        job = client.post(
            f"/api/v1/projects/{project.id}/review-jobs",
            headers={"Idempotency-Key": "artifact-job"},
            json={"paper_version_id": uploaded["version"]["id"], "mode": "full"},
        ).json()
        decision = client.post(
            f"/api/v1/projects/{project.id}/review-jobs/{job['id']}/decisions",
            headers={"Idempotency-Key": "concern-decision"},
            json={
                "decision_type": "concern",
                "subject_id": "concern-1",
                "decision": "confirm",
                "expected_version": job["version"],
            },
        )
        content = b"# This paper proposes a tenant-safe review workflow.\n"
        store = app.state.dependencies.object_store
        upload_id = store.create_temporary(project.scope, len(content))
        temporary = store.write_temporary(project.scope, upload_id, io.BytesIO(content))
        descriptor = store.publish(project.scope, temporary, "reports/summary.md")
        artifact = Artifact(
            uuid4(),
            project.organization_id,
            project.id,
            UUID(job["id"]),
            "paper_summary.md",
            descriptor,
            "available",
            datetime.now(UTC),
        )
        with app.state.dependencies.uow_factory(reviewer) as uow:
            uow.artifacts.add(project.scope, artifact)
            uow.commit()
        listed = client.get(
            f"/api/v1/projects/{project.id}/review-jobs/{job['id']}/artifacts"
        )
        downloaded = client.get(
            f"/api/v1/projects/{project.id}/review-jobs/{job['id']}/artifacts/{artifact.id}"
        )

    assert decision.status_code == 200
    assert listed.status_code == 200 and listed.json()[0]["logical_name"] == "paper_summary.md"
    assert downloaded.status_code == 200 and downloaded.content == content
    assert downloaded.headers["etag"] == f'"{hashlib.sha256(content).hexdigest()}"'

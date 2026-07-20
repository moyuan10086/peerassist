from __future__ import annotations

from fastapi.testclient import TestClient
from services.api.routes.organizations import require_management_actor
from tests.platform.test_paper_api import _app


def test_failed_or_cancelled_review_job_can_be_deleted() -> None:
    app, reviewer, _, project = _app()
    app.dependency_overrides[require_management_actor] = lambda: reviewer
    with TestClient(app) as client:
        uploaded = client.post(
            f"/api/v1/projects/{project.id}/papers",
            headers={"Idempotency-Key": "delete-paper"},
            files={"file": ("paper.pdf", b"%PDF-1.7\ndelete\n%%EOF\n", "application/pdf")},
        ).json()
        job = client.post(
            f"/api/v1/projects/{project.id}/review-jobs",
            headers={"Idempotency-Key": "delete-job"},
            json={"paper_version_id": uploaded["version"]["id"], "mode": "full"},
        ).json()
        cancelled = client.post(
            f"/api/v1/projects/{project.id}/review-jobs/{job['id']}/cancel",
            headers={"Idempotency-Key": "delete-cancel"},
            json={"expected_version": job["version"]},
        )
        deleted = client.delete(
            f"/api/v1/projects/{project.id}/review-jobs/{job['id']}",
            headers={"Idempotency-Key": "delete-record"},
        )
        listed = client.get(f"/api/v1/projects/{project.id}/review-jobs")
        missing = client.delete(
            f"/api/v1/projects/{project.id}/review-jobs/{job['id']}",
            headers={"Idempotency-Key": "delete-record-again"},
        )

    assert cancelled.status_code == 200
    assert deleted.status_code == 204 and deleted.content == b""
    assert listed.status_code == 200 and listed.json() == []
    assert missing.status_code == 404


def test_active_review_job_cannot_be_deleted() -> None:
    app, reviewer, _, project = _app()
    app.dependency_overrides[require_management_actor] = lambda: reviewer
    with TestClient(app) as client:
        uploaded = client.post(
            f"/api/v1/projects/{project.id}/papers",
            headers={"Idempotency-Key": "active-delete-paper"},
            files={"file": ("paper.pdf", b"%PDF-1.7\nactive\n%%EOF\n", "application/pdf")},
        ).json()
        job = client.post(
            f"/api/v1/projects/{project.id}/review-jobs",
            headers={"Idempotency-Key": "active-delete-job"},
            json={"paper_version_id": uploaded["version"]["id"], "mode": "full"},
        ).json()
        deleted = client.delete(
            f"/api/v1/projects/{project.id}/review-jobs/{job['id']}",
            headers={"Idempotency-Key": "active-delete"},
        )

    assert deleted.status_code == 409
    assert deleted.json()["error"]["code"] == "invalid_review_job_state"

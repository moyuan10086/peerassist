from __future__ import annotations

from fastapi.testclient import TestClient
from services.api.routes.organizations import require_management_actor
from tests.platform.test_paper_api import _app

from peerassist.platform.models import Role


def test_review_draft_is_saved_and_reloaded_with_version_control() -> None:
    app, reviewer, _, project = _app()
    # Publishing is an owner action; keep the test actor explicit instead of
    # widening reviewer permissions just to exercise finalize.
    with app.state.dependencies.uow_factory(reviewer) as uow:
        membership = uow.projects.get_membership(project.scope, reviewer.actor_id)
        assert membership is not None
        uow.projects.save_membership(
            project.scope,
            membership.__class__(
                membership.id,
                membership.organization_id,
                membership.project_id,
                membership.user_id,
                Role.PROJECT_OWNER,
                membership.status,
                membership.version + 1,
                membership.created_at,
                membership.updated_at,
            ),
            membership.version,
        )
        uow.commit()
    app.dependency_overrides[require_management_actor] = lambda: reviewer
    with TestClient(app) as client:
        uploaded = client.post(
            f"/api/v1/projects/{project.id}/papers",
            headers={"Idempotency-Key": "draft-paper"},
            files={"file": ("paper.pdf", b"%PDF-1.7\ndraft\n%%EOF\n", "application/pdf")},
        ).json()
        job = client.post(
            f"/api/v1/projects/{project.id}/review-jobs",
            headers={"Idempotency-Key": "draft-job"},
            json={"paper_version_id": uploaded["version"]["id"], "mode": "full"},
        ).json()
        empty = client.get(f"/api/v1/projects/{project.id}/review-jobs/{job['id']}/draft")
        saved = client.patch(
            f"/api/v1/projects/{project.id}/review-jobs/{job['id']}/draft",
            headers={"Idempotency-Key": "draft-save"},
            json={"draft": "# 老师的最终意见\n", "expected_version": job["version"]},
        )
        loaded = client.get(f"/api/v1/projects/{project.id}/review-jobs/{job['id']}/draft")
        stale = client.patch(
            f"/api/v1/projects/{project.id}/review-jobs/{job['id']}/draft",
            headers={"Idempotency-Key": "draft-stale"},
            json={"draft": "stale", "expected_version": job["version"]},
        )
        finalized = client.post(
            f"/api/v1/projects/{project.id}/review-jobs/{job['id']}/finalize",
            headers={"Idempotency-Key": "draft-finalize"},
            json={"expected_version": saved.json()["version"]},
        )
        artifacts = client.get(
            f"/api/v1/projects/{project.id}/review-jobs/{job['id']}/artifacts"
        )
        assert finalized.status_code == 200, finalized.text
        assert artifacts.status_code == 200, artifacts.text
        final_artifact = next(
            item for item in artifacts.json() if item["logical_name"] == "final_report.md"
        )
        final_report = client.get(
            f"/api/v1/projects/{project.id}/review-jobs/{job['id']}/artifacts/{final_artifact['id']}"
        )

    assert empty.status_code == 200 and empty.json() == {"draft": "", "version": 1}
    assert saved.status_code == 200 and saved.json()["version"] == 2
    assert loaded.status_code == 200 and loaded.json()["draft"] == "# 老师的最终意见\n"
    assert stale.status_code == 409
    assert finalized.status_code == 200 and finalized.json()["status"] == "completed"
    assert final_report.status_code == 200
    assert final_report.headers["content-type"] == "text/markdown; charset=utf-8"
    assert final_report.text == "# 老师的最终意见\n"

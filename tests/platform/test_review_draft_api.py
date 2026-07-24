from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from services.api.routes.organizations import require_management_actor
from services.worker.main import ReviewWorker
from tests.platform.test_paper_api import _app

from peerassist.platform.models import Role


def test_review_draft_is_saved_and_reloaded_with_version_control(tmp_path: Path) -> None:
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
        prepared = ReviewWorker(
            app.state.dependencies.uow_factory,
            app.state.dependencies.object_store,
            tmp_path,
        ).run_once(worker_id="draft-prepare-worker")
        empty = client.get(f"/api/v1/projects/{project.id}/review-jobs/{job['id']}/draft")
        saved = client.patch(
            f"/api/v1/projects/{project.id}/review-jobs/{job['id']}/draft",
            headers={"Idempotency-Key": "draft-save"},
            json={"draft": "# 老师的最终意见\n", "expected_version": empty.json()["version"]},
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
        assert finalized.status_code == 202, finalized.text
        processed = ReviewWorker(
            app.state.dependencies.uow_factory,
            app.state.dependencies.object_store,
            tmp_path,
        ).run_once(worker_id="draft-export-worker")
        exports = client.get(
            f"/api/v1/workspace/reviews/{job['id']}/exports"
        )
        final_report = client.get(
            f"/api/v1/workspace/reviews/{job['id']}/exports/{exports.json()[0]['id']}"
        )

    assert empty.status_code == 200 and empty.json() == {"draft": "", "version": 2}
    assert prepared is not None and prepared.status == "blocked"
    assert saved.status_code == 200 and saved.json()["version"] == 3
    assert loaded.status_code == 200 and loaded.json()["draft"] == "# 老师的最终意见\n"
    assert stale.status_code == 409
    assert finalized.status_code == 202 and finalized.json()["status"] == "queued"
    assert processed is not None and processed.status == "completed"
    assert final_report.status_code == 200
    assert final_report.headers["content-type"] == "text/markdown; charset=utf-8"
    assert "# 老师的最终意见" in final_report.text

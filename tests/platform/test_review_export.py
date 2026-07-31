from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4, uuid5

import pytest
from fastapi.testclient import TestClient
from services.api.app import create_app
from services.api.composition import PlatformDependencies
from services.api.routes.organizations import require_management_actor
from services.worker.main import ReviewWorker
from tests.platform.test_paper_api import _settings
from tests.platform.test_review_workspace_service import _seed

from peerassist.platform.adapters.memory import MemoryObjectStore
from peerassist.platform.errors import DependencyUnavailable, StaleVersion
from peerassist.platform.models import ReviewJob
from peerassist.platform.services.review_workspace import (
    ReviewWorkspaceService,
    SaveReviewDocument,
    SubmitReviewExport,
)

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)


def _export_seed():
    workspace, _, factory, store, actors, project, version = _seed()
    job = ReviewJob(
        uuid4(), project.organization_id, project.id, version.id,
        "full", "finalize", "blocked", 1, 1,
        actors["owner"].actor_id, NOW, NOW,
    )
    with factory(actors["owner"]) as uow:
        uow.review_jobs.add(project.scope, job)
        uow.commit()
    document = workspace.get_document(actors["owner"], project.id, job.id)
    blocks = tuple(
        replace(block, text=f"{block.section} content")
        for block in document.blocks
    )
    document = workspace.save_document(
        actors["owner"],
        SaveReviewDocument(
            project.id,
            job.id,
            blocks,
            document.document_version,
            "prepare-export-document",
            "request-prepare-export-document",
        ),
    )
    return workspace, factory, store, actors, project, job, document


def _request(project, job, document, *, key="export-review"):
    return SubmitReviewExport(
        project_id=project.id,
        job_id=job.id,
        expected_review_version=job.version,
        expected_document_version=document.document_version,
        expected_decision_event_id=document.base_decision_event_id,
        format="markdown",
        idempotency_key=key,
        request_id=f"request-{key}",
    )


def test_export_submission_freezes_document_and_replays_idempotently() -> None:
    workspace, factory, _, actors, project, job, document = _export_seed()

    submitted = workspace.submit_export(
        actors["owner"], _request(project, job, document)
    )
    replay = workspace.submit_export(
        actors["owner"], _request(project, job, document)
    )

    assert replay == submitted
    assert submitted.status == "queued"
    with factory(actors["owner"]) as uow:
        queued = uow.work_items.get(project.scope, submitted.export_id)
        assert queued is not None and queued.stage == "export"
        assert queued.attempt_id == uuid5(job.id, f"attempt:{job.attempt}")
        event = uow.review_jobs.list_events(project.scope, job.id)[-1]
        assert event.aggregate_sequence == queued.input_revision
        assert event.payload["document_version"] == document.document_version
        assert event.payload["blocks"][0]["text"].endswith("content")
        current = uow.review_jobs.get(project.scope, job.id)
        assert current is not None and current.status == "exporting_report"


def test_export_rejects_job_that_is_still_processing() -> None:
    workspace, factory, _, actors, project, job, document = _export_seed()
    queued = replace(job, stage="queued", status="queued", version=2)
    with factory(actors["owner"]) as uow:
        uow.review_jobs.save(project.scope, queued, job.version)
        uow.commit()

    with pytest.raises(ValueError, match="not ready"):
        workspace.submit_export(
            actors["owner"], _request(project, queued, document, key="too-early")
        )


def test_export_rejects_stale_document_snapshot_without_enqueuing() -> None:
    workspace, factory, _, actors, project, job, document = _export_seed()
    stale = replace(
        _request(project, job, document, key="stale-export"),
        expected_document_version=document.document_version - 1,
    )

    with pytest.raises(StaleVersion):
        workspace.submit_export(actors["owner"], stale)

    with factory(actors["owner"]) as uow:
        assert not any(item.stage == "export" for item in uow.work_items._state.work_items.values())
        assert uow.review_jobs.get(project.scope, job.id) == job


def test_export_worker_publishes_report_before_completing_job(tmp_path: Path) -> None:
    workspace, factory, store, actors, project, job, document = _export_seed()
    submitted = workspace.submit_export(
        actors["owner"], _request(project, job, document)
    )

    completed = ReviewWorker(factory, store, tmp_path, clock=lambda: NOW).run_once(
        worker_id="export-worker"
    )

    assert completed is not None and completed.status == "completed"
    with factory(actors["owner"]) as uow:
        reports = uow.report_versions.list_for_job(project.scope, job.id)
        assert len(reports) == 1 and reports[0].status == "published"
        artifact = uow.artifacts.get(
            project.scope,
            workspace.export_artifact_id(reports[0].id),
        )
        assert artifact is not None and artifact.status == "available"
        assert uow.work_items.get(project.scope, submitted.export_id) is None
    stream = store.open_immutable(project.scope, artifact.object.object_id)
    try:
        report = stream.read().decode("utf-8")
    finally:
        stream.close()
    assert "# PeerAssist 审稿意见" in report
    assert "major_issues content" in report


def test_teacher_export_api_submits_lists_and_downloads(tmp_path: Path) -> None:
    _, factory, store, actors, _, job, document = _export_seed()
    base = PlatformDependencies.for_test()
    app = create_app(
        _settings(),
        PlatformDependencies(
            factory,
            base.identity_provider,
            store,
            base.session_service,
            base.readiness_checks,
            base.lifecycle_resources,
            legacy_reader=base.legacy_reader,
        ),
    )
    app.dependency_overrides[require_management_actor] = lambda: actors["owner"]

    with TestClient(app) as client:
        submitted = client.post(
            f"/api/v1/workspace/reviews/{job.id}/exports",
            headers={"Idempotency-Key": "export-api"},
            json={
                "expected_review_version": job.version,
                "expected_document_version": document.document_version,
                "expected_decision_event_id": document.base_decision_event_id,
                "format": "markdown",
            },
        )
        factory._clock = lambda: datetime.now(UTC) + timedelta(seconds=1)
        ReviewWorker(factory, store, tmp_path).run_once(worker_id="export-api-worker")
        listed = client.get(f"/api/v1/workspace/reviews/{job.id}/exports")
        report_id = listed.json()[0]["id"]
        downloaded = client.get(
            f"/api/v1/workspace/reviews/{job.id}/exports/{report_id}"
        )

    assert submitted.status_code == 202 and submitted.json()["status"] == "queued"
    assert listed.status_code == 200 and len(listed.json()) == 1
    assert listed.json()[0]["status"] == "published"
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"].startswith("text/markdown")
    assert "major_issues content" in downloaded.text


def test_export_publish_failure_retries_without_false_completion(tmp_path: Path) -> None:
    class FailOnceStore(MemoryObjectStore):
        failed = False

        def publish(self, scope, temporary, object_id):
            if not self.failed:
                self.failed = True
                raise DependencyUnavailable()
            return super().publish(scope, temporary, object_id)

    _, factory, _, actors, project, job, document = _export_seed()
    factory._clock = lambda: NOW
    store = FailOnceStore(clock=lambda: NOW)
    workspace = ReviewWorkspaceService(factory, store, clock=lambda: NOW)
    submitted = workspace.submit_export(
        actors["owner"], _request(project, job, document, key="retry-export")
    )
    worker = ReviewWorker(factory, store, tmp_path, clock=lambda: NOW)

    with pytest.raises(DependencyUnavailable):
        worker.run_once(worker_id="retry-export-worker")

    with factory(actors["owner"]) as uow:
        current = uow.review_jobs.get(project.scope, job.id)
        assert current is not None and current.status == "exporting_report"
        assert current.safe_error_code == "export_retry_pending"
        assert uow.report_versions.list_for_job(project.scope, job.id) == ()
        assert uow.work_items.get(project.scope, submitted.export_id) is not None

    completed = worker.run_once(worker_id="retry-export-worker")
    assert completed is not None and completed.status == "completed"
    with factory(actors["owner"]) as uow:
        assert len(uow.report_versions.list_for_job(project.scope, job.id)) == 1

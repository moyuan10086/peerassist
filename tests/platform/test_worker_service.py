from __future__ import annotations

import io
import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from services.worker.main import ReviewWorker
from tests.platform.test_paper_service import _seed

from peerassist.model_review import ModelReviewConfig
from peerassist.platform.models import Project, ProjectMembership, Role
from peerassist.platform.services.papers import UploadPaper
from peerassist.platform.services.review_workspace import (
    ChangeExternalServiceConsent,
    ReviewWorkspaceService,
)
from peerassist.platform.services.reviews import CreateReviewJob, ReviewService


def test_worker_claims_review_and_publishes_summary_and_report(tmp_path: Path) -> None:
    paper_service, store, actors, project = _seed()
    uploaded = paper_service.upload(
        actors["reviewer"],
        UploadPaper(
            project.id,
            "paper.pdf",
            "application/pdf",
            1024,
            "worker-paper",
            "worker-paper-request",
        ),
        io.BytesIO(b"%PDF-1.7\nworker fixture\n%%EOF\n"),
    )
    review_service = ReviewService(paper_service._uow_factory, clock=paper_service._clock)
    job = review_service.create(
        actors["reviewer"],
        CreateReviewJob(
            project.id,
            uploaded.version.id,
            "full",
            "worker-review",
            "worker-review-request",
        ),
    )
    summary = "# 这篇论文讲了什么\n\n本文提出了一个可验证的论文审阅流程。\n"
    report = "# 审阅报告\n\n## 主要结论\n该方法需要进一步验证。\n"
    worker = ReviewWorker(
        paper_service._uow_factory,
        store,
        tmp_path,
        document_generator=lambda _: (summary, report),
        clock=paper_service._clock,
    )

    processed = worker.run_once(worker_id="worker-test")

    assert processed is not None
    assert processed.id == job.id and processed.stage == "finalize" and processed.status == "blocked"
    artifacts = worker.artifact_service.list(actors["viewer"], project.id, job.id)
    assert {artifact.logical_name for artifact in artifacts} == {
        "paper_summary.md",
        "review.md",
        "review_result.json",
    }
    result_artifact = next(item for item in artifacts if item.logical_name == "review_result.json")
    result_source = worker.artifact_service.open(
        actors["viewer"], project.id, job.id, result_artifact.id
    )
    try:
        result = json.loads(result_source.stream.read().decode("utf-8"))
    finally:
        result_source.stream.close()
    assert result["schema_version"] == "peerassist.review_result.v1"
    assert result["concerns"][0]["status"] == "pending_human_confirmation"
    assert result["concerns"][0]["evidence"][0]["page"] == 1
    summary_artifact = next(item for item in artifacts if item.logical_name == "paper_summary.md")
    source = worker.artifact_service.open(
        actors["viewer"], project.id, job.id, summary_artifact.id
    )
    try:
        assert source.stream.read().decode("utf-8") == summary
    finally:
        source.stream.close()
    assert not any(tmp_path.rglob("object-000.bin"))


def test_worker_processes_jobs_from_projects_created_after_bootstrap(tmp_path: Path) -> None:
    paper_service, store, actors, first_project = _seed()
    second_project = Project(
        uuid4(),
        first_project.organization_id,
        "Second Review Project",
        "active",
        1,
        first_project.created_at,
        first_project.updated_at,
    )
    with paper_service._uow_factory(actors["owner"]) as uow:
        uow.projects.add(second_project.scope, second_project)
        for role in ("owner", "reviewer", "viewer"):
            uow.projects.save_membership(
                second_project.scope,
                ProjectMembership(
                    uuid4(),
                    second_project.organization_id,
                    second_project.id,
                    actors[role].actor_id,
                    Role.PROJECT_OWNER if role == "owner" else Role(role),
                    "active",
                    1,
                    first_project.created_at,
                    first_project.updated_at,
                ),
                None,
            )
        uow.commit()

    review_service = ReviewService(paper_service._uow_factory, clock=paper_service._clock)
    jobs = []
    for index, project in enumerate((first_project, second_project), start=1):
        uploaded = paper_service.upload(
            actors["reviewer"],
            UploadPaper(
                project.id,
                f"paper-{index}.pdf",
                "application/pdf",
                1024,
                f"multi-paper-{index}",
                f"multi-paper-request-{index}",
            ),
            io.BytesIO(f"%PDF-1.7\nproject {index}\n%%EOF\n".encode()),
        )
        jobs.append(
            review_service.create(
                actors["reviewer"],
                CreateReviewJob(
                    project.id,
                    uploaded.version.id,
                    "full",
                    f"multi-review-{index}",
                    f"multi-review-request-{index}",
                ),
            )
        )
    worker = ReviewWorker(
        paper_service._uow_factory,
        store,
        tmp_path,
        document_generator=lambda _: ("# Summary\n", "# Review\n"),
        clock=paper_service._clock,
    )

    processed = {
        worker.run_once(worker_id="multi-project-worker").id,
        worker.run_once(worker_id="multi-project-worker").id,
    }

    assert processed == {job.id for job in jobs}


def test_worker_without_current_model_consent_uses_local_fallback_and_never_calls_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paper_service, store, actors, project = _seed()
    uploaded = paper_service.upload(
        actors["reviewer"],
        UploadPaper(
            project.id,
            "paper.pdf",
            "application/pdf",
            1024,
            "worker-consent-paper",
            "worker-consent-paper-request",
        ),
        io.BytesIO(
            b"%PDF-1.7\nThis is a sufficiently long paper sentence for local fallback.\n%%EOF\n"
        ),
    )
    job = ReviewService(paper_service._uow_factory, clock=paper_service._clock).create(
        actors["reviewer"],
        CreateReviewJob(
            project.id,
            uploaded.version.id,
            "full",
            "worker-consent-review",
            "worker-consent-review-request",
        ),
    )
    calls = {"external": 0}

    def external(*_args, **_kwargs):
        calls["external"] += 1
        return ("# external summary", "# external report")

    monkeypatch.setattr(
        "services.worker.main.resolve_model_review_config",
        lambda: ModelReviewConfig(
            api_key="server-only-key", base_url="https://provider.example/v1", model="model-a"
        ),
    )
    monkeypatch.setattr(
        "services.worker.main._extract_pdf",
        lambda _pdf: ("Synthetic paper", "A sufficiently long extracted paper text for model gating."),
    )
    monkeypatch.setattr("services.worker.main._generate_with_model", external)
    worker = ReviewWorker(paper_service._uow_factory, store, tmp_path, clock=paper_service._clock)

    processed = worker.run_once(worker_id="consent-worker")

    assert processed is not None and processed.id == job.id
    assert calls["external"] == 0
    artifacts = worker.artifact_service.list(actors["viewer"], project.id, job.id)
    summary_artifact = next(item for item in artifacts if item.logical_name == "paper_summary.md")
    source = worker.artifact_service.open(actors["viewer"], project.id, job.id, summary_artifact.id)
    try:
        assert "尚未调用已配置模型" in source.stream.read().decode("utf-8")
    finally:
        source.stream.close()


def test_worker_calls_model_only_with_current_matching_grant(tmp_path: Path, monkeypatch) -> None:
    paper_service, store, actors, project = _seed()
    uploaded = paper_service.upload(
        actors["reviewer"],
        UploadPaper(
            project.id, "paper.pdf", "application/pdf", 1024,
            "worker-valid-paper", "worker-valid-paper-request",
        ),
        io.BytesIO(b"%PDF-1.7\nvalid model fixture\n%%EOF\n"),
    )
    review_service = ReviewService(paper_service._uow_factory, clock=paper_service._clock)
    job = review_service.create(
        actors["reviewer"],
        CreateReviewJob(
            project.id, uploaded.version.id, "full", "worker-valid-review", "worker-valid-review-request"
        ),
    )
    workspace = ReviewWorkspaceService(paper_service._uow_factory, store, clock=paper_service._clock)
    consent = dict(
        project_id=project.id,
        job_id=job.id,
        paper_version_id=uploaded.version.id,
        service="model",
        provider_config_revision=1,
        policy_version="peerassist.model-policy.v1",
        data_scope={"paper_text": True},
        idempotency_key="worker-valid-consent-reapply",
        request_id="worker-valid-consent-reapply",
    )
    workspace.change_consent(actors["reviewer"], ChangeExternalServiceConsent(action="reapply", expected_consent_version=0, **consent))
    workspace.change_consent(
        actors["reviewer"],
        ChangeExternalServiceConsent(
            action="grant", expected_consent_version=1, expires_at=paper_service._clock() + timedelta(hours=1),
            idempotency_key="worker-valid-consent-grant", request_id="worker-valid-consent-grant", **{
                key: value for key, value in consent.items() if key not in {"idempotency_key", "request_id"}
            },
        ),
    )
    calls = {"external": 0}

    def external(*_args, **_kwargs):
        calls["external"] += 1
        return "# external summary", "# external report"

    monkeypatch.setattr(
        "services.worker.main.resolve_model_review_config",
        lambda: ModelReviewConfig(
            api_key="server-only-key", base_url="https://provider.example/v1", model="model-a",
            provider_config_revision=1, policy_version="peerassist.model-policy.v1",
            configuration_id="config-1",
        ),
    )
    monkeypatch.setattr(
        "services.worker.main._extract_pdf",
        lambda _pdf: ("Synthetic paper", "A sufficiently long extracted paper text for model gating."),
    )
    monkeypatch.setattr("services.worker.main._generate_with_model", external)
    worker = ReviewWorker(paper_service._uow_factory, store, tmp_path, clock=paper_service._clock)

    processed = worker.run_once(worker_id="valid-consent-worker")

    assert processed is not None and processed.id == job.id
    assert calls["external"] == 1
    with paper_service._uow_factory(actors["reviewer"]) as uow:
        events = uow.review_jobs.list_events(project.scope, job.id)
    published = events[-1]
    assert published.payload["model_status"] == "external_model"


def test_worker_rechecks_model_snapshot_after_consent_gate_before_external_call(
    tmp_path: Path, monkeypatch
) -> None:
    paper_service, store, actors, project = _seed()
    uploaded = paper_service.upload(
        actors["reviewer"],
        UploadPaper(
            project.id, "paper.pdf", "application/pdf", 1024,
            "worker-race-paper", "worker-race-paper-request",
        ),
        io.BytesIO(b"%PDF-1.7\nrace fixture\n%%EOF\n"),
    )
    ReviewService(paper_service._uow_factory, clock=paper_service._clock).create(
        actors["reviewer"],
        CreateReviewJob(
            project.id, uploaded.version.id, "full", "worker-race-review", "worker-race-review-request"
        ),
    )
    calls = {"external": 0, "config": 0}
    first = ModelReviewConfig(
        api_key="server-only-key", base_url="https://provider.example/v1", model="model-a",
        provider_config_revision=1, policy_version="peerassist.model-policy.v1",
        configuration_id="config-1",
    )
    changed = ModelReviewConfig(
        api_key="server-only-key", base_url="https://provider.example/v1", model="model-a",
        provider_config_revision=2, policy_version="peerassist.model-policy.v1",
        configuration_id="config-2",
    )

    def resolve_config():
        calls["config"] += 1
        return first if calls["config"] == 1 else changed

    monkeypatch.setattr("services.worker.main.resolve_model_review_config", resolve_config)
    monkeypatch.setattr(
        "services.worker.main._extract_pdf",
        lambda _pdf: ("Synthetic paper", "A sufficiently long extracted paper text for model gating."),
    )
    monkeypatch.setattr("services.worker.main._generate_with_model", lambda *_args: calls.__setitem__("external", calls["external"] + 1))
    # No consent is needed to prove the config snapshot is checked after the gate.
    worker = ReviewWorker(paper_service._uow_factory, store, tmp_path, clock=paper_service._clock)
    monkeypatch.setattr(worker, "_model_consent_gate", lambda *_args: (resolve_config(), ""))

    processed = worker.run_once(worker_id="race-worker")

    assert processed is not None and calls["external"] == 0

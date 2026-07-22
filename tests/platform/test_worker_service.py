from __future__ import annotations

import io
import json
from pathlib import Path
from uuid import uuid4

from services.worker.main import ReviewWorker
from tests.platform.test_paper_service import _seed

from peerassist.platform.models import Project, ProjectMembership, Role
from peerassist.platform.services.papers import UploadPaper
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

from __future__ import annotations

import io
from pathlib import Path

from services.worker.main import ReviewWorker
from tests.platform.test_paper_service import _seed

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

    processed = worker.run_once(project.scope, worker_id="worker-test")

    assert processed is not None
    assert processed.id == job.id and processed.stage == "finalize" and processed.status == "blocked"
    artifacts = worker.artifact_service.list(actors["viewer"], project.id, job.id)
    assert {artifact.logical_name for artifact in artifacts} == {
        "paper_summary.md",
        "review.md",
    }
    summary_artifact = next(item for item in artifacts if item.logical_name == "paper_summary.md")
    source = worker.artifact_service.open(
        actors["viewer"], project.id, job.id, summary_artifact.id
    )
    try:
        assert source.stream.read().decode("utf-8") == summary
    finally:
        source.stream.close()
    assert not any(tmp_path.rglob("object-000.bin"))

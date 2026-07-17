from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pymupdf

from peerassist.job_adapters import build_local_stage_adapters
from peerassist.job_repository import PaperRepository, ReviewJobRepository
from peerassist.job_runner import RecoverableReviewJobRunner, ReviewJobScheduler
from peerassist.review_context import build_review_context
from schemas.peerassist_jobs import (
    ConsentDecision,
    PaperRecord,
    ReviewJobState,
    ReviewJobStatus,
    ReviewStage,
)


def _pdf(path: Path) -> str:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text(
        (72, 72),
        "PeerAssist Study\nWe show accuracy improves.\nExperiments use Dataset A with n=100.",
    )
    document.save(path)
    document.close()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _job(repository: ReviewJobRepository) -> ReviewJobState:
    job_id = uuid4()
    return repository.create(
        ReviewJobState(
            id=job_id,
            paper_id="a" * 64,
            run_dir=f"jobs/{job_id}/run",
            attempt_id="attempt-1",
        )
    )


def _adapters(calls: list[str]):
    return {
        stage: (lambda state, stage=stage: calls.append(stage.value) or {"stage": stage.value})
        for stage in (
            ReviewStage.VALIDATE,
            ReviewStage.PARSE,
            ReviewStage.EVIDENCE,
            ReviewStage.PROFILE,
            ReviewStage.PLAN,
            ReviewStage.DETERMINISTIC,
            ReviewStage.CITATION,
            ReviewStage.AGENTS,
            ReviewStage.INTEGRATE,
        )
    }


def test_review_context_prioritizes_core_claim_evidence_beyond_first_80() -> None:
    ledger = {
        "items": [
            {
                "id": f"E{index:03d}",
                "type": "text_span",
                "locator": f"page {index // 10 + 1}",
                "section": "Body",
                "text": f"Evidence {index}",
            }
            for index in range(100)
        ]
    }
    context = build_review_context(
        ledger=ledger,
        paper_profile={"paper_id": "paper", "title": {"value": "Title"}},
        claim_graph={
            "claims": [
                {
                    "claim_id": "claim-core",
                    "centrality": 1.0,
                    "evidence_ids": ["E099"],
                    "support_evidence_ids": [],
                }
            ]
        },
        experiment_inventory={"experiments": []},
        review_plan={
            "core_claim_ids": ["claim-core"],
            "reading_route": [{"rank": 1, "evidence_ids": ["E099"]}],
        },
        deterministic_checks={"checks": []},
        max_evidence=80,
    )

    selected_ids = context["selected_evidence_ids"]
    assert len(selected_ids) == 80
    assert "E099" in selected_ids
    assert context["paper_profile"]["title"]["value"] == "Title"
    assert context["claim_graph"]["claims"][0]["claim_id"] == "claim-core"
    assert "experiment_inventory" in context
    assert "review_plan" in context


def test_review_context_merges_pdf_lines_into_bounded_traceable_blocks() -> None:
    ledger = {
        "items": [
            {
                "id": "P0001-B0001-L001",
                "type": "text_span",
                "locator": "page 1, block 1, line 1",
                "section": "Abstract",
                "text": "We propose an evidence-grounded frame-",
                "metadata": {"block_id": "P0001-B0001", "line": 1},
            },
            {
                "id": "P0001-B0001-L002",
                "type": "text_span",
                "locator": "page 1, block 1, line 2",
                "section": "Abstract",
                "text": "work for peer review.",
                "metadata": {"block_id": "P0001-B0001", "line": 2},
            },
            {
                "id": "P0001-B0002-L001",
                "type": "text_span",
                "locator": "page 1, block 2, line 1",
                "section": "Results",
                "text": "The workflow reduces review time by 42%.",
                "metadata": {"block_id": "P0001-B0002", "line": 1},
            },
        ]
    }
    context = build_review_context(
        ledger=ledger,
        paper_profile={},
        claim_graph={
            "claims": [
                {
                    "claim_id": "claim-core",
                    "centrality": 1.0,
                    "evidence_ids": ["P0001-B0001-L001", "P0001-B0001-L002"],
                    "support_evidence_ids": ["P0001-B0002-L001"],
                }
            ]
        },
        experiment_inventory={},
        review_plan={"core_claim_ids": ["claim-core"], "reading_route": []},
        deterministic_checks={},
        max_evidence=2,
        max_context_chars=500,
    )

    assert len(context["selected_evidence"]) == 2
    first = context["selected_evidence"][0]
    assert first["text"] == "We propose an evidence-grounded framework for peer review."
    assert first["evidence_ids"] == ["P0001-B0001-L001", "P0001-B0001-L002"]
    assert context["budget"]["selected_blocks"] == 2
    assert context["budget"]["selected_chars"] <= 500


def test_review_context_enforces_total_serialized_budget_for_large_artifacts() -> None:
    evidence = [
        {
            "id": f"E{index:03d}",
            "type": "text_span",
            "locator": f"page {index + 1}",
            "section": "Results",
            "text": "A long evidence sentence " * 20,
        }
        for index in range(80)
    ]
    claims = [
        {
            "claim_id": f"claim-{index}",
            "text": "A verbose central claim " * 20,
            "claim_type": "empirical",
            "centrality": 1.0 - index / 100,
            "evidence_ids": [row["id"] for row in evidence],
            "support_evidence_ids": [row["id"] for row in evidence],
            "support_status": "partially_supported",
        }
        for index in range(40)
    ]
    experiment_fields = {
        name: {
            "value": ["A verbose experimental detail " * 20 for _ in range(5)],
            "evidence_ids": [row["id"] for row in evidence],
            "provenance": "paper_extraction",
            "needs_human_review": True,
        }
        for name in (
            "label",
            "datasets",
            "sample_sizes",
            "data_splits",
            "baselines",
            "metrics",
            "random_seeds",
            "statistics",
            "ablations",
            "key_figures",
            "key_tables",
        )
    }
    context = build_review_context(
        ledger={"items": evidence},
        paper_profile={
            "paper_id": "paper",
            "title": {"value": "Title", "evidence_ids": ["E000"]},
            "abstract": {"value": "Very long abstract " * 300, "evidence_ids": ["E000"]},
        },
        claim_graph={"claims": claims, "edges": []},
        experiment_inventory={
            "experiments": [
                {
                    "experiment_id": f"experiment-{index}",
                    "importance_score": 1.0 - index / 10,
                    **experiment_fields,
                }
                for index in range(8)
            ]
        },
        review_plan={
            "core_claim_ids": ["claim-0"],
            "reading_route": [{"rank": 1, "evidence_ids": [row["id"] for row in evidence]}],
        },
        deterministic_checks={
            "checks": [
                {
                    "id": f"check-{index}",
                    "kind": "statistical_consistency",
                    "status": "lead",
                    "applicability": "applicable",
                    "evidence_ids": [row["id"] for row in evidence],
                    "message": "A verbose deterministic finding " * 30,
                    "benign_explanations": ["A verbose explanation " * 20 for _ in range(5)],
                }
                for index in range(50)
            ]
        },
        max_evidence=40,
        max_context_chars=12_000,
        max_serialized_chars=24_000,
    )

    actual_serialized_chars = len(
        json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    )
    assert actual_serialized_chars <= 24_000
    assert context["budget"]["serialized_chars"] == actual_serialized_chars
    assert len(context["claim_graph"]["claims"]) <= 8
    assert len(context["claim_graph"]["claims"][0]["evidence_ids"]) <= 6


def test_review_job_persists_service_denial_degradation_contract() -> None:
    job_id = uuid4()
    now = datetime.now(UTC)
    state = ReviewJobState(
        id=job_id,
        paper_id="a" * 64,
        run_dir=f"jobs/{job_id}/run",
        attempt_id="attempt-1",
        degradation_code="service_denied_local_fallback",
        degraded_services=["model"],
        degraded_at=now,
    )

    assert state.degradation_code == "service_denied_local_fallback"
    assert state.degraded_services == ["model"]
    assert state.degraded_at == now


def test_runner_commits_stage_dag_and_stops_for_human_confirmation(tmp_path) -> None:
    repository = ReviewJobRepository(tmp_path)
    state = _job(repository)
    calls: list[str] = []
    runner = RecoverableReviewJobRunner(repository, _adapters(calls), owner="worker-1")
    state = repository.update(
        state.id,
        expected_revision=state.revision,
        model_consent=state.model_consent.model_copy(update={"decision": ConsentDecision.GRANTED}),
    )

    result = runner.run(state.id)

    assert result.status is ReviewJobStatus.AWAITING_HUMAN_CONFIRMATION
    assert result.stage is ReviewStage.AWAIT_CONFIRMATION
    assert calls[-1] == "integrate"
    assert repository.current_stage_manifest(state.id, ReviewStage.PROFILE) is not None


def test_runner_blocks_and_resumes_at_model_consent(tmp_path) -> None:
    repository = ReviewJobRepository(tmp_path)
    state = _job(repository)
    calls: list[str] = []
    runner = RecoverableReviewJobRunner(repository, _adapters(calls), owner="worker-1")

    blocked = runner.run(state.id)

    assert blocked.status is ReviewJobStatus.BLOCKED
    assert blocked.required_consents == ["model"]
    assert blocked.resume_stage is ReviewStage.AGENTS
    assert "deterministic" in calls
    assert "agents" not in calls

    runner.grant_consent(state.id, service="model", actor="reviewer")
    resumed = runner.run(state.id)
    assert resumed.status is ReviewJobStatus.AWAITING_HUMAN_CONFIRMATION
    assert calls.count("agents") == 1


def test_runner_denied_model_uses_durable_local_fallback(tmp_path) -> None:
    repository = ReviewJobRepository(tmp_path)
    state = _job(repository)
    calls: list[str] = []
    runner = RecoverableReviewJobRunner(repository, _adapters(calls), owner="worker-1")
    denied = state.model_consent.model_copy(
        update={
            "decision": ConsentDecision.DENIED,
            "decided_by": "reviewer",
            "decided_at": datetime.now(UTC),
            "reason": "Keep the manuscript local.",
        }
    )
    repository.update(
        state.id,
        expected_revision=state.revision,
        model_consent=denied,
    )

    result = runner.run(state.id)

    assert result.status is ReviewJobStatus.AWAITING_HUMAN_CONFIRMATION
    assert result.degradation_code == "service_denied_local_fallback"
    assert result.degraded_services == ["model"]
    assert "agents" not in calls
    assert repository.current_stage_manifest(state.id, ReviewStage.AGENTS) is not None


def test_runner_observes_cancel_before_starting_next_stage(tmp_path) -> None:
    repository = ReviewJobRepository(tmp_path)
    state = _job(repository)
    calls: list[str] = []
    runner = RecoverableReviewJobRunner(repository, _adapters(calls), owner="worker-1")
    repository.update(
        state.id,
        expected_revision=state.revision,
        cancel_requested=True,
        status=ReviewJobStatus.CANCEL_REQUESTED,
    )

    result = runner.run(state.id)

    assert result.status is ReviewJobStatus.CANCELLED
    assert calls == []


def test_retry_uses_new_attempt_without_deleting_previous_stage_outputs(tmp_path) -> None:
    repository = ReviewJobRepository(tmp_path)
    state = _job(repository)
    calls: list[str] = []
    runner = RecoverableReviewJobRunner(repository, _adapters(calls), owner="worker-1")
    repository.update(
        state.id,
        expected_revision=state.revision,
        status=ReviewJobStatus.FAILED,
        stage=ReviewStage.EVIDENCE,
        error_code="stage_failed",
        error="temporary failure",
    )

    retried = runner.retry(state.id)

    assert retried.status is ReviewJobStatus.QUEUED
    assert retried.stage is ReviewStage.EVIDENCE
    assert retried.attempt_id != state.attempt_id
    assert retried.error is None


def test_background_scheduler_runs_and_recovers_orphaned_job(tmp_path) -> None:
    repository = ReviewJobRepository(tmp_path)
    state = _job(repository)
    calls: list[str] = []
    runner = RecoverableReviewJobRunner(repository, _adapters(calls), owner="worker-1")
    state = repository.update(
        state.id,
        expected_revision=state.revision,
        model_consent=state.model_consent.model_copy(update={"decision": ConsentDecision.GRANTED}),
        status=ReviewJobStatus.INTERRUPTED,
    )
    scheduler = ReviewJobScheduler(runner, max_workers=1)
    try:
        recovered = scheduler.recover_orphans()
        result = scheduler.wait(state.id, timeout=5)
    finally:
        scheduler.shutdown()

    assert recovered == [state.id]
    assert result.status is ReviewJobStatus.AWAITING_HUMAN_CONFIRMATION


def test_finalize_updates_job_status_from_export_result(tmp_path, monkeypatch) -> None:
    repository = ReviewJobRepository(tmp_path)
    state = _job(repository)
    runner = RecoverableReviewJobRunner(repository, _adapters([]), owner="worker-1")
    state = repository.update(
        state.id,
        expected_revision=state.revision,
        stage=ReviewStage.AWAIT_CONFIRMATION,
        status=ReviewJobStatus.AWAITING_HUMAN_CONFIRMATION,
    )
    monkeypatch.setattr(
        "peerassist.job_runner.finalize_confirmed_report",
        lambda **_kwargs: {"status": "ok", "confirmation_revision": 0},
    )

    completed = runner.finalize(state.id, expected_confirmation_revision=0)

    assert completed.status is ReviewJobStatus.COMPLETED
    assert completed.stage is ReviewStage.COMPLETE


def test_cancel_request_is_durable_before_worker_observes_it(tmp_path) -> None:
    repository = ReviewJobRepository(tmp_path)
    state = _job(repository)
    runner = RecoverableReviewJobRunner(repository, _adapters([]), owner="worker-1")

    cancelled = runner.request_cancel(state.id)

    assert cancelled.cancel_requested is True
    assert cancelled.status is ReviewJobStatus.CANCEL_REQUESTED


def test_state_guarded_commands_converge_under_thread_contention(tmp_path) -> None:
    repository = ReviewJobRepository(tmp_path)
    runner = RecoverableReviewJobRunner(repository, _adapters([]), owner="worker-1")

    consent_job = _job(repository)
    consent_job = repository.update(
        consent_job.id,
        expected_revision=consent_job.revision,
        status=ReviewJobStatus.BLOCKED,
        resume_stage=ReviewStage.AGENTS,
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        granted = list(
            pool.map(
                lambda _: runner.grant_consent(consent_job.id, service="model", actor="reviewer"),
                range(16),
            )
        )
    assert {state.model_consent.decision for state in granted} == {ConsentDecision.GRANTED}

    cancel_job = _job(repository)
    with ThreadPoolExecutor(max_workers=8) as pool:
        cancelled = list(pool.map(lambda _: runner.request_cancel(cancel_job.id), range(16)))
    assert all(state.cancel_requested for state in cancelled)

    retry_job = _job(repository)
    retry_job = repository.update(
        retry_job.id,
        expected_revision=retry_job.revision,
        status=ReviewJobStatus.FAILED,
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        retried = list(pool.map(lambda _: runner.retry(retry_job.id), range(16)))
    assert {state.status for state in retried} == {ReviewJobStatus.QUEUED}
    assert len({state.attempt_id for state in retried}) == 1


def test_real_local_stages_commit_before_model_consent_gate(tmp_path) -> None:
    source = tmp_path / "source.pdf"
    paper_id = _pdf(source)
    paper_dir = tmp_path / "papers" / paper_id / "source"
    paper_dir.mkdir(parents=True)
    stored_pdf = paper_dir / "source.pdf"
    stored_pdf.write_bytes(source.read_bytes())
    PaperRepository(tmp_path).create_or_get(
        PaperRecord(
            paper_id=paper_id,
            source_pdf_path="source/source.pdf",
            size_bytes=stored_pdf.stat().st_size,
        )
    )
    repository = ReviewJobRepository(tmp_path)
    job_id = uuid4()
    state = repository.create(
        ReviewJobState(
            id=job_id,
            paper_id=paper_id,
            run_dir=f"jobs/{job_id}/run",
            attempt_id="attempt-real",
        )
    )
    runner = RecoverableReviewJobRunner(
        repository,
        build_local_stage_adapters(repository),
        owner="worker-real",
    )

    blocked = runner.run(state.id)

    assert blocked.status is ReviewJobStatus.BLOCKED
    assert blocked.resume_stage is ReviewStage.AGENTS
    for stage in (
        ReviewStage.PARSE,
        ReviewStage.EVIDENCE,
        ReviewStage.PROFILE,
        ReviewStage.PLAN,
        ReviewStage.DETERMINISTIC,
        ReviewStage.CITATION,
    ):
        assert repository.current_stage_manifest(state.id, stage) is not None

    runner.grant_consent(state.id, service="model", actor="reviewer")
    completed_candidate = runner.run(state.id)
    out_dir = tmp_path / completed_candidate.run_dir / "stages" / "peerassist"

    assert completed_candidate.status is ReviewJobStatus.AWAITING_HUMAN_CONFIRMATION
    assert (out_dir / "confirmation_review_queue.json").is_file()
    assert (out_dir / "human_confirmations.json").is_file()
    assert not (out_dir / "current_final_report.json").exists()

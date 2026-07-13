from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from peerassist.job_repository import ReviewJobRepository
from peerassist.job_runner import RecoverableReviewJobRunner
from peerassist.review_context import build_review_context
from schemas.peerassist_jobs import ConsentDecision, ReviewJobState, ReviewJobStatus, ReviewStage


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

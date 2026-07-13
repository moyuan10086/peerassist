"""Repository-backed, recoverable PeerAssist review stage runner."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID, uuid4

from common.storage import write_json_atomic
from peerassist.job_repository import ReviewJobRepository
from schemas.peerassist_jobs import (
    ConsentDecision,
    ReviewJobState,
    ReviewJobStatus,
    ReviewStage,
    StageCheckpoint,
    StageManifest,
)

StageAdapter = Callable[[ReviewJobState], dict[str, Any]]

_STAGES = (
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
_NEXT = {stage: _STAGES[index + 1] for index, stage in enumerate(_STAGES[:-1])}
_NEXT[ReviewStage.INTEGRATE] = ReviewStage.AWAIT_CONFIRMATION
_STATUS = {
    ReviewStage.VALIDATE: ReviewJobStatus.VALIDATING_INPUT,
    ReviewStage.PARSE: ReviewJobStatus.PARSING,
    ReviewStage.EVIDENCE: ReviewJobStatus.EVIDENCE_BUILDING,
    ReviewStage.PROFILE: ReviewJobStatus.PROFILING,
    ReviewStage.PLAN: ReviewJobStatus.PLANNING_REVIEW,
    ReviewStage.DETERMINISTIC: ReviewJobStatus.DETERMINISTIC_CHECKING,
    ReviewStage.CITATION: ReviewJobStatus.CITATION_CHECKING,
    ReviewStage.AGENTS: ReviewJobStatus.AGENTS_RUNNING,
    ReviewStage.INTEGRATE: ReviewJobStatus.INTEGRATING,
}


class RecoverableReviewJobRunner:
    """Execute one job from its persisted stage until a durable wait state."""

    def __init__(
        self,
        repository: ReviewJobRepository,
        adapters: Mapping[ReviewStage, StageAdapter],
        *,
        owner: str,
        lease_seconds: float = 300,
    ) -> None:
        self.repository = repository
        self.adapters = dict(adapters)
        self.owner = owner
        self.lease_seconds = lease_seconds

    def run(self, job_id: UUID | str) -> ReviewJobState:
        claim = self.repository.claim(
            job_id,
            owner=self.owner,
            lease_seconds=self.lease_seconds,
        )
        if claim is None:
            return self.repository.get(job_id)
        try:
            while True:
                state = self.repository.get(job_id)
                if state.cancel_requested:
                    return self._update(
                        state,
                        status=ReviewJobStatus.CANCELLED,
                        error_code=None,
                        error=None,
                    )
                if state.stage in {
                    ReviewStage.AWAIT_CONFIRMATION,
                    ReviewStage.COMPLETE,
                }:
                    return state
                if state.status is ReviewJobStatus.BLOCKED:
                    return state
                if state.stage is ReviewStage.AGENTS:
                    decision = state.model_consent.decision
                    if decision is ConsentDecision.PENDING:
                        blocked = self._update(
                            state,
                            status=ReviewJobStatus.BLOCKED,
                            blocked_reason="approval_required",
                            required_consents=["model"],
                            resume_stage=ReviewStage.AGENTS,
                        )
                        self._event(blocked, "consent_required")
                        return blocked
                    if decision is ConsentDecision.DENIED:
                        state = self._commit_denied_model_fallback(state)
                        continue
                state = self._update(state, status=_STATUS[state.stage])
                self._event(state, "stage_started")
                adapter = self.adapters.get(state.stage)
                if adapter is None:
                    raise RuntimeError(f"missing stage adapter: {state.stage.value}")
                payload = adapter(state)
                self._commit_stage(state, payload)
                next_stage = _NEXT[state.stage]
                next_status = (
                    ReviewJobStatus.AWAITING_HUMAN_CONFIRMATION
                    if next_stage is ReviewStage.AWAIT_CONFIRMATION
                    else ReviewJobStatus.QUEUED
                )
                state = self._update(
                    self.repository.get(job_id),
                    stage=next_stage,
                    status=next_status,
                    blocked_reason=None,
                    required_consents=[],
                    resume_stage=None,
                )
                self._event(state, "stage_completed")
                if next_stage is ReviewStage.AWAIT_CONFIRMATION:
                    return state
        except Exception as exc:
            state = self.repository.get(job_id)
            failed = self._update(
                state,
                status=ReviewJobStatus.FAILED,
                error_code="stage_failed",
                error=str(exc),
            )
            self._event(failed, "stage_failed", message=str(exc))
            return failed
        finally:
            self.repository.release_claim(
                job_id,
                owner=self.owner,
                token=claim.token,
            )

    def grant_consent(self, job_id: UUID | str, *, service: str, actor: str) -> ReviewJobState:
        if service not in {"parse", "search", "model"}:
            raise ValueError(f"unsupported consent service: {service}")
        state = self.repository.get(job_id)
        field = f"{service}_consent"
        consent = getattr(state, field)
        if consent.decision is ConsentDecision.GRANTED:
            return state
        granted = consent.model_copy(
            update={
                "decision": ConsentDecision.GRANTED,
                "decided_by": actor,
                "decided_at": datetime.now(UTC),
                "reason": "",
            }
        )
        resume_stage = state.resume_stage or state.stage
        updated = self._update(
            state,
            **{
                field: granted,
                "stage": resume_stage,
                "status": ReviewJobStatus.QUEUED,
                "blocked_reason": None,
                "required_consents": [],
                "resume_stage": None,
            },
        )
        self._event(updated, "consent_granted", payload={"service": service, "actor": actor})
        return updated

    def retry(self, job_id: UUID | str) -> ReviewJobState:
        state = self.repository.get(job_id)
        if state.status not in {
            ReviewJobStatus.FAILED,
            ReviewJobStatus.INTERRUPTED,
            ReviewJobStatus.CANCELLED,
        }:
            return state
        updated = self._update(
            state,
            attempt_id=f"attempt-{uuid4().hex}",
            status=ReviewJobStatus.QUEUED,
            cancel_requested=False,
            error_code=None,
            error=None,
        )
        self._event(updated, "job_retried")
        return updated

    def _commit_denied_model_fallback(self, state: ReviewJobState) -> ReviewJobState:
        payload = {
            "schema_version": "peerassist.no_model_agent_result.v1",
            "status": "degraded",
            "degradation_code": "service_denied_local_fallback",
            "service": "model",
            "reason": state.model_consent.reason,
        }
        self._commit_stage(state, payload)
        current = self.repository.get(state.id)
        degraded_services = list(dict.fromkeys([*current.degraded_services, "model"]))
        next_stage = _NEXT[state.stage]
        updated = self._update(
            current,
            stage=next_stage,
            status=ReviewJobStatus.QUEUED,
            degradation_code="service_denied_local_fallback",
            degraded_services=degraded_services,
            degraded_at=datetime.now(UTC),
        )
        self._event(updated, "consent_denied_degraded", payload={"service": "model"})
        return updated

    def _commit_stage(self, state: ReviewJobState, payload: dict[str, Any]) -> StageManifest:
        relative_output = PurePosixPath(
            "attempts",
            state.attempt_id,
            "stages",
            state.stage.value,
            "outputs",
        )
        job_root = self.repository.jobs_dir / str(state.id)
        output_dir = job_root.joinpath(*relative_output.parts)
        output_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = output_dir / "result.json"
        write_json_atomic(artifact_path, payload)
        content = artifact_path.read_bytes()
        committed_at = datetime.now(UTC)
        manifest = StageManifest(
            stage=state.stage,
            attempt_id=state.attempt_id,
            checkpoint_path=str(relative_output.parent / "checkpoint.json"),
            output_dir=str(relative_output),
            artifacts={"result": "result.json"},
            artifact_sha256={"result": hashlib.sha256(content).hexdigest()},
            artifact_sizes={"result": len(content)},
            committed_at=committed_at,
        )
        checkpoint = StageCheckpoint(
            stage=state.stage,
            attempt_id=state.attempt_id,
            status=_STATUS[state.stage],
            committed=True,
            committed_at=committed_at,
            manifest=manifest,
        )
        checkpoint_path = job_root.joinpath(*PurePosixPath(manifest.checkpoint_path).parts)
        write_json_atomic(checkpoint_path, checkpoint.model_dump(mode="json"))
        return self.repository.commit_stage_outputs(state.id, manifest)

    def _update(self, state: ReviewJobState, **changes: Any) -> ReviewJobState:
        return self.repository.update(
            state.id,
            expected_revision=state.revision,
            **changes,
        )

    def _event(
        self,
        state: ReviewJobState,
        event_type: str,
        *,
        message: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.repository.append_event(
            state.id,
            event_type,
            stage=state.stage,
            status=state.status,
            attempt_id=state.attempt_id,
            message=message,
            payload=payload,
        )

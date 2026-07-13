from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from common.types import JobState
from schemas.peerassist_jobs import (
    ExternalServiceConsent,
    ReviewJobState,
    ReviewJobStatus,
    ReviewStage,
    StageCheckpoint,
    StageManifest,
)

PAPER_SHA = "a" * 64
ARTIFACT_SHA = "b" * 64


def _job_contract(**overrides: object) -> ReviewJobState:
    job_id = uuid4()
    values: dict[str, object] = {
        "id": job_id,
        "paper_id": PAPER_SHA,
        "run_dir": f"data/jobs/{job_id}/run",
        "mode": "fast",
        "stage": ReviewStage.VALIDATE,
        "status": ReviewJobStatus.QUEUED,
        "attempt_id": "attempt-1",
    }
    values.update(overrides)
    return ReviewJobState.model_validate(values)


def test_review_job_contract_requires_full_paper_sha_and_safe_run_directory() -> None:
    state = _job_contract()

    assert state.paper_id == PAPER_SHA
    assert state.run_dir.endswith(f"/{state.id}/run")

    with pytest.raises(ValidationError, match="paper_id"):
        _job_contract(paper_id=PAPER_SHA[:16])
    with pytest.raises(ValidationError, match="paper_id"):
        _job_contract(paper_id="A" * 64)
    with pytest.raises(ValidationError, match="run_dir"):
        _job_contract(run_dir="../outside")


def test_review_job_contract_defaults_revisions_and_persists_consent_gate() -> None:
    parse_consent = ExternalServiceConsent(
        decision="granted",
        service="mineru",
        decided_by="reviewer-1",
        reason="Use OCR for scanned pages.",
    )
    state = _job_contract(
        status=ReviewJobStatus.BLOCKED,
        parse_consent=parse_consent,
        search_consent={"decision": "denied", "service": "semantic-scholar"},
        model_consent={"decision": "pending", "service": "review-model"},
        blocked_reason="approval_required",
        required_consents=["model"],
        resume_stage=ReviewStage.AGENTS,
    )

    restored = ReviewJobState.model_validate_json(state.model_dump_json())

    assert restored.revision == 0
    assert restored.last_event_id == 0
    assert restored.confirmation_revision == 0
    assert restored.cancel_requested is False
    assert restored.current_stage_manifests == {}
    assert restored.parse_consent == parse_consent
    assert restored.search_consent.decision == "denied"
    assert restored.model_consent.decision == "pending"
    assert restored.blocked_reason == "approval_required"
    assert restored.required_consents == ["model"]
    assert restored.resume_stage is ReviewStage.AGENTS


def test_stage_checkpoint_contract_validates_committed_manifest_consistency() -> None:
    committed_at = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    manifest = StageManifest(
        stage=ReviewStage.PARSE,
        attempt_id="attempt-1",
        checkpoint_path="attempts/attempt-1/stages/parse/checkpoint.json",
        output_dir="attempts/attempt-1/stages/parse/outputs",
        artifacts={"paper": "paper.json"},
        artifact_sha256={"paper": ARTIFACT_SHA},
        artifact_sizes={"paper": 128},
        committed_at=committed_at,
    )
    checkpoint = StageCheckpoint(
        stage=ReviewStage.PARSE,
        attempt_id="attempt-1",
        status="completed",
        committed=True,
        committed_at=committed_at,
        manifest=manifest,
    )

    assert checkpoint.manifest.stage is checkpoint.stage
    assert checkpoint.committed_at == checkpoint.manifest.committed_at

    with pytest.raises(ValidationError, match="stage"):
        StageCheckpoint(
            stage=ReviewStage.EVIDENCE,
            attempt_id="attempt-1",
            status="completed",
            committed=True,
            committed_at=committed_at,
            manifest=manifest,
        )
    with pytest.raises(ValidationError, match="attempt_id"):
        StageCheckpoint(
            stage=ReviewStage.PARSE,
            attempt_id="attempt-2",
            status="completed",
            committed=True,
            committed_at=committed_at,
            manifest=manifest,
        )
    with pytest.raises(ValidationError, match="manifest"):
        StageCheckpoint(
            stage=ReviewStage.PARSE,
            attempt_id="attempt-1",
            status="completed",
            committed=True,
            committed_at=committed_at,
        )
    with pytest.raises(ValidationError, match="path"):
        StageManifest(
            stage=ReviewStage.PARSE,
            attempt_id="attempt-1",
            checkpoint_path="../../checkpoint.json",
            output_dir="attempts/attempt-1/stages/parse/outputs",
        )


@pytest.mark.parametrize(
    ("artifact_sha256", "artifact_sizes"),
    [
        ({}, {"paper": 128}),
        ({"paper": ARTIFACT_SHA}, {}),
        ({"paper": ARTIFACT_SHA, "extra": "c" * 64}, {"paper": 128}),
        ({"paper": ARTIFACT_SHA}, {"paper": 128, "extra": 1}),
    ],
)
def test_stage_manifest_contract_requires_exact_artifact_metadata_sets(
    artifact_sha256: dict[str, str], artifact_sizes: dict[str, int]
) -> None:
    with pytest.raises(ValidationError, match="artifact"):
        StageManifest(
            stage=ReviewStage.PARSE,
            attempt_id="attempt-1",
            checkpoint_path="attempts/attempt-1/stages/parse/checkpoint.json",
            output_dir="attempts/attempt-1/stages/parse/outputs",
            artifacts={"paper": "paper.json"},
            artifact_sha256=artifact_sha256,
            artifact_sizes=artifact_sizes,
        )


def test_stage_checkpoint_contract_requires_matching_committed_timestamp() -> None:
    committed_at = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    manifest = StageManifest(
        stage=ReviewStage.PARSE,
        attempt_id="attempt-1",
        checkpoint_path="attempts/attempt-1/stages/parse/checkpoint.json",
        output_dir="attempts/attempt-1/stages/parse/outputs",
        committed_at=committed_at,
    )

    with pytest.raises(ValidationError, match="committed_at"):
        StageCheckpoint(
            stage=ReviewStage.PARSE,
            attempt_id="attempt-1",
            status="completed",
            committed=True,
            manifest=manifest,
        )
    with pytest.raises(ValidationError, match="committed_at"):
        StageCheckpoint(
            stage=ReviewStage.PARSE,
            attempt_id="attempt-1",
            status="completed",
            committed=True,
            committed_at=committed_at + timedelta(seconds=1),
            manifest=manifest,
        )


def test_review_job_status_contract_includes_startup_recovery_states() -> None:
    assert ReviewJobStatus.CANCEL_REQUESTED == "cancel_requested"
    assert ReviewJobStatus.INTERRUPTED == "interrupted"


def test_legacy_job_state_load_contract_preserves_artifacts_and_accepts_migration_metadata() -> None:
    legacy_payload = {
        "title": "Legacy review",
        "source_pdf_name": "paper.pdf",
        "artifacts": {
            "source_pdf_path": "/legacy/jobs/source.pdf",
            "final_markdown_path": "/legacy/jobs/final.md",
        },
    }

    legacy = JobState.model_validate(legacy_payload)
    migrated = JobState.model_validate(
        {
            **legacy_payload,
            "schema_version": "legacy.job_state.v1",
            "migrated_to_schema_version": "peerassist.review_job.v2",
            "paper_id": PAPER_SHA,
            "run_dir": "/legacy/jobs/run",
            "mode": "fast",
            "stage": "awaiting_human_confirmation",
            "revision": 7,
            "checkpoint": "stages/peerassist/checkpoint.json",
            "error_code": None,
        }
    )

    assert legacy.artifacts.source_pdf_path == "/legacy/jobs/source.pdf"
    assert legacy.artifacts.final_markdown_path == "/legacy/jobs/final.md"
    assert migrated.artifacts == legacy.artifacts
    assert migrated.schema_version == "legacy.job_state.v1"
    assert migrated.migrated_to_schema_version == "peerassist.review_job.v2"
    assert migrated.revision == 7

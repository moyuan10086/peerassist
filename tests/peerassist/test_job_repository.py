from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import stat
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from queue import Empty
from uuid import uuid4

import pytest
from pydantic import ValidationError

import peerassist.job_repository as job_repository
from common.storage import write_json_atomic
from common.types import JobState
from peerassist.job_repository import (
    ManifestValidationError,
    PaperRepository,
    RepositoryConflictError,
    RepositoryCorruptionError,
    RepositoryNotFoundError,
    ReviewJobRepository,
    WorkerClaim,
)
from schemas.peerassist_jobs import (
    ExternalServiceConsent,
    FinalReportManifest,
    PaperRecord,
    Principal,
    ResourceGrant,
    ReviewJobEvent,
    ReviewJobState,
    ReviewJobStatus,
    ReviewStage,
    SessionRecord,
    StageCheckpoint,
    StageManifest,
)

PAPER_SHA = "a" * 64
ARTIFACT_SHA = "b" * 64


def _cas_process(
    data_dir: str,
    job_id: str,
    barrier: multiprocessing.synchronize.Barrier,
    results: multiprocessing.queues.Queue,
) -> None:
    repository = ReviewJobRepository(Path(data_dir))
    barrier.wait()
    try:
        state = repository.update(
            job_id,
            expected_revision=0,
            status=ReviewJobStatus.PARSING,
        )
    except RepositoryConflictError:
        results.put("conflict")
    else:
        results.put(f"updated:{state.revision}")


def _append_events_process(
    data_dir: str,
    job_id: str,
    process_name: str,
    count: int,
    barrier: multiprocessing.synchronize.Barrier,
) -> None:
    repository = ReviewJobRepository(Path(data_dir))
    barrier.wait()
    for index in range(count):
        repository.append_event(
            job_id,
            "process_event",
            payload={"process": process_name, "index": index},
        )


def _claim_process(
    data_dir: str,
    job_id: str,
    owner: str,
    barrier: multiprocessing.synchronize.Barrier,
    results: multiprocessing.queues.Queue,
) -> None:
    repository = ReviewJobRepository(Path(data_dir))
    barrier.wait()
    claim = repository.claim(job_id, owner=owner, lease_seconds=30)
    results.put(None if claim is None else claim.model_dump(mode="json"))


def _atomic_writer_process(
    destination: str,
    value: int,
    barrier: multiprocessing.synchronize.Barrier,
    temp_paths: multiprocessing.queues.Queue,
) -> None:
    import common.storage as storage

    original_replace = storage.os.replace

    def synchronized_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        temp_paths.put(os.fspath(source))
        barrier.wait()
        original_replace(source, target)

    storage.os.replace = synchronized_replace
    storage.write_json_atomic(Path(destination), {"value": value})


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


def test_review_job_contract_rejects_absolute_run_directory() -> None:
    job_id = uuid4()

    with pytest.raises(ValidationError, match="run_dir"):
        _job_contract(id=job_id, run_dir=f"/data/jobs/{job_id}/run")


def test_review_job_contract_rejects_run_directory_for_another_job() -> None:
    job_id = uuid4()

    with pytest.raises(ValidationError, match="run_dir"):
        _job_contract(id=job_id, run_dir=f"data/jobs/{uuid4()}/run")


@pytest.mark.parametrize("unknown_field", ["expected_revision", "last_eventd_id"])
def test_review_job_contract_rejects_unknown_cas_and_event_fields(unknown_field: str) -> None:
    with pytest.raises(ValidationError, match=unknown_field):
        _job_contract(**{unknown_field: 0})


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (ExternalServiceConsent, {}),
        (PaperRecord, {"paper_id": PAPER_SHA}),
        (
            StageManifest,
            {
                "stage": "parse",
                "attempt_id": "attempt-1",
                "checkpoint_path": "attempts/attempt-1/stages/parse/checkpoint.json",
                "output_dir": "attempts/attempt-1/stages/parse/outputs",
            },
        ),
        (StageCheckpoint, {"stage": "parse", "attempt_id": "attempt-1", "status": "queued"}),
        (
            ReviewJobState,
            {
                "paper_id": PAPER_SHA,
                "run_dir": "data/jobs/00000000-0000-0000-0000-000000000001/run",
                "attempt_id": "attempt-1",
                "id": "00000000-0000-0000-0000-000000000001",
            },
        ),
        (
            ReviewJobEvent,
            {
                "job_id": "00000000-0000-0000-0000-000000000001",
                "event_id": 1,
                "event_type": "created",
            },
        ),
        (
            FinalReportManifest,
            {
                "job_id": "00000000-0000-0000-0000-000000000001",
                "paper_id": PAPER_SHA,
                "report_version": "report-1",
                "confirmation_revision": 1,
                "artifacts": {},
            },
        ),
        (Principal, {"principal_id": "reviewer-1"}),
        (
            SessionRecord,
            {
                "principal_id": "reviewer-1",
                "token_hash": "token-hash",
                "csrf_token_hash": "csrf-hash",
                "expires_at": datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
                "created_at": datetime(2026, 7, 13, 12, 0, tzinfo=UTC),
            },
        ),
        (
            ResourceGrant,
            {
                "principal_id": "reviewer-1",
                "resource_type": "job",
                "resource_id": "00000000-0000-0000-0000-000000000001",
                "role": "owner",
            },
        ),
    ],
)
def test_durable_contract_rejects_future_schema_version(
    model: type[object], payload: dict[str, object]
) -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        model.model_validate({**payload, "schema_version": "peerassist.future.v999"})


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


def test_paper_repository_create_or_get_uses_complete_source_sha(tmp_path: Path) -> None:
    repository = PaperRepository(tmp_path)
    original = PaperRecord(
        paper_id=PAPER_SHA,
        source_pdf_name="original.pdf",
        source_pdf_path="source/source.pdf",
        size_bytes=123,
    )

    created = repository.create_or_get(original)
    deduplicated = repository.create_or_get(
        original.model_copy(update={"source_pdf_name": "renamed.pdf"})
    )

    assert created == original
    assert deduplicated == original
    assert repository.get(PAPER_SHA) == original
    assert (tmp_path / "papers" / PAPER_SHA / "paper.json").is_file()
    assert not (tmp_path / "papers" / PAPER_SHA[:16]).exists()


def test_paper_repository_rejects_symlinked_identity_directory(tmp_path: Path) -> None:
    repository = PaperRepository(tmp_path)
    outside = tmp_path / "outside-paper"
    outside.mkdir()
    (tmp_path / "papers" / PAPER_SHA).symlink_to(outside, target_is_directory=True)
    record = PaperRecord(paper_id=PAPER_SHA)

    with pytest.raises(RepositoryCorruptionError, match="symlink"):
        repository.create_or_get(record)

    assert not (outside / "paper.json").exists()


def test_identity_directory_creation_fsyncs_repository_parents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper_repository = PaperRepository(tmp_path)
    job_repository = ReviewJobRepository(tmp_path)
    fsynced: list[Path] = []
    monkeypatch.setattr(paper_repository, "_fsync_directory", fsynced.append, raising=False)
    monkeypatch.setattr(job_repository, "_fsync_directory", fsynced.append)

    paper_repository.create_or_get(PaperRecord(paper_id=PAPER_SHA))
    job_repository.create(_job_contract())

    assert paper_repository.papers_dir in fsynced
    assert job_repository.jobs_dir in fsynced


def test_review_job_repository_create_read_list_and_cas(tmp_path: Path) -> None:
    repository = ReviewJobRepository(tmp_path)
    first = _job_contract()
    second = _job_contract()

    assert repository.create(first) == first
    assert repository.create(second) == second
    assert repository.get(first.id) == first
    assert [job.id for job in repository.list()] == sorted([first.id, second.id], key=str)

    updated = repository.update(
        first.id,
        expected_revision=0,
        status=ReviewJobStatus.PARSING,
        stage=ReviewStage.PARSE,
    )

    assert updated.revision == 1
    assert updated.status is ReviewJobStatus.PARSING
    assert updated.stage is ReviewStage.PARSE

    with pytest.raises(RepositoryConflictError):
        repository.update(
            first.id,
            expected_revision=0,
            status=ReviewJobStatus.FAILED,
        )

    assert repository.get(first.id) == updated


def test_review_job_repository_rejects_symlinked_job_directory(tmp_path: Path) -> None:
    repository = ReviewJobRepository(tmp_path)
    job = _job_contract()
    outside = tmp_path / "outside-job"
    outside.mkdir()
    (tmp_path / "jobs" / str(job.id)).symlink_to(outside, target_is_directory=True)

    with pytest.raises(RepositoryCorruptionError, match="symlink"):
        repository.create(job)

    assert not (outside / "job.json").exists()


def test_review_job_repository_delete_archives_entire_job_directory(tmp_path: Path) -> None:
    repository = ReviewJobRepository(tmp_path)
    job = repository.create(_job_contract())
    job_dir = tmp_path / "jobs" / str(job.id)
    artifact = job_dir / "attempts" / "attempt-1" / "artifact.bin"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"audit-me")
    repository.append_event(job.id, "created")

    archived_dir = repository.delete(job.id, expected_revision=0)

    assert archived_dir == tmp_path / "jobs" / ".deleted" / str(job.id)
    assert not job_dir.exists()
    assert job.id not in {state.id for state in repository.list()}
    with pytest.raises(RepositoryNotFoundError):
        repository.get(job.id)
    assert (archived_dir / "job.json").is_file()
    assert (archived_dir / "events.jsonl").is_file()
    assert (archived_dir / "attempts" / "attempt-1" / "artifact.bin").read_bytes() == b"audit-me"


def test_review_job_repository_stale_delete_conflicts_without_archiving(tmp_path: Path) -> None:
    repository = ReviewJobRepository(tmp_path)
    job = repository.create(_job_contract())
    artifact = tmp_path / "jobs" / str(job.id) / "run" / "keep.txt"
    artifact.write_text("keep", encoding="utf-8")
    updated = repository.update(
        job.id,
        expected_revision=0,
        status=ReviewJobStatus.PARSING,
    )

    with pytest.raises(RepositoryConflictError, match="revision"):
        repository.delete(job.id, expected_revision=0)

    assert repository.get(job.id) == updated
    assert artifact.read_text(encoding="utf-8") == "keep"
    assert not (tmp_path / "jobs" / ".deleted" / str(job.id)).exists()


def test_review_job_repository_create_rejects_deleted_job_id(tmp_path: Path) -> None:
    repository = ReviewJobRepository(tmp_path)
    job = _job_contract()
    tombstone = tmp_path / "jobs" / ".deleted" / str(job.id)
    tombstone.mkdir()

    with pytest.raises(RepositoryConflictError, match="deleted"):
        repository.create(job)

    assert not (tmp_path / "jobs" / str(job.id)).exists()


def test_stale_cas_across_processes_has_one_winner_without_mutation(tmp_path: Path) -> None:
    repository = ReviewJobRepository(tmp_path)
    job = repository.create(_job_contract())
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    results = context.Queue()
    processes = [
        context.Process(target=_cas_process, args=(str(tmp_path), str(job.id), barrier, results))
        for _ in range(2)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)

    assert all(process.exitcode == 0 for process in processes)
    assert sorted(results.get(timeout=1) for _ in processes) == ["conflict", "updated:1"]
    assert repository.get(job.id).revision == 1


def test_event_ids_are_monotonic_and_unique_across_processes(tmp_path: Path) -> None:
    repository = ReviewJobRepository(tmp_path)
    job = repository.create(_job_contract())
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    processes = [
        context.Process(
            target=_append_events_process,
            args=(str(tmp_path), str(job.id), name, 20, barrier),
        )
        for name in ("first", "second")
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)

    assert all(process.exitcode == 0 for process in processes)
    events = repository.replay_events(job.id)
    assert [event.event_id for event in events] == list(range(1, 41))
    assert len({event.event_id for event in events}) == 40
    assert {event.payload["process"] for event in events} == {"first", "second"}
    assert repository.get(job.id).last_event_id == 40


def test_event_replay_after_last_event_id_and_damaged_tail_recovery(tmp_path: Path) -> None:
    repository = ReviewJobRepository(tmp_path)
    job = repository.create(_job_contract())
    repository.append_event(job.id, "created")
    repository.append_event(job.id, "started")
    repository.append_event(job.id, "checkpoint")

    replay = repository.replay_events(job.id, after_event_id=1)
    assert [event.event_type for event in replay] == ["started", "checkpoint"]

    events_path = tmp_path / "jobs" / str(job.id) / "events.jsonl"
    valid_size = events_path.stat().st_size
    damaged_tail = b'{"event_id": 4, "event_type": "damaged"'
    with events_path.open("ab") as stream:
        stream.write(damaged_tail)
        stream.flush()
        os.fsync(stream.fileno())

    recovered = repository.replay_events(job.id)

    assert [event.event_id for event in recovered] == [1, 2, 3, 4]
    warning = recovered[-1]
    assert warning.event_type == "event_tail_recovered"
    assert warning.payload["recovered_bytes"] == len(damaged_tail)
    assert "damaged" in warning.payload["context"]
    assert events_path.read_bytes()[:valid_size].endswith(b"\n")
    assert repository.get(job.id).last_event_id == 4
    assert repository.append_event(job.id, "resumed").event_id == 5


def test_event_replay_reconciles_last_event_id_after_post_append_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = ReviewJobRepository(tmp_path)
    job = repository.create(_job_contract())

    def fail_state_update(*args: object, **kwargs: object) -> None:
        raise OSError("injected state failure")

    monkeypatch.setattr(repository, "_update_last_event_id", fail_state_update)
    with pytest.raises(OSError, match="injected state failure"):
        repository.append_event(job.id, "committed-before-crash")
    monkeypatch.undo()

    assert ReviewJobState.model_validate(
        json.loads((tmp_path / "jobs" / str(job.id) / "job.json").read_text(encoding="utf-8"))
    ).last_event_id == 0
    assert [event.event_id for event in repository.replay_events(job.id)] == [1]
    assert repository.get(job.id).last_event_id == 1


def test_get_reconciles_last_event_id_to_validated_event_tail(tmp_path: Path) -> None:
    repository = ReviewJobRepository(tmp_path)
    job = repository.create(_job_contract())
    repository.append_event(job.id, "created")
    state_path = tmp_path / "jobs" / str(job.id) / "job.json"
    stale = ReviewJobState.model_validate(json.loads(state_path.read_text(encoding="utf-8")))
    write_json_atomic(state_path, stale.model_copy(update={"last_event_id": 9}).model_dump(mode="json"))

    reconciled = repository.get(job.id)

    assert reconciled.last_event_id == 1
    assert ReviewJobState.model_validate(json.loads(state_path.read_text(encoding="utf-8"))) == reconciled


def test_events_jsonl_symlink_is_rejected_for_append_and_replay(tmp_path: Path) -> None:
    repository = ReviewJobRepository(tmp_path)
    job = repository.create(_job_contract())
    outside = tmp_path / "outside-events.jsonl"
    outside.write_bytes(b'{"damaged": true')
    original = outside.read_bytes()
    events_path = tmp_path / "jobs" / str(job.id) / "events.jsonl"
    events_path.symlink_to(outside)

    with pytest.raises(RepositoryCorruptionError, match="symlink"):
        repository.append_event(job.id, "unsafe")
    with pytest.raises(RepositoryCorruptionError, match="symlink"):
        repository.replay_events(job.id)

    assert events_path.is_symlink()
    assert outside.read_bytes() == original


def test_worker_claim_is_exclusive_across_processes_and_releasable(tmp_path: Path) -> None:
    repository = ReviewJobRepository(tmp_path)
    job = repository.create(_job_contract())
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    results = context.Queue()
    processes = [
        context.Process(
            target=_claim_process,
            args=(str(tmp_path), str(job.id), owner, barrier, results),
        )
        for owner in ("worker-1", "worker-2")
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)

    assert all(process.exitcode == 0 for process in processes)
    claims = [results.get(timeout=1) for _ in processes]
    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    winner = winners[0]
    assert winner["owner"] in {"worker-1", "worker-2"}
    assert winner["token"]
    assert repository.release_claim(job.id, owner=winner["owner"], token="wrong") is False
    assert repository.release_claim(
        job.id,
        owner=winner["owner"],
        token=winner["token"],
    )
    assert repository.claim(job.id, owner="worker-3", lease_seconds=30) is not None


def test_worker_claim_symlink_is_rejected_for_claim_and_release(tmp_path: Path) -> None:
    repository = ReviewJobRepository(tmp_path)
    job = repository.create(_job_contract())
    now = datetime.now(UTC)
    outside = tmp_path / "outside-worker-claim.json"
    claim = WorkerClaim(
        job_id=job.id,
        owner="worker-1",
        token="outside-token",
        acquired_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    write_json_atomic(outside, claim.model_dump(mode="json"))
    original = outside.read_bytes()
    claim_path = tmp_path / "jobs" / str(job.id) / "worker_claim.json"
    claim_path.symlink_to(outside)

    with pytest.raises(RepositoryCorruptionError, match="symlink"):
        repository.claim(job.id, owner="worker-2", lease_seconds=30)
    with pytest.raises(RepositoryCorruptionError, match="symlink"):
        repository.release_claim(job.id, owner=claim.owner, token=claim.token)

    assert claim_path.is_symlink()
    assert outside.read_bytes() == original


def test_expired_worker_claim_can_be_reclaimed(tmp_path: Path) -> None:
    repository = ReviewJobRepository(tmp_path)
    job = repository.create(_job_contract())

    first = repository.claim(job.id, owner="worker-1", lease_seconds=0.01)
    assert first is not None
    time.sleep(0.03)
    second = repository.claim(job.id, owner="worker-2", lease_seconds=30)

    assert second is not None
    assert second.owner == "worker-2"
    assert second.token != first.token


def test_atomic_writes_use_unique_temporary_files_across_processes(tmp_path: Path) -> None:
    destination = tmp_path / "state.json"
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    temp_paths = context.Queue()
    processes = [
        context.Process(
            target=_atomic_writer_process,
            args=(str(destination), value, barrier, temp_paths),
        )
        for value in (1, 2)
    ]

    for process in processes:
        process.start()
    try:
        paths = [Path(temp_paths.get(timeout=5)) for _ in processes]
    except Empty:
        for process in processes:
            process.terminate()
        pytest.fail("atomic writers did not reach replacement")
    for process in processes:
        process.join(timeout=10)

    assert all(process.exitcode == 0 for process in processes)
    assert len(set(paths)) == 2
    assert all(path.parent == destination.parent for path in paths)
    assert json.loads(destination.read_text(encoding="utf-8"))["value"] in {1, 2}
    assert not list(tmp_path.glob("*.tmp"))


def _stage_manifest(
    job_id: object,
    *,
    attempt_id: str,
    content: bytes,
) -> StageManifest:
    relative_output = f"attempts/{attempt_id}/stages/parse/outputs"
    return StageManifest(
        stage=ReviewStage.PARSE,
        attempt_id=attempt_id,
        checkpoint_path=f"attempts/{attempt_id}/stages/parse/checkpoint.json",
        output_dir=relative_output,
        artifacts={"paper": "paper.json"},
        artifact_sha256={"paper": hashlib.sha256(content).hexdigest()},
        artifact_sizes={"paper": len(content)},
    )


def _write_stage_output(
    data_dir: Path,
    job_id: object,
    attempt_id: str,
    content: bytes,
) -> Path:
    output = (
        data_dir
        / "jobs"
        / str(job_id)
        / "attempts"
        / attempt_id
        / "stages"
        / "parse"
        / "outputs"
        / "paper.json"
    )
    output.parent.mkdir(parents=True)
    output.write_bytes(content)
    return output


def _write_stage_checkpoint(
    data_dir: Path,
    job_id: object,
    manifest: StageManifest,
    **overrides: object,
) -> Path:
    values: dict[str, object] = {
        "stage": manifest.stage,
        "attempt_id": manifest.attempt_id,
        "status": ReviewJobStatus.COMPLETED,
        "committed": True,
        "committed_at": manifest.committed_at,
        "manifest": manifest,
    }
    values.update(overrides)
    checkpoint = StageCheckpoint.model_validate(values)
    path = data_dir / "jobs" / str(job_id) / manifest.checkpoint_path
    write_json_atomic(path, checkpoint.model_dump(mode="json"))
    return path


def test_commit_stage_manifest_validates_then_materializes_read_only_view(tmp_path: Path) -> None:
    repository = ReviewJobRepository(tmp_path)
    job = repository.create(_job_contract())
    content = b'{"attempt": 1}\n'
    attempt_output = _write_stage_output(tmp_path, job.id, "attempt-1", content)
    manifest = _stage_manifest(job.id, attempt_id="attempt-1", content=content)
    _write_stage_checkpoint(tmp_path, job.id, manifest)

    committed = repository.commit_stage_outputs(job.id, manifest)

    pointer = tmp_path / "jobs" / str(job.id) / "current_stages" / "parse.json"
    immutable = (
        tmp_path / "jobs" / str(job.id) / "manifests" / "parse" / "attempt-1.json"
    )
    compatibility = tmp_path / "jobs" / str(job.id) / "run" / "stages" / "parse" / "paper.json"
    assert committed == manifest
    assert repository.current_stage_manifest(job.id, ReviewStage.PARSE) == manifest
    assert StageManifest.model_validate_json(pointer.read_text(encoding="utf-8")) == manifest
    assert StageManifest.model_validate_json(immutable.read_text(encoding="utf-8")) == manifest
    assert compatibility.read_bytes() == content
    assert os.path.samefile(attempt_output, compatibility)
    assert stat.S_IMODE(compatibility.stat().st_mode) & 0o222 == 0
    assert attempt_output.read_bytes() == content
    assert repository.get(job.id).current_stage_manifests == {ReviewStage.PARSE: manifest}


def test_manifest_pointer_failure_preserves_previous_commit_and_attempts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = ReviewJobRepository(tmp_path)
    job = repository.create(_job_contract())
    first_content = b'{"attempt": 1}\n'
    second_content = b'{"attempt": 2}\n'
    first_output = _write_stage_output(tmp_path, job.id, "attempt-1", first_content)
    second_output = _write_stage_output(tmp_path, job.id, "attempt-2", second_content)
    first_manifest = _stage_manifest(job.id, attempt_id="attempt-1", content=first_content)
    second_manifest = _stage_manifest(job.id, attempt_id="attempt-2", content=second_content)
    _write_stage_checkpoint(tmp_path, job.id, first_manifest)
    _write_stage_checkpoint(tmp_path, job.id, second_manifest)
    repository.commit_stage_outputs(job.id, first_manifest)

    def fail_before_replace(*args: object, **kwargs: object) -> None:
        raise OSError("injected pointer failure")

    monkeypatch.setattr(repository, "_replace_current_stage_pointer", fail_before_replace)

    with pytest.raises(OSError, match="injected pointer failure"):
        repository.commit_stage_outputs(job.id, second_manifest)

    compatibility = tmp_path / "jobs" / str(job.id) / "run" / "stages" / "parse" / "paper.json"
    assert repository.current_stage_manifest(job.id, ReviewStage.PARSE) == first_manifest
    assert compatibility.read_bytes() == first_content
    assert first_output.read_bytes() == first_content
    assert second_output.read_bytes() == second_content
    assert (
        tmp_path / "jobs" / str(job.id) / "manifests" / "parse" / "attempt-2.json"
    ).is_file()
    unpublished = (
        tmp_path
        / "jobs"
        / str(job.id)
        / "compatibility_views"
        / "parse"
        / "attempt-2"
        / "paper.json"
    )
    assert os.path.samefile(second_output, unpublished)


def test_post_pointer_failure_is_reconciled_without_rolling_back_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = ReviewJobRepository(tmp_path)
    job = repository.create(_job_contract())
    first_content = b'{"attempt": 1}\n'
    second_content = b'{"attempt": 2}\n'
    _write_stage_output(tmp_path, job.id, "attempt-1", first_content)
    second_output = _write_stage_output(tmp_path, job.id, "attempt-2", second_content)
    first_manifest = _stage_manifest(job.id, attempt_id="attempt-1", content=first_content)
    second_manifest = _stage_manifest(job.id, attempt_id="attempt-2", content=second_content)
    _write_stage_checkpoint(tmp_path, job.id, first_manifest)
    _write_stage_checkpoint(tmp_path, job.id, second_manifest)
    repository.commit_stage_outputs(job.id, first_manifest)
    original_write_json_atomic = job_repository.write_json_atomic

    def fail_state_write(path: Path, payload: dict[str, object]) -> None:
        if Path(path).name == "job.json":
            raise OSError("injected post-pointer state failure")
        original_write_json_atomic(path, payload)

    monkeypatch.setattr(job_repository, "write_json_atomic", fail_state_write)
    with pytest.raises(OSError, match="post-pointer"):
        repository.commit_stage_outputs(job.id, second_manifest)
    monkeypatch.undo()

    root = tmp_path / "jobs" / str(job.id)
    pointer = StageManifest.model_validate_json(
        (root / "current_stages" / "parse.json").read_text(encoding="utf-8")
    )
    stale_state = ReviewJobState.model_validate_json((root / "job.json").read_text(encoding="utf-8"))
    published = root / "run" / "stages" / "parse" / "paper.json"
    assert pointer == second_manifest
    assert stale_state.current_stage_manifests == {ReviewStage.PARSE: first_manifest}
    assert published.read_bytes() == first_content

    reconciled = repository.get(job.id)

    assert reconciled.current_stage_manifests == {ReviewStage.PARSE: second_manifest}
    assert repository.current_stage_manifest(job.id, ReviewStage.PARSE) == second_manifest
    assert published.read_bytes() == second_content
    assert os.path.samefile(second_output, published)


def test_manifest_commit_rejects_injected_regular_compatibility_artifact(
    tmp_path: Path,
) -> None:
    repository = ReviewJobRepository(tmp_path)
    job = repository.create(_job_contract())
    content = b'{"attempt": 1}\n'
    output = _write_stage_output(tmp_path, job.id, "attempt-1", content)
    manifest = _stage_manifest(job.id, attempt_id="attempt-1", content=content)
    _write_stage_checkpoint(tmp_path, job.id, manifest)
    injected = (
        tmp_path
        / "jobs"
        / str(job.id)
        / "compatibility_views"
        / "parse"
        / "attempt-1"
        / "paper.json"
    )
    injected.parent.mkdir(parents=True)
    injected.write_bytes(content)

    with pytest.raises(ManifestValidationError, match="hard link"):
        repository.commit_stage_outputs(job.id, manifest)

    assert not os.path.samefile(output, injected)
    assert repository.current_stage_manifest(job.id, ReviewStage.PARSE) is None


def test_manifest_validation_rejects_hash_mismatch_and_symlink_escape(tmp_path: Path) -> None:
    repository = ReviewJobRepository(tmp_path)
    job = repository.create(_job_contract())
    content = b'{"attempt": 1}\n'
    output = _write_stage_output(tmp_path, job.id, "attempt-1", content)
    manifest = _stage_manifest(job.id, attempt_id="attempt-1", content=content)
    _write_stage_checkpoint(tmp_path, job.id, manifest)

    output.write_bytes(b"tampered")
    with pytest.raises(ManifestValidationError, match=r"size|SHA"):
        repository.commit_stage_outputs(job.id, manifest)

    outside = tmp_path / "outside.json"
    outside.write_bytes(content)
    output.unlink()
    output.symlink_to(outside)
    with pytest.raises(ManifestValidationError, match=r"symlink|escape"):
        repository.commit_stage_outputs(job.id, manifest)

    assert repository.current_stage_manifest(job.id, ReviewStage.PARSE) is None


def test_manifest_validation_rejects_symlinked_attempt_output_directory(tmp_path: Path) -> None:
    repository = ReviewJobRepository(tmp_path)
    job = repository.create(_job_contract())
    content = b'{"outside": true}\n'
    outside = tmp_path / "outside-outputs"
    outside.mkdir()
    (outside / "paper.json").write_bytes(content)
    output_dir = (
        tmp_path
        / "jobs"
        / str(job.id)
        / "attempts"
        / "attempt-1"
        / "stages"
        / "parse"
        / "outputs"
    )
    output_dir.parent.mkdir(parents=True)
    output_dir.symlink_to(outside, target_is_directory=True)
    manifest = _stage_manifest(job.id, attempt_id="attempt-1", content=content)
    _write_stage_checkpoint(tmp_path, job.id, manifest)

    with pytest.raises(ManifestValidationError, match=r"symlink|escape"):
        repository.commit_stage_outputs(job.id, manifest)

    assert repository.current_stage_manifest(job.id, ReviewStage.PARSE) is None


def test_manifest_commit_requires_checkpoint_file(tmp_path: Path) -> None:
    repository = ReviewJobRepository(tmp_path)
    job = repository.create(_job_contract())
    content = b'{"attempt": 1}\n'
    _write_stage_output(tmp_path, job.id, "attempt-1", content)
    manifest = _stage_manifest(job.id, attempt_id="attempt-1", content=content)

    with pytest.raises(ManifestValidationError, match="checkpoint"):
        repository.commit_stage_outputs(job.id, manifest)


def test_manifest_commit_rejects_invalid_checkpoint_json(tmp_path: Path) -> None:
    repository = ReviewJobRepository(tmp_path)
    job = repository.create(_job_contract())
    content = b'{"attempt": 1}\n'
    _write_stage_output(tmp_path, job.id, "attempt-1", content)
    manifest = _stage_manifest(job.id, attempt_id="attempt-1", content=content)
    checkpoint = tmp_path / "jobs" / str(job.id) / manifest.checkpoint_path
    checkpoint.write_text("{invalid", encoding="utf-8")

    with pytest.raises(ManifestValidationError, match="checkpoint"):
        repository.commit_stage_outputs(job.id, manifest)


def test_manifest_commit_rejects_uncommitted_checkpoint(tmp_path: Path) -> None:
    repository = ReviewJobRepository(tmp_path)
    job = repository.create(_job_contract())
    content = b'{"attempt": 1}\n'
    _write_stage_output(tmp_path, job.id, "attempt-1", content)
    manifest = _stage_manifest(job.id, attempt_id="attempt-1", content=content)
    _write_stage_checkpoint(tmp_path, job.id, manifest, committed=False)

    with pytest.raises(ManifestValidationError, match="committed"):
        repository.commit_stage_outputs(job.id, manifest)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("stage", ReviewStage.EVIDENCE),
        ("attempt_id", "attempt-2"),
    ],
)
def test_manifest_commit_rejects_checkpoint_identity_mismatch(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    repository = ReviewJobRepository(tmp_path)
    job = repository.create(_job_contract())
    content = b'{"attempt": 1}\n'
    _write_stage_output(tmp_path, job.id, "attempt-1", content)
    manifest = _stage_manifest(job.id, attempt_id="attempt-1", content=content)
    checkpoint = StageCheckpoint(
        stage=manifest.stage,
        attempt_id=manifest.attempt_id,
        status=ReviewJobStatus.COMPLETED,
        committed=True,
        committed_at=manifest.committed_at,
        manifest=manifest,
    ).model_dump(mode="json")
    checkpoint[field] = value.value if isinstance(value, ReviewStage) else value
    checkpoint_path = tmp_path / "jobs" / str(job.id) / manifest.checkpoint_path
    write_json_atomic(checkpoint_path, checkpoint)

    with pytest.raises(ManifestValidationError, match="checkpoint"):
        repository.commit_stage_outputs(job.id, manifest)


def test_manifest_commit_rejects_checkpoint_manifest_mismatch(tmp_path: Path) -> None:
    repository = ReviewJobRepository(tmp_path)
    job = repository.create(_job_contract())
    content = b'{"attempt": 1}\n'
    _write_stage_output(tmp_path, job.id, "attempt-1", content)
    manifest = _stage_manifest(job.id, attempt_id="attempt-1", content=content)
    different_manifest = manifest.model_copy(
        update={"artifacts": {}, "artifact_sha256": {}, "artifact_sizes": {}}
    )
    _write_stage_checkpoint(
        tmp_path,
        job.id,
        different_manifest,
    )

    with pytest.raises(ManifestValidationError, match="manifest"):
        repository.commit_stage_outputs(job.id, manifest)


def test_manifest_commit_rejects_symlinked_checkpoint(tmp_path: Path) -> None:
    repository = ReviewJobRepository(tmp_path)
    job = repository.create(_job_contract())
    content = b'{"attempt": 1}\n'
    _write_stage_output(tmp_path, job.id, "attempt-1", content)
    manifest = _stage_manifest(job.id, attempt_id="attempt-1", content=content)
    outside = tmp_path / "outside-checkpoint.json"
    checkpoint = StageCheckpoint(
        stage=manifest.stage,
        attempt_id=manifest.attempt_id,
        status=ReviewJobStatus.COMPLETED,
        committed=True,
        committed_at=manifest.committed_at,
        manifest=manifest,
    )
    write_json_atomic(outside, checkpoint.model_dump(mode="json"))
    checkpoint_path = tmp_path / "jobs" / str(job.id) / manifest.checkpoint_path
    checkpoint_path.symlink_to(outside)

    with pytest.raises(ManifestValidationError, match="symlink"):
        repository.commit_stage_outputs(job.id, manifest)


def test_current_manifest_reader_rejects_symlinked_pointer(tmp_path: Path) -> None:
    repository = ReviewJobRepository(tmp_path)
    job = repository.create(_job_contract())
    content = b'{"attempt": 1}\n'
    manifest = _stage_manifest(job.id, attempt_id="attempt-1", content=content)
    outside = tmp_path / "outside-manifest.json"
    write_json_atomic(outside, manifest.model_dump(mode="json"))
    pointer_dir = tmp_path / "jobs" / str(job.id) / "current_stages"
    pointer_dir.mkdir()
    (pointer_dir / "parse.json").symlink_to(outside)

    with pytest.raises(ManifestValidationError, match="symlink"):
        repository.current_stage_manifest(job.id, ReviewStage.PARSE)


def test_legacy_atomic_writer_still_accepts_dict_payload(tmp_path: Path) -> None:
    path = tmp_path / "legacy.json"

    write_json_atomic(path, {"legacy": True})

    assert json.loads(path.read_text(encoding="utf-8")) == {"legacy": True}

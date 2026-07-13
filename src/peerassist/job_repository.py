"""Cross-process durable storage for PeerAssist papers and review jobs."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from common.config import get_settings
from common.storage import exclusive_file_lock, read_json, write_bytes_atomic, write_json_atomic
from schemas.peerassist_jobs import (
    PaperRecord,
    ReviewJobEvent,
    ReviewJobState,
    ReviewStage,
    StageCheckpoint,
    StageManifest,
)


class RepositoryError(Exception):
    """Base repository error."""


class RepositoryNotFoundError(RepositoryError):
    """Requested repository object does not exist."""


class RepositoryConflictError(RepositoryError):
    """A create or compare-and-swap operation conflicted."""


class RepositoryCorruptionError(RepositoryError):
    """Durable repository data is invalid away from a recoverable tail."""


class ManifestValidationError(RepositoryError):
    """A stage manifest does not match its declared outputs."""


class WorkerClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: UUID
    owner: str = Field(min_length=1)
    token: str = Field(min_length=1)
    acquired_at: datetime
    expires_at: datetime


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _paper_id(value: str) -> str:
    return PaperRecord(paper_id=value).paper_id


def _job_id(value: UUID | str) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"invalid job id: {value}") from exc


def _json_payload(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _ensure_repository_directory(path: Path) -> None:
    if path.is_symlink():
        raise RepositoryCorruptionError(f"repository directory is a symlink: {path.name}")
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir() or path.is_symlink():
        raise RepositoryCorruptionError(f"repository directory is unsafe: {path.name}")


class PaperRepository:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = Path(data_dir or get_settings().data_dir).resolve()
        self.papers_dir = self.data_dir / "papers"
        self.locks_dir = self.papers_dir / ".locks"
        _ensure_repository_directory(self.papers_dir)
        _ensure_repository_directory(self.locks_dir)

    def _record_path(self, paper_id: str) -> Path:
        return self.papers_dir / _paper_id(paper_id) / "paper.json"

    def create_or_get(self, record: PaperRecord) -> PaperRecord:
        validated = PaperRecord.model_validate(record)
        path = self._record_path(validated.paper_id)
        lock_path = self.locks_dir / f"{validated.paper_id}.lock"
        with exclusive_file_lock(lock_path):
            if path.parent.is_symlink():
                raise RepositoryCorruptionError("paper identity directory is a symlink")
            identity_created = not path.parent.exists()
            path.parent.mkdir(exist_ok=True)
            if identity_created:
                self._fsync_directory(self.papers_dir)
            if path.is_symlink():
                raise RepositoryCorruptionError("paper record is a symlink")
            if path.exists():
                return PaperRecord.model_validate(read_json(path))
            write_json_atomic(path, _json_payload(validated))
            return validated

    def get(self, paper_id: str) -> PaperRecord:
        path = self._record_path(paper_id)
        if path.parent.is_symlink() or path.is_symlink():
            raise RepositoryCorruptionError("paper record path is a symlink")
        if not path.is_file():
            raise RepositoryNotFoundError(f"paper not found: {paper_id}")
        return PaperRecord.model_validate(read_json(path))

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class ReviewJobRepository:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = Path(data_dir or get_settings().data_dir).resolve()
        self.jobs_dir = self.data_dir / "jobs"
        self.locks_dir = self.jobs_dir / ".locks"
        self.deleted_dir = self.jobs_dir / ".deleted"
        _ensure_repository_directory(self.jobs_dir)
        _ensure_repository_directory(self.locks_dir)
        _ensure_repository_directory(self.deleted_dir)

    def _job_dir(self, job_id: UUID | str) -> Path:
        return self.jobs_dir / str(_job_id(job_id))

    def _lock_path(self, job_id: UUID | str) -> Path:
        return self.locks_dir / f"{_job_id(job_id)}.lock"

    def _state_path(self, job_id: UUID | str) -> Path:
        return self._job_dir(job_id) / "job.json"

    def _load_state(self, job_id: UUID | str) -> ReviewJobState:
        path = self._state_path(job_id)
        if path.parent.is_symlink() or path.is_symlink():
            raise RepositoryCorruptionError("review job state path is a symlink")
        if not path.is_file():
            raise RepositoryNotFoundError(f"review job not found: {_job_id(job_id)}")
        return ReviewJobState.model_validate(read_json(path))

    def create(self, state: ReviewJobState) -> ReviewJobState:
        validated = ReviewJobState.model_validate(state)
        if validated.revision != 0:
            raise RepositoryConflictError("new review jobs must start at revision 0")
        with exclusive_file_lock(self._lock_path(validated.id)):
            path = self._state_path(validated.id)
            deleted_path = self.deleted_dir / str(validated.id)
            if deleted_path.is_symlink():
                raise RepositoryCorruptionError("deleted review job path is a symlink")
            if deleted_path.exists():
                raise RepositoryConflictError(f"review job was deleted: {validated.id}")
            if path.parent.is_symlink():
                raise RepositoryCorruptionError("review job directory is a symlink")
            if path.is_symlink():
                raise RepositoryCorruptionError("review job state is a symlink")
            if path.exists():
                raise RepositoryConflictError(f"review job already exists: {validated.id}")
            root = path.parent
            identity_created = not root.exists()
            (root / "run").mkdir(parents=True, exist_ok=True)
            if identity_created:
                self._fsync_directory(self.jobs_dir)
            write_json_atomic(path, _json_payload(validated))
        return validated

    def get(self, job_id: UUID | str) -> ReviewJobState:
        with exclusive_file_lock(self._lock_path(job_id)):
            state = self._load_state(job_id)
            state = self._reconcile_last_event_id_locked(job_id, state)
            return self._reconcile_stage_views_locked(job_id, state)

    def list(self) -> list[ReviewJobState]:
        jobs: list[ReviewJobState] = []
        for path in sorted(self.jobs_dir.iterdir(), key=lambda item: item.name):
            if path.is_symlink() or not path.is_dir() or path.name.startswith("."):
                continue
            try:
                UUID(path.name)
            except ValueError:
                continue
            state_path = path / "job.json"
            if state_path.is_file():
                jobs.append(self.get(path.name))
        return jobs

    def compare_and_swap(
        self,
        job_id: UUID | str,
        *,
        expected_revision: int,
        transform: Callable[[ReviewJobState], ReviewJobState | None],
    ) -> ReviewJobState:
        with exclusive_file_lock(self._lock_path(job_id)):
            current = self._load_state(job_id)
            if current.revision != expected_revision:
                raise RepositoryConflictError(
                    f"revision conflict: expected {expected_revision}, found {current.revision}"
                )
            candidate = current.model_copy(deep=True)
            transformed = transform(candidate)
            if transformed is not None:
                candidate = transformed
            payload = candidate.model_dump(mode="python")
            payload["id"] = current.id
            payload["revision"] = current.revision + 1
            payload["updated_at"] = _utcnow()
            updated = ReviewJobState.model_validate(payload)
            write_json_atomic(self._state_path(job_id), _json_payload(updated))
            return updated

    def update(
        self,
        job_id: UUID | str,
        *,
        expected_revision: int,
        **changes: Any,
    ) -> ReviewJobState:
        def apply_changes(state: ReviewJobState) -> ReviewJobState:
            payload = state.model_dump(mode="python")
            payload.update(changes)
            return ReviewJobState.model_validate(payload)

        return self.compare_and_swap(
            job_id,
            expected_revision=expected_revision,
            transform=apply_changes,
        )

    def delete(self, job_id: UUID | str, *, expected_revision: int) -> Path:
        with exclusive_file_lock(self._lock_path(job_id)):
            current = self._load_state(job_id)
            if current.revision != expected_revision:
                raise RepositoryConflictError(
                    f"revision conflict: expected {expected_revision}, found {current.revision}"
                )
            _ensure_repository_directory(self.deleted_dir)
            archived = self.deleted_dir / str(current.id)
            if archived.is_symlink():
                raise RepositoryCorruptionError("deleted review job path is a symlink")
            if archived.exists():
                raise RepositoryConflictError(f"deleted review job already exists: {current.id}")
            os.rename(self._job_dir(current.id), archived)
            self._fsync_directory(self.deleted_dir)
            self._fsync_directory(self.jobs_dir)
            return archived

    def _events_path(self, job_id: UUID | str) -> Path:
        return self._job_dir(job_id) / "events.jsonl"

    @staticmethod
    def _reject_symlink(path: Path, description: str) -> None:
        if path.is_symlink():
            raise RepositoryCorruptionError(f"{description} is a symlink")

    def _truncate_events(self, path: Path, size: int) -> None:
        self._reject_symlink(path, "review job events file")
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            if path.is_symlink():
                raise RepositoryCorruptionError("review job events file is a symlink") from exc
            raise
        with os.fdopen(descriptor, "r+b") as stream:
            stream.truncate(size)
            stream.flush()
            os.fsync(stream.fileno())
        self._fsync_directory(path.parent)

    def _append_event_row(self, path: Path, event: ReviewJobEvent) -> None:
        self._reject_symlink(path, "review job events file")
        row = json.dumps(_json_payload(event), ensure_ascii=True, separators=(",", ":"))
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as exc:
            if path.is_symlink():
                raise RepositoryCorruptionError("review job events file is a symlink") from exc
            raise
        with os.fdopen(descriptor, "ab") as stream:
            stream.write(row.encode("ascii") + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        self._fsync_directory(path.parent)

    def _update_last_event_id(self, job_id: UUID | str, event_id: int) -> None:
        state = self._load_state(job_id)
        if state.last_event_id == event_id:
            return
        updated = state.model_copy(update={"last_event_id": event_id, "updated_at": _utcnow()})
        write_json_atomic(self._state_path(job_id), _json_payload(updated))

    def _recover_event_tail(
        self,
        job_id: UUID | str,
        path: Path,
        events: list[ReviewJobEvent],
        *,
        valid_size: int,
        damaged_tail: bytes,
    ) -> list[ReviewJobEvent]:
        self._truncate_events(path, valid_size)
        warning = ReviewJobEvent(
            job_id=_job_id(job_id),
            event_id=len(events) + 1,
            event_type="event_tail_recovered",
            message="Recovered a damaged events.jsonl tail.",
            payload={
                "recovered_bytes": len(damaged_tail),
                "context": damaged_tail[:256].decode("utf-8", errors="replace"),
            },
        )
        self._append_event_row(path, warning)
        self._update_last_event_id(job_id, warning.event_id)
        return [*events, warning]

    def _read_events_locked(self, job_id: UUID | str) -> list[ReviewJobEvent]:
        path = self._events_path(job_id)
        self._reject_symlink(path, "review job events file")
        if not path.exists():
            return []
        content = path.read_bytes()
        lines = content.splitlines(keepends=True)
        events: list[ReviewJobEvent] = []
        valid_size = 0
        expected_id = 1
        for index, line in enumerate(lines):
            is_tail = index == len(lines) - 1
            if not line.endswith(b"\n"):
                return self._recover_event_tail(
                    job_id,
                    path,
                    events,
                    valid_size=valid_size,
                    damaged_tail=content[valid_size:],
                )
            try:
                event = ReviewJobEvent.model_validate_json(line[:-1])
            except Exception as exc:
                if is_tail:
                    return self._recover_event_tail(
                        job_id,
                        path,
                        events,
                        valid_size=valid_size,
                        damaged_tail=content[valid_size:],
                    )
                raise RepositoryCorruptionError("invalid event before JSONL tail") from exc
            if event.job_id != _job_id(job_id) or event.event_id != expected_id:
                raise RepositoryCorruptionError("event identity or sequence is invalid")
            events.append(event)
            expected_id += 1
            valid_size += len(line)
        return events

    def append_event(
        self,
        job_id: UUID | str,
        event_type: str,
        *,
        stage: ReviewStage | None = None,
        status: Any = None,
        attempt_id: str | None = None,
        message: str = "",
        payload: dict[str, Any] | None = None,
    ) -> ReviewJobEvent:
        if not event_type:
            raise ValueError("event_type is required")
        with exclusive_file_lock(self._lock_path(job_id)):
            state = self._load_state(job_id)
            events = self._read_events_locked(job_id)
            event = ReviewJobEvent(
                job_id=state.id,
                event_id=len(events) + 1,
                event_type=event_type,
                stage=stage,
                status=status,
                attempt_id=attempt_id,
                message=message,
                payload=payload or {},
            )
            path = self._events_path(job_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._append_event_row(path, event)
            if state.last_event_id != event.event_id:
                self._update_last_event_id(job_id, event.event_id)
            return event

    def replay_events(
        self,
        job_id: UUID | str,
        *,
        after_event_id: int = 0,
    ) -> list[ReviewJobEvent]:
        if after_event_id < 0:
            raise ValueError("after_event_id must be non-negative")
        with exclusive_file_lock(self._lock_path(job_id)):
            state = self._load_state(job_id)
            events = self._read_events_locked(job_id)
            self._reconcile_last_event_id_locked(job_id, state, events=events)
            return [event for event in events if event.event_id > after_event_id]

    def _reconcile_last_event_id_locked(
        self,
        job_id: UUID | str,
        state: ReviewJobState,
        *,
        events: list[ReviewJobEvent] | None = None,
    ) -> ReviewJobState:
        validated_events = self._read_events_locked(job_id) if events is None else events
        last_event_id = validated_events[-1].event_id if validated_events else 0
        if state.last_event_id == last_event_id:
            return state
        updated = state.model_copy(
            update={"last_event_id": last_event_id, "updated_at": _utcnow()}
        )
        write_json_atomic(self._state_path(job_id), _json_payload(updated))
        return updated

    def _claim_path(self, job_id: UUID | str) -> Path:
        return self._job_dir(job_id) / "worker_claim.json"

    def claim(
        self,
        job_id: UUID | str,
        *,
        owner: str,
        lease_seconds: float,
    ) -> WorkerClaim | None:
        if not owner.strip():
            raise ValueError("claim owner is required")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        with exclusive_file_lock(self._lock_path(job_id)):
            state = self._load_state(job_id)
            path = self._claim_path(job_id)
            self._reject_symlink(path, "worker claim")
            now = _utcnow()
            if path.is_file():
                existing = WorkerClaim.model_validate(read_json(path))
                if existing.expires_at > now:
                    return None
            claim = WorkerClaim(
                job_id=state.id,
                owner=owner,
                token=uuid4().hex,
                acquired_at=now,
                expires_at=now + timedelta(seconds=lease_seconds),
            )
            write_json_atomic(path, _json_payload(claim))
            return claim

    def release_claim(self, job_id: UUID | str, *, owner: str, token: str) -> bool:
        with exclusive_file_lock(self._lock_path(job_id)):
            self._load_state(job_id)
            path = self._claim_path(job_id)
            self._reject_symlink(path, "worker claim")
            if not path.is_file():
                return False
            existing = WorkerClaim.model_validate(read_json(path))
            if existing.owner != owner or existing.token != token:
                return False
            path.unlink()
            self._fsync_directory(path.parent)
            return True

    def _expected_output_dir(self, job_id: UUID | str, manifest: StageManifest) -> Path:
        expected_relative = PurePosixPath(
            "attempts",
            manifest.attempt_id,
            "stages",
            manifest.stage.value,
            "outputs",
        )
        if PurePosixPath(manifest.output_dir) != expected_relative:
            raise ManifestValidationError("manifest output directory does not match its attempt")
        expected_checkpoint = expected_relative.parent / "checkpoint.json"
        if PurePosixPath(manifest.checkpoint_path) != expected_checkpoint:
            raise ManifestValidationError("manifest checkpoint path does not match its attempt")
        return self._job_dir(job_id).joinpath(*expected_relative.parts)

    def _ensure_owned_path(self, root: Path, path: Path) -> Path:
        try:
            relative = path.relative_to(root)
        except ValueError as exc:
            raise ManifestValidationError("repository path escapes its job") from exc
        current = root
        for part in (".", *relative.parts):
            if part != ".":
                current = current / part
            try:
                metadata = current.lstat()
            except FileNotFoundError as exc:
                raise ManifestValidationError(f"repository path does not exist: {current.name}") from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise ManifestValidationError(f"repository path uses a symlink escape: {current.name}")
        try:
            path.resolve(strict=True).relative_to(root.resolve(strict=True))
        except (FileNotFoundError, ValueError) as exc:
            raise ManifestValidationError("repository path escapes its job") from exc
        return path

    def _mkdir_owned(self, root: Path, relative: PurePosixPath) -> Path:
        current = root
        if current.is_symlink():
            raise ManifestValidationError("job directory uses a symlink escape")
        for part in relative.parts:
            current = current / part
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                current.mkdir()
                self._fsync_directory(current.parent)
                continue
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise ManifestValidationError(f"repository directory is unsafe: {current.name}")
        return current

    def _validate_owned_regular_file(self, root: Path, relative_path: str) -> Path:
        relative = PurePosixPath(relative_path)
        candidate = root.joinpath(*relative.parts)
        current = root
        try:
            root_resolved = root.resolve(strict=True)
        except FileNotFoundError as exc:
            raise ManifestValidationError("attempt output directory does not exist") from exc
        for part in relative.parts:
            current = current / part
            try:
                metadata = current.lstat()
            except FileNotFoundError as exc:
                raise ManifestValidationError(f"declared output does not exist: {relative_path}") from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise ManifestValidationError(f"declared output uses a symlink escape: {relative_path}")
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root_resolved)
        except (FileNotFoundError, ValueError) as exc:
            raise ManifestValidationError(f"declared output escapes its attempt: {relative_path}") from exc
        if not resolved.is_file():
            raise ManifestValidationError(f"declared output is not a regular file: {relative_path}")
        return resolved

    def _validate_manifest_outputs(self, job_id: UUID | str, manifest: StageManifest) -> Path:
        output_dir = self._expected_output_dir(job_id, manifest)
        self._ensure_owned_path(self._job_dir(job_id), output_dir)
        for name, relative_path in manifest.artifacts.items():
            artifact = self._validate_owned_regular_file(output_dir, relative_path)
            size = artifact.stat().st_size
            if size != manifest.artifact_sizes[name]:
                raise ManifestValidationError(f"artifact size mismatch: {name}")
            digest = hashlib.sha256()
            with artifact.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != manifest.artifact_sha256[name]:
                raise ManifestValidationError(f"artifact SHA mismatch: {name}")
        return output_dir

    def _validate_stage_checkpoint(
        self,
        job_id: UUID | str,
        manifest: StageManifest,
    ) -> StageCheckpoint:
        self._expected_output_dir(job_id, manifest)
        root = self._job_dir(job_id)
        relative = PurePosixPath(manifest.checkpoint_path)
        path = root.joinpath(*relative.parts)
        self._ensure_owned_path(root, path)
        try:
            checkpoint = StageCheckpoint.model_validate(read_json(path))
        except (OSError, ValueError) as exc:
            raise ManifestValidationError("checkpoint JSON is invalid") from exc
        if not checkpoint.committed:
            raise ManifestValidationError("checkpoint must be committed")
        if checkpoint.stage != manifest.stage:
            raise ManifestValidationError("checkpoint stage does not match manifest")
        if checkpoint.attempt_id != manifest.attempt_id:
            raise ManifestValidationError("checkpoint attempt does not match manifest")
        if checkpoint.manifest != manifest:
            raise ManifestValidationError("checkpoint manifest does not match submitted manifest")
        return checkpoint

    def _write_immutable_manifest(self, path: Path, manifest: StageManifest) -> None:
        payload = json.dumps(
            _json_payload(manifest),
            ensure_ascii=True,
            indent=2,
        ).encode("ascii")
        if path.is_symlink():
            raise ManifestValidationError("immutable manifest path is a symlink escape")
        if path.exists():
            existing = StageManifest.model_validate(read_json(path))
            if existing != manifest:
                raise RepositoryConflictError(f"immutable manifest already exists: {path.name}")
            return
        temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
        write_bytes_atomic(temporary, payload, mode=0o400)
        try:
            os.link(temporary, path)
            self._fsync_directory(path.parent)
        except FileExistsError:
            existing = StageManifest.model_validate(read_json(path))
            if existing != manifest:
                raise RepositoryConflictError(
                    f"immutable manifest already exists: {path.name}"
                ) from None
        finally:
            temporary.unlink(missing_ok=True)
            self._fsync_directory(path.parent)

    def _replace_current_stage_pointer(self, path: Path, manifest: StageManifest) -> None:
        write_json_atomic(path, _json_payload(manifest))

    def _prepare_compatibility_view(
        self,
        job_id: UUID | str,
        manifest: StageManifest,
        output_dir: Path,
    ) -> Path:
        root = self._job_dir(job_id)
        view_dir = self._mkdir_owned(
            root,
            PurePosixPath("compatibility_views", manifest.stage.value, manifest.attempt_id),
        )
        expected_files = {PurePosixPath(path) for path in manifest.artifacts.values()}
        expected_directories = {PurePosixPath(".")}
        for artifact_path in expected_files:
            expected_directories.update(artifact_path.parents)
        for existing in view_dir.rglob("*"):
            relative = PurePosixPath(existing.relative_to(view_dir).as_posix())
            if existing.is_symlink():
                raise ManifestValidationError("compatibility view contains an undeclared symlink")
            if existing.is_dir():
                if relative not in expected_directories:
                    raise ManifestValidationError(
                        "compatibility view contains an undeclared directory"
                    )
            elif relative not in expected_files:
                raise ManifestValidationError("compatibility view contains an undeclared artifact")
        for relative_path in manifest.artifacts.values():
            artifact = self._validate_owned_regular_file(output_dir, relative_path)
            relative = PurePosixPath(relative_path)
            target_parent = self._mkdir_owned(
                root,
                PurePosixPath(
                    "compatibility_views",
                    manifest.stage.value,
                    manifest.attempt_id,
                    *relative.parts[:-1],
                ),
            )
            target_artifact = target_parent / relative.name
            if target_artifact.is_symlink():
                raise ManifestValidationError("compatibility artifact is a symlink escape")
            if target_artifact.exists():
                if not target_artifact.is_file() or not os.path.samefile(artifact, target_artifact):
                    raise ManifestValidationError(
                        "compatibility artifact must be a hard link to committed output"
                    )
            else:
                os.link(artifact, target_artifact)
                self._fsync_directory(target_parent)
            target_artifact.chmod(stat.S_IMODE(target_artifact.stat().st_mode) & ~0o222)
        for directory, child_dirs, _files in os.walk(view_dir, topdown=False):
            for child_dir in child_dirs:
                child = Path(directory) / child_dir
                child.chmod(stat.S_IMODE(child.stat().st_mode) & ~0o222)
            current = Path(directory)
            current.chmod(stat.S_IMODE(current.stat().st_mode) & ~0o222)
        return view_dir

    def _publish_compatibility_view(
        self,
        job_id: UUID | str,
        manifest: StageManifest,
        view_dir: Path,
    ) -> None:
        root = self._job_dir(job_id)
        stages_dir = self._mkdir_owned(root, PurePosixPath("run", "stages"))
        target = stages_dir / manifest.stage.value
        if target.is_symlink() and target.resolve() == view_dir.resolve():
            return
        if target.exists() and not target.is_symlink():
            raise ManifestValidationError("compatibility stage target is not a symlink")
        temporary = stages_dir / f".{manifest.stage.value}.{uuid4().hex}.tmp"
        relative_target = os.path.relpath(view_dir, stages_dir)
        os.symlink(relative_target, temporary, target_is_directory=True)
        try:
            os.replace(temporary, target)
            self._fsync_directory(stages_dir)
        finally:
            temporary.unlink(missing_ok=True)

    def _current_stage_manifests_locked(
        self,
        job_id: UUID | str,
    ) -> dict[ReviewStage, StageManifest]:
        root = self._job_dir(job_id)
        pointer_dir = root / "current_stages"
        if not pointer_dir.exists():
            return {}
        self._ensure_owned_path(root, pointer_dir)
        manifests: dict[ReviewStage, StageManifest] = {}
        for path in sorted(pointer_dir.glob("*.json")):
            self._ensure_owned_path(root, path)
            manifest = StageManifest.model_validate(read_json(path))
            if path.stem != manifest.stage.value:
                raise RepositoryCorruptionError("current stage pointer name does not match stage")
            manifests[manifest.stage] = manifest
        return manifests

    def _reconcile_stage_views_locked(
        self,
        job_id: UUID | str,
        state: ReviewJobState,
    ) -> ReviewJobState:
        manifests = self._current_stage_manifests_locked(job_id)
        if state.current_stage_manifests != manifests:
            state = state.model_copy(
                update={
                    "current_stage_manifests": manifests,
                    "revision": state.revision + 1,
                    "updated_at": _utcnow(),
                }
            )
            write_json_atomic(self._state_path(job_id), _json_payload(state))
        for manifest in manifests.values():
            output_dir = self._validate_manifest_outputs(job_id, manifest)
            view_dir = self._prepare_compatibility_view(job_id, manifest, output_dir)
            self._publish_compatibility_view(job_id, manifest, view_dir)
        return state

    def commit_stage_outputs(
        self,
        job_id: UUID | str,
        manifest: StageManifest,
    ) -> StageManifest:
        validated = StageManifest.model_validate(manifest)
        with exclusive_file_lock(self._lock_path(job_id)):
            state = self._load_state(job_id)
            self._validate_stage_checkpoint(job_id, validated)
            output_dir = self._validate_manifest_outputs(job_id, validated)
            root = self._job_dir(job_id)
            immutable = (
                root
                / "manifests"
                / validated.stage.value
                / f"{validated.attempt_id}.json"
            )
            self._mkdir_owned(
                root,
                PurePosixPath("manifests", validated.stage.value),
            )
            self._write_immutable_manifest(immutable, validated)
            self._prepare_compatibility_view(job_id, validated, output_dir)
            pointer_dir = self._mkdir_owned(root, PurePosixPath("current_stages"))
            pointer = pointer_dir / f"{validated.stage.value}.json"
            if pointer.is_symlink():
                raise ManifestValidationError("current stage pointer is a symlink escape")
            self._replace_current_stage_pointer(pointer, validated)
            self._reconcile_stage_views_locked(job_id, state)
            return validated

    def current_stage_manifest(
        self,
        job_id: UUID | str,
        stage: ReviewStage,
    ) -> StageManifest | None:
        path = self._job_dir(job_id) / "current_stages" / f"{ReviewStage(stage).value}.json"
        if not path.is_file():
            return None
        self._ensure_owned_path(self._job_dir(job_id), path)
        return StageManifest.model_validate(read_json(path))

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

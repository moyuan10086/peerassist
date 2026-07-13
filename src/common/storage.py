from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from .config import get_settings


def jobs_root() -> Path:
    root = get_settings().data_dir / "jobs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_job_id(job_id: UUID | str) -> str:
    if isinstance(job_id, UUID):
        return str(job_id)
    token = str(job_id or "").strip()
    if not token:
        raise ValueError("job_id is required")
    try:
        return str(UUID(token))
    except Exception as exc:
        raise ValueError(f"invalid job_id: {job_id}") from exc


def job_dir(job_id: UUID | str) -> Path:
    path = jobs_root() / _safe_job_id(job_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_path(job_id: UUID | str) -> Path:
    return job_dir(job_id) / "job.json"


def events_path(job_id: UUID | str) -> Path:
    return job_dir(job_id) / "events.jsonl"


def annotations_path(job_id: UUID | str) -> Path:
    return job_dir(job_id) / "annotations.json"


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _unique_temp_path(path: Path) -> Path:
    return path.parent / f".{path.name}.{uuid4().hex}.tmp"


def write_bytes_atomic(path: Path, content: bytes, *, mode: int | None = None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _unique_temp_path(path)
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode or 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    write_bytes_atomic(path, content)


def write_text_atomic(path: Path, content: str) -> None:
    write_bytes_atomic(path, content.encode("utf-8"))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@contextmanager
def exclusive_file_lock(path: Path) -> Iterator[None]:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def append_event(job_id: UUID | str, event: str, **extra: Any) -> None:
    now = datetime.now(UTC).isoformat()
    row = {
        "ts": now,
        "event": event,
        **extra,
    }
    events_file = events_path(job_id)
    events_file.parent.mkdir(parents=True, exist_ok=True)
    with (
        exclusive_file_lock(events_file.parent / ".legacy-events.lock"),
        events_file.open("a", encoding="utf-8") as stream,
    ):
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    _fsync_directory(events_file.parent)

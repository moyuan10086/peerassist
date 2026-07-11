"""Secure immutable persistence for citation-verification response artifacts."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import secrets
import stat
from pathlib import Path
from typing import Any

from schemas.citation import RawResponseArtifact

_ATTEMPT_ID_RE = re.compile(r"[A-Za-z0-9_-]+$")
_ARTIFACT_DIRECTORY = "citation_verifications"


class ArtifactSerializationError(Exception):
    """The supplied protocol response cannot be represented as immutable bytes."""


class ArtifactWriteError(Exception):
    """The immutable response could not be safely published."""


class ArtifactPathError(ArtifactWriteError):
    """The artifact path no longer names the directories held during publication."""


def response_bytes(raw_response: Any) -> bytes:
    """Preserve supplied bytes or encode JSON values in deterministic canonical form."""
    if isinstance(raw_response, bytes):
        return raw_response
    try:
        return json.dumps(
            raw_response,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ArtifactSerializationError("raw response is not JSON-serializable") from exc


def expected_response_artifact_path(attempt_id: str) -> str:
    """Return the only valid indexed path for an immutable attempt response."""
    return f"{_ARTIFACT_DIRECTORY}/{_artifact_filename(attempt_id)}"


def write_response_artifact(artifact_root: str | Path, attempt_id: str, raw_response: Any) -> RawResponseArtifact:
    """Publish immutable bytes using only root- and directory-fd-relative operations."""
    encoded = response_bytes(raw_response)
    filename = _artifact_filename(attempt_id)
    root_fd = _open_root_fd(artifact_root, create=True)
    artifact_fd = -1
    temporary_name = ""
    published = False
    try:
        artifact_fd = _open_artifact_directory_fd(root_fd, create=True)
        temporary_name, temporary_fd = _create_temporary_file(artifact_fd)
        try:
            _write_and_sync(temporary_fd, encoded)
        finally:
            os.close(temporary_fd)
        os.link(
            temporary_name,
            filename,
            src_dir_fd=artifact_fd,
            dst_dir_fd=artifact_fd,
            follow_symlinks=False,
        )
        published = True
        os.fsync(artifact_fd)
        if not _binding_matches(artifact_root, root_fd, artifact_fd):
            _cleanup_published_artifact(artifact_fd, filename)
            published = False
            raise ArtifactPathError("artifact directory binding changed during publication")
        published = False
    except FileExistsError:
        raise
    except ArtifactPathError:
        raise
    except OSError as exc:
        raise ArtifactWriteError("unable to publish immutable response artifact") from exc
    finally:
        if published and artifact_fd >= 0:
            # This is only reached when an exception occurred after publication.
            _cleanup_published_artifact(artifact_fd, filename)
        if temporary_name and artifact_fd >= 0:
            _best_effort_unlink(artifact_fd, temporary_name)
        if artifact_fd >= 0:
            _best_effort_close(artifact_fd)
        _best_effort_close(root_fd)
    return RawResponseArtifact(path=expected_response_artifact_path(attempt_id), sha256=hashlib.sha256(encoded).hexdigest())


def validate_response_artifact(artifact_root: str | Path, artifact: RawResponseArtifact) -> bool:
    """Hash a regular response file through one no-follow descriptor open."""
    try:
        filename = _artifact_filename_from_path(artifact.path)
        root_fd = _open_root_fd(artifact_root, create=False)
    except (ArtifactWriteError, ValueError):
        return False
    artifact_fd = -1
    try:
        artifact_fd = _open_artifact_directory_fd(root_fd, create=False)
        if not _binding_matches(artifact_root, root_fd, artifact_fd):
            return False
        digest = _descriptor_sha256(artifact_fd, filename)
        if not _binding_matches(artifact_root, root_fd, artifact_fd):
            return False
    except (ArtifactWriteError, OSError):
        return False
    finally:
        if artifact_fd >= 0:
            os.close(artifact_fd)
        os.close(root_fd)
    return digest == artifact.sha256


def _artifact_filename(attempt_id: str) -> str:
    if not _ATTEMPT_ID_RE.fullmatch(attempt_id):
        raise ValueError("attempt_id must contain only letters, digits, underscores, and hyphens")
    return f"attempt-{attempt_id}.json"


def _artifact_filename_from_path(path: str) -> str:
    expected_prefix = f"{_ARTIFACT_DIRECTORY}/"
    if not path.startswith(expected_prefix):
        raise ValueError("artifact path is not in the citation verification directory")
    filename = path.removeprefix(expected_prefix)
    if not filename or "/" in filename or "\\" in filename or filename in {".", ".."}:
        raise ValueError("artifact filename is invalid")
    return filename


def _directory_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


def _open_root_fd(artifact_root: str | Path, *, create: bool) -> int:
    root = Path(artifact_root)
    try:
        if create:
            root.mkdir(parents=True, exist_ok=True)
        return os.open(root, _directory_flags())
    except OSError as exc:
        raise ArtifactWriteError("artifact root is unavailable") from exc


def _open_artifact_directory_fd(root_fd: int, *, create: bool) -> int:
    try:
        return os.open(_ARTIFACT_DIRECTORY, _directory_flags(), dir_fd=root_fd)
    except FileNotFoundError:
        if not create:
            raise ArtifactWriteError("artifact directory is unavailable") from None
        try:
            os.mkdir(_ARTIFACT_DIRECTORY, mode=0o700, dir_fd=root_fd)
        except FileExistsError:
            pass
        try:
            return os.open(_ARTIFACT_DIRECTORY, _directory_flags(), dir_fd=root_fd)
        except OSError as exc:
            raise ArtifactWriteError("artifact directory is unavailable") from exc
    except OSError as exc:
        raise ArtifactWriteError("artifact directory is unavailable") from exc


def _create_temporary_file(directory_fd: int) -> tuple[str, int]:
    for _ in range(64):
        filename = f".attempt-{secrets.token_hex(16)}.tmp"
        try:
            descriptor = os.open(
                filename,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
                dir_fd=directory_fd,
            )
            return filename, descriptor
        except FileExistsError:
            continue
    raise ArtifactWriteError("unable to allocate unique artifact temporary file")


def _write_and_sync(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    written = 0
    while written < len(view):
        written += os.write(descriptor, view[written:])
    os.fsync(descriptor)


def _descriptor_sha256(directory_fd: int, filename: str) -> str:
    descriptor = os.open(filename, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError(errno.EINVAL, "artifact is not a regular file")
        digest = hashlib.sha256()
        while block := os.read(descriptor, 1024 * 1024):
            digest.update(block)
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _binding_matches(artifact_root: str | Path, root_fd: int, artifact_fd: int) -> bool:
    fresh_root_fd = -1
    fresh_artifact_fd = -1
    try:
        fresh_root_fd = _open_root_fd(artifact_root, create=False)
        if _directory_identity(fresh_root_fd) != _directory_identity(root_fd):
            return False
        fresh_artifact_fd = _open_artifact_directory_fd(fresh_root_fd, create=False)
        return _directory_identity(fresh_artifact_fd) == _directory_identity(artifact_fd)
    except ArtifactWriteError:
        return False
    finally:
        if fresh_artifact_fd >= 0:
            os.close(fresh_artifact_fd)
        if fresh_root_fd >= 0:
            os.close(fresh_root_fd)


def _directory_identity(descriptor: int) -> tuple[int, int]:
    file_stat = os.fstat(descriptor)
    return file_stat.st_dev, file_stat.st_ino


def _cleanup_published_artifact(directory_fd: int, filename: str) -> None:
    """Best-effort removal and durability sync that cannot replace a primary failure."""
    if _best_effort_unlink(directory_fd, filename):
        _best_effort_fsync(directory_fd)


def _best_effort_unlink(directory_fd: int, filename: str) -> bool:
    try:
        os.unlink(filename, dir_fd=directory_fd)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def _best_effort_fsync(descriptor: int) -> None:
    try:
        os.fsync(descriptor)
    except OSError:
        pass


def _best_effort_close(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass

"""Secure immutable persistence for citation-verification response artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any

from schemas.citation import RawResponseArtifact

_ATTEMPT_ID_RE = re.compile(r"[A-Za-z0-9_-]+$")
_ARTIFACT_PREFIX = Path("citation_verifications")


class ArtifactSerializationError(Exception):
    """The supplied protocol response cannot be represented as immutable bytes."""


class ArtifactWriteError(Exception):
    """The immutable response could not be safely published."""


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


def _resolved_root(artifact_root: str | Path) -> Path:
    root = Path(artifact_root)
    try:
        root.mkdir(parents=True, exist_ok=True)
        return root.resolve(strict=True)
    except OSError as exc:
        raise ArtifactWriteError("artifact root is unavailable") from exc


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _relative_artifact_path(path: str | Path) -> Path:
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("artifact path must be relative and confined to artifact_root")
    return relative


def _confined_destination(root: Path, relative: Path) -> Path:
    candidate = root / relative
    try:
        parent = candidate.parent.resolve(strict=False)
    except OSError as exc:
        raise ArtifactWriteError("artifact directory cannot be resolved") from exc
    if not _is_within(parent, root):
        raise ValueError("artifact path escapes artifact_root")
    return candidate


def _artifact_relative_path(attempt_id: str) -> Path:
    if not _ATTEMPT_ID_RE.fullmatch(attempt_id):
        raise ValueError("attempt_id must contain only letters, digits, underscores, and hyphens")
    return _ARTIFACT_PREFIX / f"attempt-{attempt_id}.json"


def expected_response_artifact_path(attempt_id: str) -> str:
    """Return the only valid indexed path for an immutable attempt response."""
    return _artifact_relative_path(attempt_id).as_posix()


def write_response_artifact(artifact_root: str | Path, attempt_id: str, raw_response: Any) -> RawResponseArtifact:
    """Publish an immutable raw response without replacing an existing attempt."""
    encoded = response_bytes(raw_response)
    root = _resolved_root(artifact_root)
    relative = _artifact_relative_path(attempt_id)
    destination = _confined_destination(root, relative)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not _is_within(destination.parent.resolve(strict=True), root):
            raise ValueError("artifact path escapes artifact_root")
        file_descriptor, temp_name = tempfile.mkstemp(prefix=".attempt-", suffix=".tmp", dir=destination.parent)
        temporary = Path(temp_name)
        try:
            with os.fdopen(file_descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, destination)
            _fsync_directory(destination.parent)
        finally:
            temporary.unlink(missing_ok=True)
    except FileExistsError:
        raise
    except ValueError:
        raise
    except OSError as exc:
        raise ArtifactWriteError("unable to publish immutable response artifact") from exc
    return RawResponseArtifact(path=relative.as_posix(), sha256=hashlib.sha256(encoded).hexdigest())


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def validate_response_artifact(artifact_root: str | Path, artifact: RawResponseArtifact) -> bool:
    """Return whether an indexed artifact is confined, regular, and hash-valid."""
    try:
        root = Path(artifact_root).resolve(strict=True)
        relative = _relative_artifact_path(artifact.path)
        candidate = _confined_destination(root, relative)
        digest = _descriptor_sha256(candidate)
    except (OSError, ValueError):
        return False
    return digest == artifact.sha256


def _descriptor_sha256(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("artifact is not a regular file")
        digest = hashlib.sha256()
        while block := os.read(descriptor, 1024 * 1024):
            digest.update(block)
        return digest.hexdigest()
    finally:
        os.close(descriptor)

"""Bounded streaming manuscript uploads for PeerAssist."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from python_multipart import MultipartParser
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import parse_options_header

from common.config import get_settings
from common.storage import exclusive_file_lock
from peerassist.job_repository import PaperRepository, RepositoryCorruptionError
from schemas.peerassist_jobs import PaperRecord


class UploadError(Exception):
    """Base upload error."""


class UploadValidationError(UploadError):
    """Upload metadata or content is not an accepted PDF."""


class UploadTooLargeError(UploadError):
    """Upload exceeds the configured manuscript limit."""


class UploadInterruptedError(UploadError):
    """The source stream stopped before upload completion."""


class UploadParseError(UploadError):
    """The multipart request is malformed or incomplete."""


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _safe_filename(filename: str) -> str:
    value = str(filename or "").strip()
    if not value or "\x00" in value or "/" in value or "\\" in value:
        raise UploadValidationError("filename must be a plain file name")
    if PurePosixPath(value).name != value or value in {".", ".."}:
        raise UploadValidationError("filename must be a plain file name")
    return value


def _pdf_content_type(content_type: str) -> str:
    media_type = str(content_type or "").split(";", 1)[0].strip().lower()
    if media_type != "application/pdf":
        raise UploadValidationError("content type must be application/pdf")
    return media_type


def _configured_limit(max_pdf_bytes: int | None) -> int:
    value = get_settings().max_pdf_bytes if max_pdf_bytes is None else max_pdf_bytes
    if value <= 0:
        raise ValueError("max_pdf_bytes must be positive")
    return int(value)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _PdfUploadSink:
    def __init__(
        self,
        *,
        repository: PaperRepository,
        filename: str,
        content_type: str,
        max_pdf_bytes: int,
        content_length: int | None,
    ) -> None:
        self.repository = repository
        self.filename = _safe_filename(filename)
        self.content_type = _pdf_content_type(content_type)
        self.max_pdf_bytes = max_pdf_bytes
        if content_length is not None:
            if content_length < 0:
                raise UploadValidationError("content_length must be non-negative")
            if content_length > max_pdf_bytes:
                raise UploadTooLargeError("declared PDF size exceeds the configured limit")

        uploads_dir = repository.papers_dir / ".uploads"
        if uploads_dir.is_symlink():
            raise RepositoryCorruptionError("upload temporary directory is a symlink")
        uploads_dir.mkdir(parents=True, exist_ok=True)
        _fsync_directory(repository.papers_dir)
        self.temp_path = uploads_dir / f"{uuid4().hex}.tmp"
        descriptor = os.open(
            self.temp_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        self.stream = os.fdopen(descriptor, "wb")
        self.digest = hashlib.sha256()
        self.prefix = bytearray()
        self.size = 0
        self.closed = False

    def write(self, chunk: bytes) -> None:
        if self.closed:
            raise UploadInterruptedError("upload sink is closed")
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise UploadValidationError("upload chunks must be bytes")
        data = bytes(chunk)
        if not data:
            return
        next_size = self.size + len(data)
        if next_size > self.max_pdf_bytes:
            raise UploadTooLargeError("actual PDF size exceeds the configured limit")
        if len(self.prefix) < 5:
            needed = 5 - len(self.prefix)
            self.prefix.extend(data[:needed])
        self.stream.write(data)
        self.digest.update(data)
        self.size = next_size

    def abort(self) -> None:
        if not self.closed:
            self.stream.close()
            self.closed = True
        self.temp_path.unlink(missing_ok=True)

    def finish(self) -> PaperRecord:
        if self.size == 0:
            raise UploadValidationError("PDF upload is empty")
        if bytes(self.prefix) != b"%PDF-":
            raise UploadValidationError("uploaded content does not have a PDF header")
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self.stream.close()
        self.closed = True
        paper_id = self.digest.hexdigest()
        record = PaperRecord(
            paper_id=paper_id,
            source_pdf_name=self.filename,
            source_pdf_path="source/source.pdf",
            size_bytes=self.size,
            content_type="application/pdf",
        )
        source_created = False
        lock_path = self.repository.locks_dir / f"{paper_id}.lock"
        try:
            with exclusive_file_lock(lock_path):
                identity_dir = self.repository.papers_dir / paper_id
                source_dir = identity_dir / "source"
                source_path = source_dir / "source.pdf"
                if identity_dir.is_symlink() or source_dir.is_symlink() or source_path.is_symlink():
                    raise RepositoryCorruptionError("paper source path is a symlink")
                identity_created = not identity_dir.exists()
                source_dir.mkdir(parents=True, exist_ok=True)
                if identity_created:
                    _fsync_directory(self.repository.papers_dir)
                _fsync_directory(identity_dir)
                if source_path.exists():
                    if not source_path.is_file():
                        raise RepositoryCorruptionError("paper source is not a regular file")
                    if source_path.stat().st_size != self.size:
                        raise RepositoryCorruptionError("existing paper source size does not match")
                    existing_digest = _sha256_file(source_path)
                    if existing_digest != paper_id:
                        raise RepositoryCorruptionError("existing paper source hash does not match")
                    self.temp_path.unlink(missing_ok=True)
                else:
                    os.replace(self.temp_path, source_path)
                    os.chmod(source_path, 0o600)
                    _fsync_directory(source_dir)
                    source_created = True
            return self.repository.create_or_get(record)
        except Exception:
            self.temp_path.unlink(missing_ok=True)
            if source_created:
                with exclusive_file_lock(lock_path):
                    paper_path = self.repository.papers_dir / paper_id / "paper.json"
                    source_path = self.repository.papers_dir / paper_id / "source" / "source.pdf"
                    if not paper_path.exists():
                        source_path.unlink(missing_ok=True)
            raise


def persist_pdf_upload(
    chunks: Iterable[bytes],
    *,
    filename: str,
    content_type: str,
    repository: PaperRepository,
    content_length: int | None = None,
    max_pdf_bytes: int | None = None,
) -> PaperRecord:
    """Persist one PDF stream without buffering it in memory."""

    sink = _PdfUploadSink(
        repository=repository,
        filename=filename,
        content_type=content_type,
        max_pdf_bytes=_configured_limit(max_pdf_bytes),
        content_length=content_length,
    )
    try:
        for chunk in chunks:
            sink.write(chunk)
        return sink.finish()
    except UploadError:
        sink.abort()
        raise
    except Exception as exc:
        sink.abort()
        raise UploadInterruptedError(str(exc)) from exc


def persist_multipart_upload(
    chunks: Iterable[bytes],
    *,
    content_type: str,
    repository: PaperRepository,
    content_length: int | None = None,
    max_pdf_bytes: int | None = None,
) -> PaperRecord:
    """Parse a multipart stream and persist its single ``file`` PDF part."""

    media_type, parameters = parse_options_header(content_type)
    if media_type.lower() != b"multipart/form-data" or not parameters.get(b"boundary"):
        raise UploadParseError("multipart/form-data boundary is required")
    if content_length is not None and content_length < 0:
        raise UploadParseError("content_length must be non-negative")

    limit = _configured_limit(max_pdf_bytes)
    current_header_name = bytearray()
    current_header_value = bytearray()
    headers: dict[bytes, bytes] = {}
    current_is_file = False
    file_seen = False
    file_ended = False
    ended = False
    sink: _PdfUploadSink | None = None

    def on_part_begin() -> None:
        nonlocal headers, current_is_file
        headers = {}
        current_is_file = False

    def on_header_begin() -> None:
        current_header_name.clear()
        current_header_value.clear()

    def on_header_field(data: bytes, start: int, end: int) -> None:
        current_header_name.extend(data[start:end])

    def on_header_value(data: bytes, start: int, end: int) -> None:
        current_header_value.extend(data[start:end])

    def on_header_end() -> None:
        headers[bytes(current_header_name).strip().lower()] = bytes(current_header_value).strip()

    def on_headers_finished() -> None:
        nonlocal current_is_file, file_seen, sink
        disposition, options = parse_options_header(headers.get(b"content-disposition"))
        if disposition.lower() != b"form-data" or options.get(b"name") != b"file":
            return
        if file_seen:
            raise UploadParseError("multipart request contains more than one file part")
        filename_bytes = options.get(b"filename")
        if not filename_bytes:
            raise UploadValidationError("filename is required")
        part_content_type = headers.get(b"content-type", b"").decode("latin-1")
        filename = filename_bytes.decode("utf-8", errors="strict")
        sink = _PdfUploadSink(
            repository=repository,
            filename=filename,
            content_type=part_content_type,
            max_pdf_bytes=limit,
            content_length=None,
        )
        file_seen = True
        current_is_file = True

    def on_part_data(data: bytes, start: int, end: int) -> None:
        if current_is_file and sink is not None:
            sink.write(data[start:end])

    def on_part_end() -> None:
        nonlocal file_ended
        if current_is_file:
            file_ended = True

    def on_end() -> None:
        nonlocal ended
        ended = True

    callbacks: dict[str, Any] = {
        "on_part_begin": on_part_begin,
        "on_header_begin": on_header_begin,
        "on_header_field": on_header_field,
        "on_header_value": on_header_value,
        "on_header_end": on_header_end,
        "on_headers_finished": on_headers_finished,
        "on_part_data": on_part_data,
        "on_part_end": on_part_end,
        "on_end": on_end,
    }
    parser = MultipartParser(parameters[b"boundary"], callbacks)
    try:
        for chunk in chunks:
            parser.write(bytes(chunk))
        parser.finalize()
        if not ended or not file_seen or not file_ended or sink is None:
            raise UploadParseError("multipart request is incomplete")
        return sink.finish()
    except (UploadError, UnicodeDecodeError):
        if sink is not None:
            sink.abort()
        raise
    except MultipartParseError as exc:
        if sink is not None:
            sink.abort()
        raise UploadParseError(str(exc)) from exc
    except Exception as exc:
        if sink is not None:
            sink.abort()
        raise UploadInterruptedError(str(exc)) from exc

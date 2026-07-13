"""Bounded streaming manuscript uploads for PeerAssist."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from python_multipart import MultipartParser
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import parse_options_header

from common.config import get_settings
from common.storage import exclusive_file_lock
from peerassist.job_repository import PaperRepository, RepositoryCorruptionError
from schemas.peerassist_jobs import PaperRecord

MAX_MULTIPART_OVERHEAD_BYTES = 64 * 1024


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


def _directory_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


def _open_child_directory(parent_fd: int, name: str) -> int:
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except FileExistsError:
        pass
    try:
        return os.open(name, _directory_flags(), dir_fd=parent_fd)
    except OSError as exc:
        raise RepositoryCorruptionError(f"repository directory is unsafe: {name}") from exc


def _read_record_at(identity_fd: int) -> PaperRecord | None:
    try:
        descriptor = os.open(
            "paper.json",
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=identity_fd,
        )
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        return PaperRecord.model_validate_json(stream.read())


def _write_record_at(identity_fd: int, record: PaperRecord) -> None:
    temporary = f".paper.json.{uuid4().hex}.tmp"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=identity_fd,
    )
    try:
        payload = json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2).encode(
            "utf-8"
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, "paper.json", src_dir_fd=identity_fd, dst_dir_fd=identity_fd)
        os.fsync(identity_fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=identity_fd)
        except FileNotFoundError:
            pass


def _validate_record_source(existing: PaperRecord, expected: PaperRecord) -> None:
    immutable_fields = ("paper_id", "source_pdf_path", "size_bytes", "content_type")
    if any(getattr(existing, field) != getattr(expected, field) for field in immutable_fields):
        raise RepositoryCorruptionError("existing paper record does not match verified source")


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

        papers_fd = os.open(repository.papers_dir, _directory_flags())
        try:
            self.uploads_fd = _open_child_directory(papers_fd, ".uploads")
        finally:
            os.close(papers_fd)
        self.temp_name = f"{uuid4().hex}.tmp"
        self.temp_path = repository.papers_dir / ".uploads" / self.temp_name
        descriptor = os.open(
            self.temp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=self.uploads_fd,
        )
        self.stream = os.fdopen(descriptor, "wb")
        self.digest = hashlib.sha256()
        self.prefix = bytearray()
        self.size = 0
        self.closed = False
        self.uploads_closed = False

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
        if not self.uploads_closed:
            try:
                os.unlink(self.temp_name, dir_fd=self.uploads_fd)
            except FileNotFoundError:
                pass
            self._close_uploads_directory()

    def _close_uploads_directory(self) -> None:
        if not self.uploads_closed:
            os.close(self.uploads_fd)
            self.uploads_closed = True

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
        lock_path = self.repository.locks_dir / f"{paper_id}.lock"
        try:
            with exclusive_file_lock(lock_path):
                papers_fd = os.open(self.repository.papers_dir, _directory_flags())
                identity_fd = -1
                source_fd = -1
                source_created = False
                try:
                    identity_fd = _open_child_directory(papers_fd, paper_id)
                    source_fd = _open_child_directory(identity_fd, "source")
                    try:
                        source_descriptor = os.open(
                            "source.pdf",
                            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                            dir_fd=source_fd,
                        )
                    except FileNotFoundError:
                        os.rename(
                            self.temp_name,
                            "source.pdf",
                            src_dir_fd=self.uploads_fd,
                            dst_dir_fd=source_fd,
                        )
                        os.chmod(
                            "source.pdf",
                            0o600,
                            dir_fd=source_fd,
                            follow_symlinks=False,
                        )
                        os.fsync(source_fd)
                        source_created = True
                    else:
                        with os.fdopen(source_descriptor, "rb") as source_stream:
                            metadata = os.fstat(source_stream.fileno())
                            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != self.size:
                                raise RepositoryCorruptionError(
                                    "existing paper source size does not match"
                                )
                            digest = hashlib.sha256()
                            for chunk in iter(lambda: source_stream.read(1024 * 1024), b""):
                                digest.update(chunk)
                            if digest.hexdigest() != paper_id:
                                raise RepositoryCorruptionError(
                                    "existing paper source hash does not match"
                                )
                        try:
                            os.unlink(self.temp_name, dir_fd=self.uploads_fd)
                        except FileNotFoundError:
                            pass

                    existing = _read_record_at(identity_fd)
                    if existing is not None:
                        try:
                            _validate_record_source(existing, record)
                        except Exception:
                            if source_created:
                                os.unlink("source.pdf", dir_fd=source_fd)
                                os.fsync(source_fd)
                            raise
                        return existing
                    try:
                        _write_record_at(identity_fd, record)
                    except Exception:
                        if source_created:
                            os.unlink("source.pdf", dir_fd=source_fd)
                            os.fsync(source_fd)
                        raise
                    return record
                finally:
                    if source_fd >= 0:
                        os.close(source_fd)
                    if identity_fd >= 0:
                        os.close(identity_fd)
                    os.close(papers_fd)
        except Exception:
            try:
                os.unlink(self.temp_name, dir_fd=self.uploads_fd)
            except FileNotFoundError:
                pass
            raise
        finally:
            self._close_uploads_directory()


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
    except (UploadError, RepositoryCorruptionError):
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
    max_request_bytes = limit + MAX_MULTIPART_OVERHEAD_BYTES
    if content_length is not None and content_length > max_request_bytes:
        raise UploadTooLargeError("declared multipart size exceeds the configured limit")
    current_header_name = bytearray()
    current_header_value = bytearray()
    header_buffer_bytes = 0
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
        nonlocal header_buffer_bytes
        header_buffer_bytes += end - start
        if header_buffer_bytes > MAX_MULTIPART_OVERHEAD_BYTES:
            raise UploadTooLargeError("multipart overhead exceeds the configured limit")
        current_header_name.extend(data[start:end])

    def on_header_value(data: bytes, start: int, end: int) -> None:
        nonlocal header_buffer_bytes
        header_buffer_bytes += end - start
        if header_buffer_bytes > MAX_MULTIPART_OVERHEAD_BYTES:
            raise UploadTooLargeError("multipart overhead exceeds the configured limit")
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
    parser = MultipartParser(
        parameters[b"boundary"],
        callbacks,
        max_header_size=MAX_MULTIPART_OVERHEAD_BYTES,
    )
    request_size = 0
    try:
        for chunk in chunks:
            data = bytes(chunk)
            request_size += len(data)
            if request_size > max_request_bytes:
                raise UploadTooLargeError("actual multipart size exceeds the configured limit")
            parser.write(data)
            file_bytes = sink.size if sink is not None else 0
            if request_size - file_bytes > MAX_MULTIPART_OVERHEAD_BYTES:
                raise UploadTooLargeError("multipart overhead exceeds the configured limit")
        parser.finalize()
        if not ended or not file_seen or not file_ended or sink is None:
            raise UploadParseError("multipart request is incomplete")
        return sink.finish()
    except (UploadError, RepositoryCorruptionError, UnicodeDecodeError):
        if sink is not None:
            sink.abort()
        raise
    except MultipartParseError as exc:
        if sink is not None:
            sink.abort()
        if "header size" in str(exc).lower():
            raise UploadTooLargeError("multipart overhead exceeds the configured limit") from exc
        raise UploadParseError(str(exc)) from exc
    except Exception as exc:
        if sink is not None:
            sink.abort()
        raise UploadInterruptedError(str(exc)) from exc

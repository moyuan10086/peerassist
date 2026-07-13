from __future__ import annotations

import hashlib
import os
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

import peerassist.upload as upload
from peerassist.job_repository import PaperRepository
from peerassist.upload import (
    UploadInterruptedError,
    UploadParseError,
    UploadTooLargeError,
    UploadValidationError,
    persist_multipart_upload,
    persist_pdf_upload,
)

PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
BOUNDARY = "peerassist-boundary"


def _multipart_body(
    content: bytes,
    *,
    filename: str = "manuscript.pdf",
    content_type: str = "application/pdf",
    boundary: str = BOUNDARY,
) -> bytes:
    return b"".join(
        [
            f"--{boundary}\r\n".encode(),
            (
                'Content-Disposition: form-data; name="file"; '
                f'filename="{filename}"\r\n'
            ).encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            content,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )


def _chunks(content: bytes, sizes: tuple[int, ...] = (1, 2, 7, 3, 11)):
    offset = 0
    index = 0
    while offset < len(content):
        size = sizes[index % len(sizes)]
        yield content[offset : offset + size]
        offset += size
        index += 1


def _assert_no_partial_upload(data_dir: Path) -> None:
    assert not list((data_dir / "papers").rglob("paper.json"))
    assert not list((data_dir / "papers").rglob("source.pdf"))
    assert not list((data_dir / "papers").rglob("*.tmp"))


def test_valid_multipart_pdf_is_hashed_and_persisted(tmp_path: Path) -> None:
    repository = PaperRepository(tmp_path)
    body = _multipart_body(PDF)

    record = persist_multipart_upload(
        _chunks(body),
        content_type=f'multipart/form-data; boundary="{BOUNDARY}"',
        content_length=len(body),
        repository=repository,
    )

    expected_sha = hashlib.sha256(PDF).hexdigest()
    source = tmp_path / "papers" / expected_sha / "source" / "source.pdf"
    assert record.paper_id == expected_sha
    assert record.source_pdf_name == "manuscript.pdf"
    assert record.source_pdf_path == "source/source.pdf"
    assert record.size_bytes == len(PDF)
    assert record.content_type == "application/pdf"
    assert source.read_bytes() == PDF
    assert repository.get(expected_sha) == record


def test_duplicate_content_returns_the_original_record_and_one_source(tmp_path: Path) -> None:
    repository = PaperRepository(tmp_path)

    first = persist_pdf_upload(
        _chunks(PDF),
        filename="first.pdf",
        content_type="application/pdf",
        repository=repository,
    )
    duplicate = persist_pdf_upload(
        _chunks(PDF),
        filename="renamed.pdf",
        content_type="application/pdf",
        repository=repository,
    )

    assert duplicate == first
    assert first.source_pdf_name == "first.pdf"
    assert len(list((tmp_path / "papers").rglob("source.pdf"))) == 1


@pytest.mark.parametrize(
    ("content_type", "content"),
    [
        ("text/plain", PDF),
        ("application/octet-stream", PDF),
        ("application/pdf", b"not a pdf"),
        ("application/x-pdf", b"%PDX-1.7\n"),
    ],
)
def test_rejects_forged_mime_and_pdf_header_combinations(
    tmp_path: Path,
    content_type: str,
    content: bytes,
) -> None:
    repository = PaperRepository(tmp_path)
    body = _multipart_body(content, content_type=content_type)

    with pytest.raises(UploadValidationError):
        persist_multipart_upload(
            _chunks(body),
            content_type=f"multipart/form-data; boundary={BOUNDARY}",
            repository=repository,
        )

    _assert_no_partial_upload(tmp_path)


def test_accepts_pdf_compatible_mime_with_parameters(tmp_path: Path) -> None:
    repository = PaperRepository(tmp_path)

    record = persist_pdf_upload(
        _chunks(PDF),
        filename="paper.pdf",
        content_type="application/pdf; charset=binary",
        repository=repository,
    )

    assert record.content_type == "application/pdf"


def test_rejects_empty_pdf_body_without_leaving_partial_state(tmp_path: Path) -> None:
    repository = PaperRepository(tmp_path)
    body = _multipart_body(b"")

    with pytest.raises(UploadValidationError, match="empty"):
        persist_multipart_upload(
            _chunks(body),
            content_type=f"multipart/form-data; boundary={BOUNDARY}",
            repository=repository,
        )

    _assert_no_partial_upload(tmp_path)


@pytest.mark.parametrize("filename", ["../../paper.pdf", "folder/paper.pdf", r"..\paper.pdf"])
def test_rejects_path_traversal_filename(tmp_path: Path, filename: str) -> None:
    repository = PaperRepository(tmp_path)
    body = _multipart_body(PDF, filename=filename)

    with pytest.raises(UploadValidationError, match="filename"):
        persist_multipart_upload(
            _chunks(body),
            content_type=f"multipart/form-data; boundary={BOUNDARY}",
            repository=repository,
        )

    _assert_no_partial_upload(tmp_path)


def test_uses_configured_pdf_size_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = PaperRepository(tmp_path)
    monkeypatch.setattr(upload, "get_settings", lambda: SimpleNamespace(max_pdf_bytes=len(PDF) - 1))

    with pytest.raises(UploadTooLargeError):
        persist_pdf_upload(
            _chunks(PDF),
            filename="paper.pdf",
            content_type="application/pdf",
            repository=repository,
        )

    _assert_no_partial_upload(tmp_path)


def test_missing_length_metadata_is_allowed_and_actual_size_is_recorded(tmp_path: Path) -> None:
    repository = PaperRepository(tmp_path)

    record = persist_pdf_upload(
        _chunks(PDF),
        filename="paper.pdf",
        content_type="application/pdf",
        content_length=None,
        repository=repository,
    )

    assert record.size_bytes == len(PDF)


def test_dishonest_low_length_does_not_bypass_actual_byte_count(tmp_path: Path) -> None:
    repository = PaperRepository(tmp_path)

    with pytest.raises(UploadTooLargeError):
        persist_pdf_upload(
            _chunks(PDF),
            filename="paper.pdf",
            content_type="application/pdf",
            content_length=1,
            max_pdf_bytes=len(PDF) - 1,
            repository=repository,
        )

    _assert_no_partial_upload(tmp_path)


def test_declared_oversize_is_rejected_before_stream_consumption(tmp_path: Path) -> None:
    repository = PaperRepository(tmp_path)
    consumed = False

    def content():
        nonlocal consumed
        consumed = True
        yield PDF

    with pytest.raises(UploadTooLargeError):
        persist_pdf_upload(
            content(),
            filename="paper.pdf",
            content_type="application/pdf",
            content_length=len(PDF) + 1,
            max_pdf_bytes=len(PDF),
            repository=repository,
        )

    assert consumed is False
    _assert_no_partial_upload(tmp_path)


def test_declared_oversize_multipart_is_rejected_before_stream_consumption(
    tmp_path: Path,
) -> None:
    repository = PaperRepository(tmp_path)
    consumed = False

    def content():
        nonlocal consumed
        consumed = True
        yield _multipart_body(PDF)

    with pytest.raises(UploadTooLargeError):
        persist_multipart_upload(
            content(),
            content_type=f"multipart/form-data; boundary={BOUNDARY}",
            content_length=len(PDF) + upload.MAX_MULTIPART_OVERHEAD_BYTES + 1,
            max_pdf_bytes=len(PDF),
            repository=repository,
        )

    assert consumed is False
    _assert_no_partial_upload(tmp_path)


def test_chunk_crossing_limit_is_removed(tmp_path: Path) -> None:
    repository = PaperRepository(tmp_path)
    chunks = [PDF[:10], PDF[10:20], PDF[20:]]

    with pytest.raises(UploadTooLargeError):
        persist_pdf_upload(
            chunks,
            filename="paper.pdf",
            content_type="application/pdf",
            max_pdf_bytes=19,
            repository=repository,
        )

    _assert_no_partial_upload(tmp_path)


def test_interrupted_stream_cleans_temp_and_durable_state(tmp_path: Path) -> None:
    repository = PaperRepository(tmp_path)

    def interrupted():
        yield PDF[:12]
        raise OSError("client disconnected")

    with pytest.raises(UploadInterruptedError, match="client disconnected"):
        persist_pdf_upload(
            interrupted(),
            filename="paper.pdf",
            content_type="application/pdf",
            repository=repository,
        )

    _assert_no_partial_upload(tmp_path)


def test_malformed_multipart_cleans_temp_and_durable_state(tmp_path: Path) -> None:
    repository = PaperRepository(tmp_path)
    body = _multipart_body(PDF)[: -len(f"--{BOUNDARY}--\r\n")]

    with pytest.raises(UploadParseError):
        persist_multipart_upload(
            _chunks(body),
            content_type=f"multipart/form-data; boundary={BOUNDARY}",
            repository=repository,
        )

    _assert_no_partial_upload(tmp_path)


def test_simultaneous_uploads_use_unique_temp_files(tmp_path: Path) -> None:
    repository = PaperRepository(tmp_path)
    ready = [threading.Event(), threading.Event()]
    release = threading.Event()

    def paused(content: bytes, index: int):
        yield content[:10]
        ready[index].set()
        assert release.wait(timeout=5)
        yield content[10:]

    other_pdf = PDF.replace(b"1 0 obj", b"2 0 obj")
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                persist_pdf_upload,
                paused(content, index),
                filename=f"paper-{index}.pdf",
                content_type="application/pdf",
                repository=repository,
            )
            for index, content in enumerate((PDF, other_pdf))
        ]
        assert all(event.wait(timeout=5) for event in ready)
        temp_files = list((tmp_path / "papers" / ".uploads").glob("*.tmp"))
        assert len(temp_files) == 2
        assert len({path.name for path in temp_files}) == 2
        release.set()
        records = [future.result(timeout=5) for future in futures]

    assert len({record.paper_id for record in records}) == 2
    assert not list((tmp_path / "papers" / ".uploads").glob("*.tmp"))


def test_concurrent_duplicate_uploads_converge_without_truncation(tmp_path: Path) -> None:
    repository = PaperRepository(tmp_path)
    barrier = threading.Barrier(2)

    def synchronized_chunks():
        yield PDF[:10]
        barrier.wait(timeout=5)
        yield PDF[10:]

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                persist_pdf_upload,
                synchronized_chunks(),
                filename=f"paper-{index}.pdf",
                content_type="application/pdf",
                repository=repository,
            )
            for index in range(2)
        ]
        records = [future.result(timeout=5) for future in futures]

    expected_sha = hashlib.sha256(PDF).hexdigest()
    source = tmp_path / "papers" / expected_sha / "source" / "source.pdf"
    assert records[0] == records[1]
    assert source.read_bytes() == PDF
    assert len(list((tmp_path / "papers").rglob("source.pdf"))) == 1


def test_persisted_pdf_mode_is_0600(tmp_path: Path) -> None:
    repository = PaperRepository(tmp_path)

    record = persist_pdf_upload(
        [PDF],
        filename="paper.pdf",
        content_type="application/pdf",
        repository=repository,
    )

    source = tmp_path / "papers" / record.paper_id / "source" / "source.pdf"
    assert stat.S_IMODE(os.stat(source).st_mode) == 0o600

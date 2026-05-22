from __future__ import annotations

from pathlib import Path

import pytest

from util.paper_input import DownloadLimitError, _download_pdf, infer_paper_key


class _FakeResponse:
    def __init__(self, body: bytes, *, content_length: int | None = None) -> None:
        self._body = body
        self._offset = 0
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if self._offset >= len(self._body):
            return b""
        if size is None or size < 0:
            size = len(self._body) - self._offset
        end = min(len(self._body), self._offset + size)
        chunk = self._body[self._offset : end]
        self._offset = end
        return chunk


def test_download_pdf_rejects_large_content_length(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EXECUTION_PAPER_PDF_MAX_BYTES", "8")

    def fake_urlopen(request: object, timeout: int) -> _FakeResponse:
        return _FakeResponse(b"%PDF-123456789", content_length=100)

    monkeypatch.setattr("util.paper_input.urlopen", fake_urlopen)
    target = tmp_path / "paper.pdf"

    with pytest.raises(DownloadLimitError, match="paper_pdf_too_large"):
        _download_pdf("https://example.test/paper.pdf", target)

    assert not target.exists()
    assert not (tmp_path / "paper.pdf.part").exists()


def test_download_pdf_rejects_large_stream_without_content_length(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EXECUTION_PAPER_PDF_MAX_BYTES", "8")

    def fake_urlopen(request: object, timeout: int) -> _FakeResponse:
        return _FakeResponse(b"%PDF-123456789", content_length=None)

    monkeypatch.setattr("util.paper_input.urlopen", fake_urlopen)

    with pytest.raises(DownloadLimitError, match="bytes_read"):
        _download_pdf("https://example.test/paper.pdf", tmp_path / "paper.pdf")


def test_infer_paper_key_uses_openreview_forum_id() -> None:
    assert infer_paper_key("https://openreview.net/forum?id=YqFLsI44vN") == "YqFLsI44vN"
    assert infer_paper_key("https://openreview.net/attachment?id=wKPQXtVejB&name=supplementary_material") == (
        "wKPQXtVejB"
    )


def test_infer_paper_key_uses_anonymous_4open_repo_id() -> None:
    assert infer_paper_key("https://anonymous.4open.science/r/BMAS-AAD0/README.md") == "BMAS-AAD0"

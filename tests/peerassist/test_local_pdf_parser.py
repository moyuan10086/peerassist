from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pymupdf

import peerassist.local_pdf_parser as local_parser
from peerassist.local_pdf_parser import LocalParseStatus, parse_pdf_locally


def _selectable_pdf(path: Path) -> None:
    document = pymupdf.open()
    first = document.new_page()
    first.insert_text((72, 72), "PeerAssist Local Parser")
    first.insert_text((72, 96), "Claim one is supported by experiment A.")
    second = document.new_page()
    second.insert_text((72, 72), "Results")
    second.insert_text((72, 96), "Accuracy improved from 80% to 85%.")
    document.save(path)
    document.close()


def test_local_parser_extracts_pages_blocks_lines_locators_and_bbox(tmp_path: Path) -> None:
    source = tmp_path / "paper.pdf"
    output = tmp_path / "parse"
    _selectable_pdf(source)

    result = parse_pdf_locally(source, output, timeout_seconds=10)

    assert result.status is LocalParseStatus.OK
    assert result.provider_name == "local_pymupdf"
    assert result.external_upload_required is False
    assert result.page_count == 2
    assert result.text_char_count > 40
    assert result.markdown_path is not None
    assert result.content_list_path is not None
    assert Path(result.markdown_path).read_text(encoding="utf-8").startswith("<!-- page: 1 -->")
    content = json.loads(Path(result.content_list_path).read_text(encoding="utf-8"))
    assert {item["page"] for item in content} == {1, 2}
    first_block = content[0]
    assert first_block["type"] == "text"
    assert len(first_block["bbox"]) == 4
    assert first_block["lines"]
    assert first_block["lines"][0]["locator"].startswith("page 1, block 1, line 1")
    assert len(first_block["lines"][0]["bbox"]) == 4
    assert result.pages[0].text.startswith("PeerAssist")


def test_blank_or_image_only_pdf_requires_ocr_without_external_call(tmp_path: Path) -> None:
    source = tmp_path / "scan.pdf"
    output = tmp_path / "parse"
    document = pymupdf.open()
    document.new_page()
    document.save(source)
    document.close()

    result = parse_pdf_locally(source, output, timeout_seconds=10)

    assert result.status is LocalParseStatus.OCR_REQUIRED
    assert result.external_upload_required is False
    assert "image_only_or_no_selectable_text" in result.warnings
    assert result.text_char_count == 0


def test_corrupted_pdf_returns_explicit_local_state(tmp_path: Path) -> None:
    source = tmp_path / "broken.pdf"
    source.write_bytes(b"%PDF-not-valid")

    result = parse_pdf_locally(source, tmp_path / "parse", timeout_seconds=10)

    assert result.status is LocalParseStatus.CORRUPTED
    assert result.external_upload_required is False
    assert result.markdown_path is None
    assert result.content_list_path is None


def test_page_extraction_corruption_returns_corrupted_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "partially-broken.pdf"
    source.write_bytes(b"%PDF-placeholder")

    class BrokenDocument:
        needs_pass = False
        page_count = 1

        def load_page(self, index: int):
            raise RuntimeError("cannot load page tree")

        def close(self) -> None:
            return None

    monkeypatch.setattr(pymupdf, "open", lambda path: BrokenDocument())
    result = local_parser._parse_worker(
        source,
        tmp_path / "parse",
        memory_limit_bytes=0,
        cpu_limit_seconds=0,
    )

    assert result.status is LocalParseStatus.CORRUPTED
    assert result.error_code == "corrupted_pdf"
    assert "pdf_page_extraction_failed" in result.warnings


def test_encrypted_pdf_returns_explicit_local_state(tmp_path: Path) -> None:
    source = tmp_path / "encrypted.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "private")
    document.save(
        source,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="owner-secret",
        user_pw="user-secret",
    )
    document.close()

    result = parse_pdf_locally(source, tmp_path / "parse", timeout_seconds=10)

    assert result.status is LocalParseStatus.ENCRYPTED
    assert result.external_upload_required is False
    assert "password_required" in result.warnings


def test_local_parser_timeout_is_explicit_and_leaves_no_parse_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "paper.pdf"
    _selectable_pdf(source)

    def timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="local-parser", timeout=0.01)

    monkeypatch.setattr(local_parser.subprocess, "run", timeout)
    output = tmp_path / "parse"
    output.mkdir()
    atomic_markdown_temp = output / ".paper.local.md.deadbeef.tmp"
    atomic_content_temp = output / ".paper.local.content_list.json.deadbeef.tmp"
    atomic_markdown_temp.write_text("partial", encoding="utf-8")
    atomic_content_temp.write_text("partial", encoding="utf-8")
    result = parse_pdf_locally(source, output, timeout_seconds=0.01)

    assert result.status is LocalParseStatus.FAILED
    assert "local_parser_timeout" in result.warnings
    assert not (output / "paper.local.md").exists()
    assert not (output / "paper.local.content_list.json").exists()
    assert not atomic_markdown_temp.exists()
    assert not atomic_content_temp.exists()

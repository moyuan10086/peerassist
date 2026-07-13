"""Local-first PDF parsing with selectable text and stable PDF locators."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from common.storage import write_bytes_atomic, write_json_atomic, write_text_atomic


class LocalParseStatus(StrEnum):
    OK = "ok"
    OCR_REQUIRED = "ocr_required"
    ENCRYPTED = "encrypted"
    CORRUPTED = "corrupted"
    FAILED = "failed"


class LocalPdfLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    locator: str
    bbox: list[float]


class LocalPdfBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: str
    page: int
    text: str
    bbox: list[float]
    lines: list[LocalPdfLine] = Field(default_factory=list)


class LocalPdfPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int
    width: float
    height: float
    text: str
    blocks: list[LocalPdfBlock] = Field(default_factory=list)


class LocalPdfParseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "peerassist.local_pdf_parse.v1"
    status: LocalParseStatus
    provider_name: str = "local_pymupdf"
    external_upload_required: bool = False
    source_pdf: str
    markdown_path: str | None = None
    content_list_path: str | None = None
    page_count: int = 0
    text_char_count: int = 0
    pages: list[LocalPdfPage] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error_code: str | None = None


def _bbox(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return [0.0, 0.0, 0.0, 0.0]
    return [round(float(item), 3) for item in value]


def _apply_resource_limits(memory_limit_bytes: int, cpu_limit_seconds: int) -> None:
    try:
        import resource
    except ImportError:
        return
    if memory_limit_bytes > 0:
        resource.setrlimit(resource.RLIMIT_AS, (memory_limit_bytes, memory_limit_bytes))
    if cpu_limit_seconds > 0:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit_seconds, cpu_limit_seconds + 1))


def _parse_worker(
    source_pdf: Path,
    output_dir: Path,
    *,
    memory_limit_bytes: int,
    cpu_limit_seconds: int,
) -> LocalPdfParseResult:
    _apply_resource_limits(memory_limit_bytes, cpu_limit_seconds)
    import pymupdf

    try:
        document = pymupdf.open(source_pdf)
    except Exception:
        return LocalPdfParseResult(
            status=LocalParseStatus.CORRUPTED,
            source_pdf=str(source_pdf.resolve()),
            warnings=["pdf_open_failed"],
            error_code="corrupted_pdf",
        )
    try:
        if document.needs_pass:
            return LocalPdfParseResult(
                status=LocalParseStatus.ENCRYPTED,
                source_pdf=str(source_pdf.resolve()),
                page_count=document.page_count,
                warnings=["password_required"],
                error_code="encrypted_pdf",
            )

        pages: list[LocalPdfPage] = []
        content_list: list[dict[str, Any]] = []
        markdown_lines: list[str] = []
        text_char_count = 0
        for page_index in range(document.page_count):
            page_number = page_index + 1
            page = document.load_page(page_index)
            page_dict = page.get_text("dict", sort=True)
            blocks: list[LocalPdfBlock] = []
            page_text_lines: list[str] = []
            markdown_lines.append(f"<!-- page: {page_number} -->")
            block_number = 0
            for raw_block in page_dict.get("blocks", []):
                if not isinstance(raw_block, dict) or int(raw_block.get("type", -1)) != 0:
                    continue
                raw_lines = raw_block.get("lines")
                if not isinstance(raw_lines, list):
                    continue
                lines: list[LocalPdfLine] = []
                for raw_line in raw_lines:
                    if not isinstance(raw_line, dict):
                        continue
                    spans = raw_line.get("spans")
                    if not isinstance(spans, list):
                        continue
                    text = "".join(
                        str(span.get("text") or "")
                        for span in spans
                        if isinstance(span, dict)
                    ).strip()
                    if not text:
                        continue
                    line_number = len(lines) + 1
                    locator = (
                        f"page {page_number}, block {block_number + 1}, line {line_number}"
                    )
                    lines.append(
                        LocalPdfLine(
                            text=text,
                            locator=locator,
                            bbox=_bbox(raw_line.get("bbox")),
                        )
                    )
                if not lines:
                    continue
                block_number += 1
                block_text = "\n".join(line.text for line in lines)
                block = LocalPdfBlock(
                    block_id=f"P{page_number:04d}-B{block_number:04d}",
                    page=page_number,
                    text=block_text,
                    bbox=_bbox(raw_block.get("bbox")),
                    lines=lines,
                )
                blocks.append(block)
                page_text_lines.extend(line.text for line in lines)
                markdown_lines.extend(line.text for line in lines)
                content_list.append(
                    {
                        "page_idx": page_index,
                        "page": page_number,
                        "type": "text",
                        "text": block_text,
                        "bbox": block.bbox,
                        "block_id": block.block_id,
                        "lines": [line.model_dump(mode="json") for line in lines],
                    }
                )
            markdown_lines.append("")
            page_text = "\n".join(page_text_lines)
            text_char_count += len(page_text)
            pages.append(
                LocalPdfPage(
                    page=page_number,
                    width=float(page.rect.width),
                    height=float(page.rect.height),
                    text=page_text,
                    blocks=blocks,
                )
            )

        output_dir.mkdir(parents=True, exist_ok=True)
        markdown_path = output_dir / "paper.local.md"
        content_list_path = output_dir / "paper.local.content_list.json"
        write_text_atomic(markdown_path, "\n".join(markdown_lines).rstrip() + "\n")
        write_bytes_atomic(
            content_list_path,
            json.dumps(content_list, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        status = LocalParseStatus.OK if text_char_count else LocalParseStatus.OCR_REQUIRED
        warnings = [] if text_char_count else ["image_only_or_no_selectable_text"]
        return LocalPdfParseResult(
            status=status,
            source_pdf=str(source_pdf.resolve()),
            markdown_path=str(markdown_path.resolve()),
            content_list_path=str(content_list_path.resolve()),
            page_count=document.page_count,
            text_char_count=text_char_count,
            pages=pages,
            warnings=warnings,
        )
    finally:
        document.close()


def parse_pdf_locally(
    source_pdf: Path,
    output_dir: Path,
    *,
    timeout_seconds: float = 30,
    memory_limit_bytes: int = 1024 * 1024 * 1024,
) -> LocalPdfParseResult:
    """Parse a PDF in a resource-limited local subprocess."""

    source_pdf = Path(source_pdf)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = output_dir / "paper.local.md"
    content_list_path = output_dir / "paper.local.content_list.json"
    markdown_path.unlink(missing_ok=True)
    content_list_path.unlink(missing_ok=True)
    if not source_pdf.is_file():
        return LocalPdfParseResult(
            status=LocalParseStatus.FAILED,
            source_pdf=str(source_pdf),
            warnings=["source_pdf_missing"],
            error_code="source_pdf_missing",
        )

    result_path = output_dir / f".local-parse-result-{uuid4().hex}.json"
    command = [
        sys.executable,
        "-m",
        "peerassist.local_pdf_parser",
        "--worker",
        "--source",
        str(source_pdf.resolve()),
        "--output-dir",
        str(output_dir.resolve()),
        "--result",
        str(result_path.resolve()),
        "--memory-limit-bytes",
        str(memory_limit_bytes),
        "--cpu-limit-seconds",
        str(max(1, int(timeout_seconds) + 1)),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        result_path.unlink(missing_ok=True)
        markdown_path.unlink(missing_ok=True)
        content_list_path.unlink(missing_ok=True)
        return LocalPdfParseResult(
            status=LocalParseStatus.FAILED,
            source_pdf=str(source_pdf.resolve()),
            warnings=["local_parser_timeout"],
            error_code="local_parser_timeout",
        )
    if completed.returncode != 0 or not result_path.is_file():
        result_path.unlink(missing_ok=True)
        markdown_path.unlink(missing_ok=True)
        content_list_path.unlink(missing_ok=True)
        return LocalPdfParseResult(
            status=LocalParseStatus.FAILED,
            source_pdf=str(source_pdf.resolve()),
            warnings=["local_parser_failed"],
            error_code="local_parser_failed",
        )
    try:
        return LocalPdfParseResult.model_validate_json(result_path.read_bytes())
    finally:
        result_path.unlink(missing_ok=True)


def _worker_main(args: argparse.Namespace) -> int:
    result = _parse_worker(
        Path(args.source),
        Path(args.output_dir),
        memory_limit_bytes=int(args.memory_limit_bytes),
        cpu_limit_seconds=int(args.cpu_limit_seconds),
    )
    write_json_atomic(Path(args.result), result.model_dump(mode="json"))
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--source")
    parser.add_argument("--output-dir")
    parser.add_argument("--result")
    parser.add_argument("--memory-limit-bytes", type=int, default=1024 * 1024 * 1024)
    parser.add_argument("--cpu-limit-seconds", type=int, default=30)
    return parser.parse_args()


if __name__ == "__main__":
    worker_args = _parse_args()
    raise SystemExit(_worker_main(worker_args) if worker_args.worker else 2)

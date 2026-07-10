"""Build PeerAssist evidence ledgers from parser artifacts."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from schemas.peerassist import EvidenceItem, EvidenceLedger, EvidenceType

PAGE_MARKER_RE = re.compile(r"<!--\s*page\s*:\s*(?P<page>\d+)\s*-->", re.I)
FIGURE_RE = re.compile(r"\b(?:fig(?:ure)?\.?|图)\s*(?P<num>S?\d+[A-Za-z0-9_.-]*)\s*[:：]?", re.I)
TABLE_RE = re.compile(r"\b(?:table|表)\s*(?P<num>S?\d+[A-Za-z0-9_.-]*)\s*[:：]?", re.I)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _coverage(items: list[EvidenceItem]) -> dict[str, int]:
    counts = {kind.value: 0 for kind in EvidenceType}
    for item in items:
        counts[item.type.value] = counts.get(item.type.value, 0) + 1
    return counts


def _read_json(path: Path | None) -> Any:
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except json.JSONDecodeError:
        return None


def _line_item_type(text: str) -> EvidenceType:
    stripped = text.strip()
    if stripped.startswith("#"):
        return EvidenceType.SECTION
    if FIGURE_RE.search(stripped):
        return EvidenceType.FIGURE_CAPTION
    if TABLE_RE.search(stripped) or (stripped.startswith("|") and stripped.endswith("|")):
        return EvidenceType.TABLE
    return EvidenceType.TEXT_SPAN


def _items_from_markdown(markdown_path: Path, *, warnings: list[str]) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    page = 1
    line_on_page = 0
    current_section = ""
    for raw_line in markdown_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        marker = PAGE_MARKER_RE.search(raw_line)
        if marker:
            page = int(marker.group("page"))
            line_on_page = 0
            continue

        text = raw_line.strip()
        if not text:
            continue

        line_on_page += 1
        item_type = _line_item_type(text)
        if item_type is EvidenceType.SECTION:
            current_section = text.lstrip("#").strip()
        item_id = f"P{page:02d}-L{line_on_page:03d}"
        locator = f"page {page}, line {line_on_page}"
        items.append(
            EvidenceItem(
                id=item_id,
                type=item_type,
                page=page,
                section=current_section,
                locator=locator,
                text=text,
                source_path=str(markdown_path),
                metadata={"line": line_on_page},
            )
        )

    if not items:
        warnings.append("mineru_markdown_empty")
    return items


def _iter_content_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("content", "contents", "blocks", "data", "items", "result"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _items_from_content_list(content_list_path: Path | None, *, warnings: list[str]) -> list[EvidenceItem]:
    payload = _read_json(content_list_path)
    if payload is None:
        if content_list_path is not None:
            warnings.append("mineru_content_list_unreadable")
        return []

    items: list[EvidenceItem] = []
    table_counter = 0
    for record in _iter_content_records(payload):
        record_type = str(record.get("type") or record.get("category") or "").lower()
        rows = record.get("rows")
        if record_type != "table" or not isinstance(rows, list):
            continue
        table_counter += 1
        table_label = str(record.get("table_id") or record.get("label") or f"Table {table_counter}").strip()
        page = int(record.get("page") or record.get("page_no") or record.get("page_num") or 0) or None
        for row_idx, row in enumerate(rows, start=1):
            if not isinstance(row, list):
                continue
            for col_idx, value in enumerate(row, start=1):
                text = str(value or "").strip()
                if not text:
                    continue
                items.append(
                    EvidenceItem(
                        id=f"T{table_counter:03d}-R{row_idx:03d}-C{col_idx:03d}",
                        type=EvidenceType.TABLE_CELL,
                        page=page,
                        section="",
                        locator=f"{table_label}, row {row_idx}, column {col_idx}",
                        text=text,
                        source_path=str(content_list_path),
                        metadata={
                            "table": table_label,
                            "row": row_idx,
                            "column": col_idx,
                            "original_value": text,
                        },
                    )
                )
    return items


def build_evidence_ledger(
    *,
    paper_id: str,
    source_pdf: Path,
    mineru_markdown_path: Path | None,
    mineru_content_list_path: Path | None = None,
) -> EvidenceLedger:
    """Build a best-effort evidence ledger from parser outputs.

    The builder is deliberately conservative: missing parser structures are
    represented as coverage zeros and warnings instead of inferred facts.
    """
    warnings: list[str] = []
    items: list[EvidenceItem] = []
    if mineru_markdown_path is None or not mineru_markdown_path.exists():
        warnings.append("mineru_markdown_missing")
    else:
        items.extend(_items_from_markdown(mineru_markdown_path, warnings=warnings))

    items.extend(_items_from_content_list(mineru_content_list_path, warnings=warnings))

    source_sha256 = _sha256(source_pdf) if source_pdf.exists() else ""
    metadata = {
        "provider": "mineru",
        "warnings": warnings,
        "mineru_markdown_path": str(mineru_markdown_path or ""),
        "mineru_content_list_path": str(mineru_content_list_path or ""),
    }
    return EvidenceLedger(
        paper_id=paper_id,
        source_sha256=source_sha256,
        items=items,
        coverage=_coverage(items),
        metadata=metadata,
    )

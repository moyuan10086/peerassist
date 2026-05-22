from __future__ import annotations

import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse, urlunparse
from urllib.request import Request, urlopen

from util.run_layout import slugify_run_key

PDF_MAGIC = b"%PDF-"
DOWNLOAD_CHUNK_SIZE = 256 * 1024
DEFAULT_PAPER_PDF_MAX_BYTES = 100 * 1024 * 1024
DEFAULT_PAPER_PDF_TIMEOUT_SEC = 180


class DownloadLimitError(RuntimeError):
    """Raised when a remote paper PDF exceeds configured safety bounds."""


@dataclass(frozen=True)
class PaperInput:
    source: str
    source_type: str
    path: Path
    downloaded: bool = False


def is_url(value: str | None) -> bool:
    parsed = urlparse(str(value or "").strip())
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def looks_like_pdf(path: Path) -> bool:
    if not path.exists() or not path.is_file():
        return False
    try:
        return path.read_bytes()[: len(PDF_MAGIC)] == PDF_MAGIC
    except Exception:
        return False


def infer_paper_key(source: str | None, *, fallback: str = "paper") -> str:
    token = str(source or "").strip()
    if not token:
        return fallback
    if is_url(token):
        parsed = urlparse(token)
        arxiv_id = _arxiv_id_from_path(parsed.path)
        if arxiv_id:
            return arxiv_id.replace("/", "_")
        openreview_id = _openreview_id_from_url(parsed)
        if openreview_id:
            return _safe_source_key(openreview_id, fallback=fallback)
        anonymous_id = _anonymous_4open_id_from_url(parsed)
        if anonymous_id:
            return _safe_source_key(anonymous_id, fallback=fallback)
        name = Path(unquote(parsed.path)).name
        if name:
            stem = name[:-4] if name.lower().endswith(".pdf") else Path(name).stem
            if stem:
                return stem
        return parsed.netloc.split(":")[0] or fallback
    return Path(token).expanduser().stem or fallback


def materialize_paper_pdf(
    source: str | Path, destination_dir: str | Path, *, paper_key: str = ""
) -> PaperInput:
    raw_source = str(source).strip()
    if not raw_source:
        raise ValueError("paper PDF input is required")

    destination = Path(destination_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)

    if is_url(raw_source):
        pdf_url = _normalize_pdf_url(raw_source)
        filename = _filename_for_url(pdf_url, paper_key=paper_key)
        target = _dedupe_path(destination / filename)
        _download_pdf(pdf_url, target)
        return PaperInput(source=raw_source, source_type="url", path=target.resolve(), downloaded=True)

    source_path = Path(raw_source).expanduser().resolve()
    if not source_path.exists() or not source_path.is_file():
        raise FileNotFoundError(f"paper pdf not found: {source_path}")
    if not looks_like_pdf(source_path):
        raise ValueError(f"paper input is not a valid PDF: {source_path}")

    filename = _safe_pdf_filename(source_path.name, paper_key=paper_key)
    target = destination / filename
    if source_path != target.resolve():
        target = _dedupe_path(target)
        shutil.copy2(source_path, target)
    else:
        target = source_path
    return PaperInput(source=raw_source, source_type="path", path=target.resolve(), downloaded=False)


def _normalize_pdf_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc.lower().endswith("arxiv.org"):
        arxiv_id = _arxiv_id_from_path(parsed.path)
        if arxiv_id:
            pdf_path = f"/pdf/{arxiv_id}"
            if not pdf_path.lower().endswith(".pdf"):
                pdf_path = f"{pdf_path}.pdf"
            return urlunparse((parsed.scheme, parsed.netloc, pdf_path, "", "", ""))
    return url


def _arxiv_id_from_path(path: str) -> str:
    clean = unquote(path or "").strip("/")
    for prefix in ("abs/", "pdf/"):
        if clean.startswith(prefix):
            token = clean[len(prefix) :].strip("/")
            if token.lower().endswith(".pdf"):
                token = token[:-4]
            return token
    return ""


def _openreview_id_from_url(parsed) -> str:
    host = str(parsed.netloc or "").lower()
    if not host.endswith("openreview.net"):
        return ""
    query = parse_qs(parsed.query or "")
    for key in ("id", "forum"):
        values = query.get(key) or []
        for value in values:
            value = str(value or "").strip()
            if value:
                return value
    return ""


def _anonymous_4open_id_from_url(parsed) -> str:
    host = str(parsed.netloc or "").lower()
    if not host.endswith("anonymous.4open.science"):
        return ""
    parts = [unquote(p).strip() for p in str(parsed.path or "").split("/") if p.strip()]
    try:
        idx = [p.lower() for p in parts].index("r")
    except ValueError:
        return ""
    if idx + 1 < len(parts):
        return parts[idx + 1]
    return ""


def _safe_source_key(value: str, *, fallback: str = "paper") -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("._-")
    return token or fallback


def _filename_for_url(url: str, *, paper_key: str = "") -> str:
    parsed = urlparse(url)
    arxiv_id = _arxiv_id_from_path(parsed.path)
    if arxiv_id:
        return _safe_pdf_filename(arxiv_id.replace("/", "_"), paper_key=paper_key)
    name = _safe_pdf_filename(Path(unquote(parsed.path)).name, paper_key=paper_key)
    if name != "paper.pdf":
        return name
    return name


def _safe_pdf_filename(name: str, *, paper_key: str = "") -> str:
    token = unquote(str(name or "")).replace("\\", "/").split("/")[-1].strip()
    token = re.sub(r"[^A-Za-z0-9._-]+", "_", token)
    token = token.strip("._-")
    if not token:
        token = slugify_run_key(paper_key, fallback="paper")
    if not token.lower().endswith(".pdf"):
        token = f"{token}.pdf"
    return token


def _dedupe_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix or ".pdf"
    for index in range(1, 10_000):
        candidate = path.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"could not allocate destination path under {path.parent}")


def _download_pdf(url: str, target: Path) -> None:
    request = Request(url, headers={"User-Agent": "FactReview/0.1"})
    temp = target.with_suffix(f"{target.suffix}.part")
    try:
        max_bytes = _download_max_bytes()
        total_timeout = _download_timeout_sec()
        deadline = time.monotonic() + total_timeout
        with urlopen(request, timeout=min(10, total_timeout)) as response, open(temp, "wb") as fh:
            content_length = _content_length(response)
            if content_length is not None and max_bytes > 0 and content_length > max_bytes:
                raise DownloadLimitError(
                    f"paper_pdf_too_large: {url} content_length={content_length} max_bytes={max_bytes}"
                )
            total = 0
            while True:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"paper_pdf_download_timeout: {url} timeout_sec={total_timeout}")
                chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if max_bytes > 0 and total > max_bytes:
                    raise DownloadLimitError(
                        f"paper_pdf_too_large: {url} bytes_read={total} max_bytes={max_bytes}"
                    )
                fh.write(chunk)
        if not looks_like_pdf(temp):
            raise ValueError(f"downloaded content is not a valid PDF: {url}")
        temp.replace(target)
    except Exception:
        try:
            temp.unlink()
        except Exception:
            pass
        raise


def _download_max_bytes() -> int:
    raw = (
        _env_value("EXECUTION_PAPER_PDF_MAX_BYTES")
        or _env_value("FACTREVIEW_PAPER_PDF_MAX_BYTES")
        or str(DEFAULT_PAPER_PDF_MAX_BYTES)
    )
    try:
        return max(int(raw), 0)
    except Exception:
        return DEFAULT_PAPER_PDF_MAX_BYTES


def _download_timeout_sec() -> int:
    raw = (
        _env_value("EXECUTION_PAPER_PDF_TIMEOUT_SEC")
        or _env_value("FACTREVIEW_PAPER_PDF_TIMEOUT_SEC")
        or str(DEFAULT_PAPER_PDF_TIMEOUT_SEC)
    )
    try:
        return max(int(raw), 1)
    except Exception:
        return DEFAULT_PAPER_PDF_TIMEOUT_SEC


def _env_value(name: str) -> str:
    import os

    return str(os.getenv(name) or "").strip()


def _content_length(response: object) -> int | None:
    headers = getattr(response, "headers", None)
    raw = None
    try:
        raw = headers.get("Content-Length") if headers is not None else None
    except Exception:
        raw = None
    if raw in (None, ""):
        try:
            raw = response.getheader("Content-Length")  # type: ignore[attr-defined]
        except Exception:
            raw = None
    try:
        return int(str(raw).strip()) if raw not in (None, "") else None
    except Exception:
        return None

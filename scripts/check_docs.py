#!/usr/bin/env python3
"""Check repository Markdown files for broken local links."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt

_IGNORED_SCHEMES = {"http", "https", "mailto"}
_MARKDOWN = MarkdownIt("commonmark")
_CURRENT_AUTHORITY = "<!-- current-authority -->"
_AUTHORITY_LINK = "<!-- authority: docs/PROJECT_OVERVIEW.md -->"
_AUTHORITY_ENTRIES = (Path("README.md"), Path("docs/README.md"), Path("docs/versions/README.md"))
_STATUS_MARKERS = {
    Path("docs/system_review_2026-07-17.md"): "<!-- status: historical-review -->",
    Path("docs/system_review_2026-07-20.md"): "<!-- status: historical-review -->",
    Path("docs/superpowers/specs/2026-07-17-peerassist-platform-canvas-agent-design.md"): "<!-- status: historical-plan -->",
    Path("docs/product/peerassist-teacher-first-roadmap.md"): "<!-- status: current-reference -->",
    Path("docs/versions/v0.1.0-p0.md"): "<!-- status: version-record -->",
    Path("docs/versions/v0.1.1-p0.md"): "<!-- status: version-record -->",
}
_BANNED_CURRENT_INSTRUCTIONS = (
    "cd /root/.worktrees/peerassist-m0",
    "WorkingDirectory=/root/PeerAssist/current",
    "ExecStart=/root/PeerAssist/current",
)


class RepositoryScanError(Exception):
    """Raised when the requested repository file set cannot be scanned safely."""

    def __init__(self, message: str = "repository scan failed") -> None:
        super().__init__(message)
        self.safe_message = message


def _tracked_markdown(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.md", "*.markdown"],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RepositoryScanError
    return [root / item.decode("utf-8", errors="surrogateescape") for item in result.stdout.split(b"\0") if item]


def _markdown_targets(source: str) -> Iterable[str]:
    pending = list(_MARKDOWN.parse(source))
    while pending:
        token = pending.pop(0)
        if token.type == "link_open":
            target = token.attrGet("href")
            if target:
                yield target
        if token.children:
            pending[0:0] = token.children


def _relative_path(root: Path, path: Path) -> Path:
    try:
        return path.resolve().relative_to(root.resolve())
    except ValueError:
        return Path(path.name)


def _lexical_path(root: Path, item: Path) -> Path | None:
    candidate = Path(os.path.abspath(item if item.is_absolute() else root / item))
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _safe_item(root: Path, item: Path) -> tuple[Path, os.stat_result] | None:
    path = _lexical_path(root, item)
    if path is None:
        return None
    try:
        root_real = root.resolve(strict=True)
        path.parent.resolve(strict=True).relative_to(root_real)
        metadata = path.lstat()
        if not stat.S_ISLNK(metadata.st_mode):
            path.resolve(strict=True).relative_to(root_real)
    except (OSError, RuntimeError, ValueError):
        return None
    return path, metadata


def _safe_sources(root: Path, files: Iterable[Path]) -> list[Path]:
    sources: set[Path] = set()
    for item in files:
        safe = _safe_item(root, Path(item))
        if safe is None:
            continue
        source, metadata = safe
        if stat.S_ISLNK(metadata.st_mode):
            continue
        sources.add(source)
    return sorted(sources, key=lambda path: path.as_posix())


def _explicit_files(root: Path, args: list[str]) -> list[Path]:
    files: list[Path] = []
    for item in args:
        safe = _safe_item(root, Path(item))
        if safe is None:
            raise RepositoryScanError
        path, _ = safe
        files.append(path)
    return files


def find_broken_links(root: Path, files: Iterable[Path]) -> list[tuple[Path, str]]:
    """Return stable ``(source, target)`` pairs for broken local Markdown links."""
    root = root.resolve()
    broken: set[tuple[str, str]] = set()
    for source in _safe_sources(root, files):
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise RepositoryScanError("repository scan failed: unable to read Markdown input") from exc
        source_rel = _relative_path(root, source)
        for raw_target in _markdown_targets(text):
            split = urlsplit(raw_target)
            if split.scheme.lower() in _IGNORED_SCHEMES or (not split.path and split.fragment):
                continue
            if split.scheme or split.netloc:
                continue
            decoded = unquote(split.path)
            if not decoded:
                continue
            try:
                candidate = (source.parent / decoded).resolve()
                candidate.relative_to(root)
                exists = candidate.exists()
            except (OSError, RuntimeError, ValueError):
                broken.add((source_rel.as_posix(), raw_target))
                continue
            if not exists:
                broken.add((source_rel.as_posix(), raw_target))
    return [(Path(source), target) for source, target in sorted(broken)]


def find_authority_errors(root: Path) -> list[str]:
    """Return deterministic documentation-authority contract failures."""
    if not (root / "docs/PROJECT_OVERVIEW.md").is_file():
        return []
    markdown_files = _tracked_markdown(root)
    authority_count = 0
    for path in markdown_files:
        try:
            authority_count += sum(
                line.strip() == _CURRENT_AUTHORITY
                for line in path.read_text(encoding="utf-8").splitlines()
            )
        except (OSError, UnicodeError) as exc:
            raise RepositoryScanError("repository scan failed: unable to read Markdown input") from exc

    errors: list[str] = []
    if authority_count != 1:
        errors.append(f"expected one current authority marker, found {authority_count}")
    for relative in _AUTHORITY_ENTRIES:
        path = root / relative
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if _AUTHORITY_LINK not in text:
            errors.append(f"{relative.as_posix()}: missing authority link marker")
        for instruction in _BANNED_CURRENT_INSTRUCTIONS:
            if instruction in text:
                errors.append(f"{relative.as_posix()}: contains retired current instruction")
    for relative, marker in _STATUS_MARKERS.items():
        path = root / relative
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if marker not in text:
            errors.append(f"{relative.as_posix()}: missing status marker")
    return errors


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path.cwd().resolve()
    try:
        files = _explicit_files(root, args) if args else _tracked_markdown(root)
    except RepositoryScanError:
        print("repository scan failed", file=sys.stderr)
        return 2
    try:
        findings = find_broken_links(root, files)
    except RepositoryScanError as exc:
        print(exc.safe_message, file=sys.stderr)
        return 2
    authority_errors = [] if args else find_authority_errors(root)
    for path, target in findings:
        print(f"{path.as_posix()}: {target}")
    for error in authority_errors:
        print(error)
    return 1 if findings or authority_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

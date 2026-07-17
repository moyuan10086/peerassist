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


class RepositoryScanError(Exception):
    """Raised when the requested repository file set cannot be scanned safely."""


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


def _safe_sources(root: Path, files: Iterable[Path]) -> list[Path]:
    sources: set[Path] = set()
    for item in files:
        try:
            source = _lexical_path(root, Path(item))
            if source is None or stat.S_ISLNK(source.lstat().st_mode):
                continue
        except OSError:
            continue
        sources.add(source)
    return sorted(sources, key=lambda path: path.as_posix())


def _explicit_files(root: Path, args: list[str]) -> list[Path]:
    files: list[Path] = []
    for item in args:
        path = _lexical_path(root, Path(item))
        try:
            if path is None:
                raise RepositoryScanError
            path.lstat()
        except OSError as exc:
            raise RepositoryScanError from exc
        files.append(path)
    return files


def find_broken_links(root: Path, files: Iterable[Path]) -> list[tuple[Path, str]]:
    """Return stable ``(source, target)`` pairs for broken local Markdown links."""
    root = root.resolve()
    broken: set[tuple[str, str]] = set()
    for source in _safe_sources(root, files):
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
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


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path.cwd().resolve()
    try:
        files = _explicit_files(root, args) if args else _tracked_markdown(root)
    except RepositoryScanError:
        print("repository scan failed", file=sys.stderr)
        return 2
    findings = find_broken_links(root, files)
    for path, target in findings:
        print(f"{path.as_posix()}: {target}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())

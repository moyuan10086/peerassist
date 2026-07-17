#!/usr/bin/env python3
"""Check repository Markdown files for broken local links."""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import unquote, urlsplit

_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\((?P<target>[^)]+)\)")
_IGNORED_SCHEMES = {"http", "https", "mailto"}


def _tracked_markdown(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.md", "*.markdown"],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        return []
    return [root / item.decode("utf-8", errors="surrogateescape") for item in result.stdout.split(b"\0") if item]


def _markdown_targets(source: str) -> Iterable[str]:
    fence: str | None = None
    for line in source.splitlines():
        match = _FENCE_RE.match(line)
        if match:
            marker = match.group(1)
            if fence is None:
                fence = marker[0]
            elif marker[0] == fence:
                fence = None
            continue
        if fence is not None:
            continue
        for link in _LINK_RE.finditer(line):
            target = link.group("target").strip()
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1].strip()
            if target:
                yield target


def _relative_path(root: Path, path: Path) -> Path:
    try:
        return path.resolve().relative_to(root.resolve())
    except ValueError:
        return Path(path.name)


def find_broken_links(root: Path, files: Iterable[Path]) -> list[tuple[Path, str]]:
    """Return stable ``(source, target)`` pairs for broken local Markdown links."""
    root = root.resolve()
    broken: set[tuple[str, str]] = set()
    for source in sorted({Path(path).resolve() for path in files}, key=lambda path: path.as_posix()):
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
            candidate = (source.parent / decoded).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                broken.add((source_rel.as_posix(), raw_target))
                continue
            if not candidate.exists():
                broken.add((source_rel.as_posix(), raw_target))
    return [(Path(source), target) for source, target in sorted(broken)]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path.cwd().resolve()
    files = [Path(item).resolve() for item in args] if args else _tracked_markdown(root)
    findings = find_broken_links(root, files)
    for path, target in findings:
        print(f"{path.as_posix()}: {target}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())

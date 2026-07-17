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


def _link_payloads(line: str) -> Iterable[str]:
    """Yield complete inline-link payloads using balanced parentheses."""
    search_from = 0
    while True:
        opener = line.find("](", search_from)
        if opener < 0:
            return
        label_start = line.rfind("[", 0, opener)
        search_from = opener + 2
        if label_start < 0 or (label_start > 0 and line[label_start - 1] == "!"):
            continue

        depth = 1
        quote: str | None = None
        angle = False
        escaped = False
        for index in range(search_from, len(line)):
            char = line[index]
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if angle:
                angle = char != ">"
                continue
            if quote:
                if char == quote:
                    quote = None
                continue
            if char == "<":
                angle = True
            elif char in {'"', "'"}:
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    yield line[search_from:index]
                    search_from = index + 1
                    break
        else:
            return


def _valid_title(value: str) -> bool:
    if len(value) < 2:
        return False
    opening, closing = value[0], value[-1]
    if (opening, closing) not in {('"', '"'), ("'", "'"), ("(", ")")}:
        return False
    escaped = False
    depth = 0
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if opening == "(" and char == "(":
            depth += 1
        elif opening == "(" and char == ")":
            depth -= 1
            if depth == 0 and index != len(value) - 1:
                return False
        elif opening != "(" and char == opening and index not in {0, len(value) - 1}:
            return False
    return not escaped and (opening != "(" or depth == 0)


def _strip_title(payload: str) -> str | None:
    value = payload.strip()
    if not value:
        return None
    if value.startswith("<"):
        escaped = False
        for index, char in enumerate(value[1:], start=1):
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == ">":
                remainder = value[index + 1 :].strip()
                if remainder and not _valid_title(remainder):
                    return None
                return value[1:index].replace("\\", "")
        return None

    for index, char in enumerate(value):
        if char.isspace():
            remainder = value[index:].strip()
            if _valid_title(remainder):
                return value[:index].replace("\\", "")
    return value.replace("\\", "")


def _markdown_targets(source: str) -> Iterable[str]:
    fence: tuple[str, int] | None = None
    for line in source.splitlines():
        match = _FENCE_RE.match(line)
        if fence is None and match:
            marker = match.group(1)
            fence = (marker[0], len(marker))
            continue
        if fence is not None:
            if match:
                marker = match.group(1)
                remainder = line[match.end() :]
                if marker[0] == fence[0] and len(marker) >= fence[1] and not remainder.strip():
                    fence = None
            continue
        for payload in _link_payloads(line):
            target = _strip_title(payload)
            if target:
                yield target


def _relative_path(root: Path, path: Path) -> Path:
    try:
        return path.resolve().relative_to(root.resolve())
    except ValueError:
        return Path(path.name)


def _safe_sources(root: Path, files: Iterable[Path]) -> list[Path]:
    sources: set[Path] = set()
    for item in files:
        try:
            source = Path(item).resolve()
            source.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            continue
        sources.add(source)
    return sorted(sources, key=lambda path: path.as_posix())


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
    files = [root / item for item in args] if args else _tracked_markdown(root)
    findings = find_broken_links(root, files)
    for path, target in findings:
        print(f"{path.as_posix()}: {target}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Scan repository text files for common committed-secret patterns."""

from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

MAX_FILE_SIZE = 2 * 1024 * 1024
_LFS_HEADER = b"version https://git-lfs.github.com/spec/v1\n"
_DEFAULT_EXCLUDED_PARTS = ("tests", "fixtures")
_DIST_PARTS = ("web", "peerassist-workspace", "dist")
_POLICY_IMPLEMENTATION_PATHS = {
    Path("scripts/check_secrets.py"),
    Path("tests/repository/test_check_secrets.py"),
}
_RULES = (
    ("github-token", re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("openai-api-key", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
    ("private-key", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----")),
    ("aws-access-key", re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}")),
)
_PASSWORD_RE = re.compile(
    r"(?i)\b(?:[A-Za-z0-9_]*password|password[A-Za-z0-9_-]*)\b\s*[:=]\s*(?P<value>[^\s#]+)"
)
_PLACEHOLDERS = {"", "example", "changeme", "change-me", "password", "secret", "test"}
_ENV_REFERENCE_RE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$")


class RepositoryScanError(Exception):
    """Raised when the requested repository file set cannot be scanned safely."""


@dataclass(frozen=True, order=True)
class Finding:
    path: Path
    line: int
    rule: str

    def render(self) -> str:
        path = self.path.as_posix()
        for _, pattern in _RULES:
            path = pattern.sub("<redacted>", path)
        path = _PASSWORD_RE.sub(lambda match: f"{match.group(0)[: match.start('value') - match.start()]}<redacted>", path)
        return f"{path}:{self.line}: {self.rule}"


def _default_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RepositoryScanError
    paths = [root / item.decode("utf-8", errors="surrogateescape") for item in result.stdout.split(b"\0") if item]
    return [path for path in paths if _relative(root, path) not in _POLICY_IMPLEMENTATION_PATHS]


def _relative(root: Path, path: Path) -> Path | None:
    candidate = Path(os.path.abspath(path if path.is_absolute() else root / path))
    try:
        return candidate.relative_to(root)
    except ValueError:
        return None


def _safe_item(root: Path, item: Path) -> tuple[Path, os.stat_result] | None:
    path = Path(os.path.abspath(item if item.is_absolute() else root / item))
    try:
        path.relative_to(root)
        root_real = root.resolve(strict=True)
        path.parent.resolve(strict=True).relative_to(root_real)
        metadata = path.lstat()
        if not stat.S_ISLNK(metadata.st_mode):
            path.resolve(strict=True).relative_to(root_real)
    except (OSError, RuntimeError, ValueError):
        return None
    return path, metadata


def _contains_parts(parts: tuple[str, ...], subsequence: tuple[str, ...]) -> bool:
    length = len(subsequence)
    return any(parts[index : index + length] == subsequence for index in range(len(parts) - length + 1))


def _excluded(relative: Path) -> bool:
    parts = relative.parts
    return (
        ".git" in parts
        or _contains_parts(parts, _DEFAULT_EXCLUDED_PARTS)
        or _contains_parts(parts, _DIST_PARTS)
    )


def _placeholder(value: str) -> bool:
    normalized = value.strip().strip("\"'").strip().lower()
    return (
        normalized in _PLACEHOLDERS
        or (normalized.startswith("<") and normalized.endswith(">"))
        or bool(_ENV_REFERENCE_RE.fullmatch(normalized))
    )


def _safe_files(root: Path, files: Iterable[Path]) -> list[Path]:
    paths: set[Path] = set()
    for item in files:
        safe = _safe_item(root, Path(item))
        if safe is None:
            continue
        path, _ = safe
        paths.add(path)
    return sorted(paths, key=lambda item: item.as_posix())


def scan_files(root: Path, files: Iterable[Path]) -> list[Finding]:
    """Return stable, deduplicated findings without retaining matched values."""
    root = root.resolve()
    findings: set[Finding] = set()
    for path in _safe_files(root, files):
        relative = _relative(root, path)
        if relative is None or _excluded(relative):
            continue
        try:
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                content = os.readlink(path).encode("utf-8", errors="surrogateescape")
            else:
                if metadata.st_size > MAX_FILE_SIZE:
                    continue
                content = path.read_bytes()
        except OSError:
            continue
        if b"\0" in content or content.startswith(_LFS_HEADER):
            continue
        try:
            text = content.decode("utf-8")
        except UnicodeError:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for rule, pattern in _RULES:
                if pattern.search(line):
                    findings.add(Finding(relative, line_number, rule))
            password = _PASSWORD_RE.search(line)
            if password and not _placeholder(password.group("value")):
                findings.add(Finding(relative, line_number, "password-assignment"))
    return sorted(findings)


def _explicit_files(root: Path, args: list[str]) -> list[Path]:
    files: list[Path] = []
    for item in args:
        safe = _safe_item(root, Path(item))
        if safe is None:
            raise RepositoryScanError
        path, _ = safe
        files.append(path)
    return files


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path.cwd().resolve()
    try:
        files = _explicit_files(root, args) if args else _default_files(root)
    except RepositoryScanError:
        print("repository scan failed", file=sys.stderr)
        return 2
    findings = scan_files(root, files)
    for finding in findings:
        print(finding.render())
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any


def _mappings(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _mappings(child)


def _is_low_aa_finding(finding: dict[str, Any]) -> bool:
    text = " ".join(str(value) for value in finding.values()).lower()
    identifies_contrast = "wcag-contrast" in text or ("wcag" in text and "contrast" in text)
    identifies_failure = "fail" in text or "below" in text or "requires 4.5" in text
    return identifies_contrast and "aa" in text and identifies_failure


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def check(design_path: Path, tokens_path: Path) -> list[str]:
    problems: list[str] = []
    if not design_path.is_file():
        return [f"DESIGN.md not found: {design_path}"]
    if not tokens_path.is_file():
        return [f"DTCG export not found: {tokens_path}"]

    try:
        lint = _run(
            ["npx", "-y", "@google/design.md", "lint", "--format", "json", str(design_path)]
        )
    except OSError as exc:
        return [f"unable to run official DESIGN.md CLI: {exc}"]
    if lint.returncode != 0:
        detail = lint.stderr.strip() or lint.stdout.strip() or "official lint failed"
        problems.append(detail)

    try:
        lint_result = json.loads(lint.stdout)
    except json.JSONDecodeError:
        if lint.returncode == 0:
            problems.append("official lint did not return valid JSON")
    else:
        low_aa = [finding for finding in _mappings(lint_result) if _is_low_aa_finding(finding)]
        if low_aa:
            messages = [str(finding.get("message", "low contrast")) for finding in low_aa]
            problems.append("WCAG AA contrast is required: " + "; ".join(messages))

    try:
        exported = _run(
            ["npx", "-y", "@google/design.md", "export", "--format", "dtcg", str(design_path)]
        )
    except OSError as exc:
        problems.append(f"unable to run official DESIGN.md CLI: {exc}")
        return problems
    if exported.returncode != 0:
        problems.append(exported.stderr.strip() or exported.stdout.strip() or "DTCG export failed")
        return problems

    try:
        generated_tokens = json.loads(exported.stdout)
        committed_tokens = json.loads(tokens_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        problems.append(f"invalid DTCG JSON: {exc}")
    else:
        if generated_tokens != committed_tokens:
            problems.append(
                f"DTCG export is stale; regenerate {tokens_path} from {design_path}"
            )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate PeerAssist DESIGN.md and its DTCG export")
    parser.add_argument("design", nargs="?", type=Path, default=Path("DESIGN.md"))
    parser.add_argument(
        "tokens",
        nargs="?",
        type=Path,
        default=Path("web/peerassist-workspace/design-tokens.json"),
    )
    args = parser.parse_args()

    problems = check(args.design, args.tokens)
    if problems:
        for problem in problems:
            print(f"design contract: {problem}", file=sys.stderr)
        return 1
    print("design contract: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

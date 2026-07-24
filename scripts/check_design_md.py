from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CLI = ROOT / "web" / "peerassist-workspace" / "node_modules" / ".bin" / "designmd"
CLI_TIMEOUT_SECONDS = 60


def _is_low_aa_finding(finding: dict[str, object]) -> bool:
    path = finding.get("path")
    message = finding.get("message")
    if not isinstance(path, str) or not path.startswith("components."):
        return False
    if not isinstance(message, str):
        return False
    normalized = message.lower()
    return (
        "contrast" in normalized
        and "wcag" in normalized
        and "aa" in normalized
        and ("fails" in normalized or "below" in normalized)
    )


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=CLI_TIMEOUT_SECONDS,
    )


def _lint_problems(stdout: str) -> list[str]:
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError:
        return ["official lint did not return valid JSON"]
    if not isinstance(result, dict):
        return ["official lint result must be an object"]

    problems: list[str] = []
    findings = result.get("findings")
    if not isinstance(findings, list):
        problems.append("official lint top-level findings must be a list")
    else:
        for index, finding in enumerate(findings):
            if not isinstance(finding, dict):
                problems.append(f"official lint finding {index} must be an object")
                continue
            path = finding.get("path")
            message = finding.get("message")
            if path is not None and not isinstance(path, str):
                problems.append(f"official lint finding {index} path must be a string")
            if not isinstance(message, str):
                problems.append(f"official lint finding {index} message must be a string")
                continue
            if _is_low_aa_finding(finding):
                problems.append(f"WCAG AA contrast is required: {message}")

    summary = result.get("summary")
    if not isinstance(summary, dict):
        problems.append("official lint top-level summary must be an object")
    else:
        errors = summary.get("errors")
        if isinstance(errors, bool) or not isinstance(errors, int) or errors < 0:
            problems.append("official lint summary.errors must be a non-negative integer")
        elif errors > 0:
            suffix = "error" if errors == 1 else "errors"
            problems.append(f"official lint summary reports {errors} {suffix}")
    return problems


def check(design_path: Path, tokens_path: Path) -> list[str]:
    problems: list[str] = []
    if not design_path.is_file():
        return [f"DESIGN.md not found: {design_path}"]
    if not tokens_path.is_file():
        return [f"DTCG export not found: {tokens_path}"]

    cli = os.environ.get("PEERASSIST_DESIGN_MD_CLI", str(DEFAULT_CLI))
    try:
        lint = _run([cli, "lint", "--format", "json", str(design_path)])
    except subprocess.TimeoutExpired:
        return [f"official DESIGN.md CLI timed out after {CLI_TIMEOUT_SECONDS} seconds"]
    except OSError as exc:
        return [f"unable to run official DESIGN.md CLI: {exc}"]
    if lint.returncode != 0:
        detail = lint.stderr.strip() or f"official lint exited with status {lint.returncode}"
        problems.append(detail)
    problems.extend(_lint_problems(lint.stdout))

    try:
        exported = _run([cli, "export", "--format", "dtcg", str(design_path)])
    except subprocess.TimeoutExpired:
        problems.append(f"official DESIGN.md CLI timed out after {CLI_TIMEOUT_SECONDS} seconds")
        return problems
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

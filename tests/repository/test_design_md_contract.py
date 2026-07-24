from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts" / "check_design_md.py"
DESIGN = ROOT / "DESIGN.md"
TOKENS = ROOT / "web" / "peerassist-workspace" / "design-tokens.json"
PACKAGE = ROOT / "web" / "peerassist-workspace" / "package.json"
PACKAGE_LOCK = ROOT / "web" / "peerassist-workspace" / "package-lock.json"
UI_SPEC = ROOT / "docs" / "superpowers" / "specs" / "2026-07-20-peerassist-editorial-workspace-ui-design.md"


def _run_with_fake_cli(
    tmp_path: Path,
    lint_payload: dict[str, Any],
    *,
    export_payload: dict[str, Any] | None = None,
) -> subprocess.CompletedProcess[str]:
    fake_cli = tmp_path / "designmd"
    fake_cli.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "lint" ]; then\n'
        f"  printf '%s\\n' '{json.dumps(lint_payload)}'\n"
        "else\n"
        f"  printf '%s\\n' '{json.dumps(export_payload or {})}'\n"
        "fi\n",
        encoding="utf-8",
    )
    fake_cli.chmod(0o755)

    design = tmp_path / "DESIGN.md"
    design.write_text("---\nversion: alpha\nname: Test\ncolors:\n  ink: \"#111111\"\n---\n", encoding="utf-8")
    tokens = tmp_path / "tokens.json"
    tokens.write_text(json.dumps(export_payload or {}), encoding="utf-8")
    env = os.environ.copy()
    env["PEERASSIST_DESIGN_MD_CLI"] = str(fake_cli)
    return subprocess.run(
        [sys.executable, str(CHECKER), str(design), str(tokens)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_repository_design_contract_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER), str(DESIGN), str(TOKENS)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_exported_tokens_preserve_peerassist_visual_contract() -> None:
    exported = json.loads(TOKENS.read_text(encoding="utf-8"))

    assert exported["color"]["primary"]["$value"]["hex"] == "#0f4c4c"
    assert exported["color"]["ink"]["$value"]["hex"] == "#172121"
    assert exported["rounded"]["sm"]["$value"] == {"value": 4, "unit": "px"}
    assert exported["rounded"]["md"]["$value"] == {"value": 6, "unit": "px"}
    assert exported["rounded"]["lg"]["$value"] == {"value": 8, "unit": "px"}
    assert exported["spacing"]["minimumTouchTarget"]["$value"] == {"value": 44, "unit": "px"}
    assert exported["typography"]["body-md"]["$value"]["letterSpacing"] == {
        "value": 0,
        "unit": "em",
    }


def test_official_design_cli_is_exactly_locked_locally() -> None:
    package = json.loads(PACKAGE.read_text(encoding="utf-8"))
    package_lock = json.loads(PACKAGE_LOCK.read_text(encoding="utf-8"))

    assert package["devDependencies"]["@google/design.md"] == "0.3.0"
    assert package_lock["packages"][""]["devDependencies"]["@google/design.md"] == "0.3.0"
    assert package_lock["packages"]["node_modules/@google/design.md"]["version"] == "0.3.0"


def test_dtcg_export_boundary_is_explicit() -> None:
    design_text = DESIGN.read_text(encoding="utf-8")
    spec_text = UI_SPEC.read_text(encoding="utf-8")

    assert "DTCG export contains foundational tokens only" in design_text
    assert "组件与完整 typography 契约仍以根目录 `DESIGN.md` 为准" in spec_text


def test_checker_rejects_low_aa_warning(tmp_path: Path) -> None:
    result = _run_with_fake_cli(
        tmp_path,
        {
            "findings": [
                {
                    "severity": "warning",
                    "path": "components.button-primary",
                    "message": "Contrast ratio 2.10:1 fails WCAG AA (requires 4.5:1)",
                }
            ],
            "summary": {"errors": 0, "warnings": 1, "infos": 0},
        },
    )

    assert result.returncode == 1
    assert "WCAG AA" in result.stderr


def test_checker_rejects_reported_errors_and_invalid_findings(tmp_path: Path) -> None:
    result = _run_with_fake_cli(
        tmp_path,
        {
            "findings": ["not-a-finding"],
            "summary": {"errors": 1, "warnings": 0, "infos": 0},
        },
    )

    assert result.returncode == 1
    assert "summary reports 1 error" in result.stderr
    assert "finding 0 must be an object" in result.stderr


def test_checker_does_not_recursively_treat_metadata_as_finding(tmp_path: Path) -> None:
    result = _run_with_fake_cli(
        tmp_path,
        {
            "findings": [],
            "summary": {"errors": 0, "warnings": 0, "infos": 0},
            "metadata": {
                "path": "components.not-a-finding",
                "message": "Contrast fails WCAG AA",
            },
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_checker_reports_missing_official_cli_without_traceback(tmp_path: Path) -> None:
    design = tmp_path / "DESIGN.md"
    design.write_text("---\nversion: alpha\nname: Test\ncolors:\n  ink: \"#111111\"\n---\n", encoding="utf-8")
    tokens = tmp_path / "tokens.json"
    tokens.write_text("{}\n", encoding="utf-8")
    env = os.environ.copy()
    env["PEERASSIST_DESIGN_MD_CLI"] = str(tmp_path / "missing-designmd")

    result = subprocess.run(
        [sys.executable, str(CHECKER), str(design), str(tokens)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "unable to run official DESIGN.md CLI" in result.stderr
    assert "Traceback" not in result.stderr


def test_checker_reports_cli_timeout_safely(tmp_path: Path, monkeypatch: Any) -> None:
    spec = importlib.util.spec_from_file_location("check_design_md", CHECKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    design = tmp_path / "DESIGN.md"
    design.write_text("---\nversion: alpha\nname: Test\ncolors:\n  ink: \"#111111\"\n---\n", encoding="utf-8")
    tokens = tmp_path / "tokens.json"
    tokens.write_text("{}\n", encoding="utf-8")
    fake_cli = tmp_path / "designmd"
    fake_cli.touch(mode=0o755)
    monkeypatch.setenv("PEERASSIST_DESIGN_MD_CLI", str(fake_cli))

    def raise_timeout(*args: Any, **kwargs: Any) -> None:
        assert kwargs["timeout"] == 60
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(module.subprocess, "run", raise_timeout)
    problems = module.check(design, tokens)

    assert problems == ["official DESIGN.md CLI timed out after 60 seconds"]

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts" / "check_design_md.py"
DESIGN = ROOT / "DESIGN.md"
TOKENS = ROOT / "web" / "peerassist-workspace" / "design-tokens.json"


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


def test_checker_rejects_low_aa_warning(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_npx = fake_bin / "npx"
    fake_npx.write_text(
        "#!/bin/sh\n"
        "cat <<'JSON'\n"
        '{"valid":true,"diagnostics":[{"rule":"wcag-contrast","severity":"warning",'
        '"message":"Contrast ratio 2.10:1 fails WCAG AA (requires 4.5:1)"}]}\n'
        "JSON\n",
        encoding="utf-8",
    )
    fake_npx.chmod(0o755)

    design = tmp_path / "DESIGN.md"
    design.write_text("---\nversion: alpha\nname: Test\ncolors:\n  ink: \"#111111\"\n---\n", encoding="utf-8")
    tokens = tmp_path / "tokens.json"
    tokens.write_text("{}\n", encoding="utf-8")
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [sys.executable, str(CHECKER), str(design), str(tokens)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "WCAG AA" in result.stderr


def test_checker_reports_missing_official_cli_without_traceback(tmp_path: Path) -> None:
    design = tmp_path / "DESIGN.md"
    design.write_text("---\nversion: alpha\nname: Test\ncolors:\n  ink: \"#111111\"\n---\n", encoding="utf-8")
    tokens = tmp_path / "tokens.json"
    tokens.write_text("{}\n", encoding="utf-8")
    env = os.environ.copy()
    env["PATH"] = str(tmp_path)

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

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SYSTEMD = ROOT / "deploy" / "systemd"


def test_systemd_examples_are_portable_private_and_hardened() -> None:
    required = {
        "EnvironmentFile=/etc/peerassist/peerassist.env",
        "User=peerassist",
        "Group=peerassist",
        "UMask=0077",
        "WorkingDirectory=/opt/peerassist",
        "NoNewPrivileges=true",
        "PrivateTmp=true",
        "ProtectSystem=strict",
    }
    for name in ("peerassist-review-api.service", "peerassist-ui.service"):
        source = (SYSTEMD / name).read_text(encoding="utf-8")
        assert required <= set(source.splitlines())
        assert "0.0.0.0" not in source
        assert "/root/" not in source
        assert "${PEERASSIST_" not in source
        assert any(
            line.startswith("ExecStart=/opt/peerassist/.venv/bin/python ")
            for line in source.splitlines()
        )

    review = (SYSTEMD / "peerassist-review-api.service").read_text(encoding="utf-8")
    workspace = (SYSTEMD / "peerassist-ui.service").read_text(encoding="utf-8")
    assert "--host 127.0.0.1 --port 8767" in review
    assert "--data-dir /var/lib/peerassist/data" in review
    assert "ReadWritePaths=/var/lib/peerassist/data" in review
    assert "ReadWritePaths=/var/lib/peerassist/workspace" not in review
    assert "InaccessiblePaths=/var/lib/peerassist/workspace" in review
    assert "--host 127.0.0.1 --port 8766" in workspace
    assert "--run-dir /var/lib/peerassist/workspace/run" in workspace
    assert "--paper-id current" in workspace
    assert "ReadWritePaths=/var/lib/peerassist/workspace" in workspace
    assert "ReadWritePaths=/var/lib/peerassist/data" not in workspace
    assert "InaccessiblePaths=/var/lib/peerassist/data" in workspace


def test_systemd_environment_and_smoke_script_are_safe_examples() -> None:
    environment = (SYSTEMD / "peerassist.env.example").read_text(encoding="utf-8")
    assert environment == "PEERASSIST_REVIEW_API_URL=http://127.0.0.1:8767\n"

    smoke = SYSTEMD / "smoke_systemd.sh"
    source = smoke.read_text(encoding="utf-8")
    assert os.access(smoke, os.X_OK)
    for token in (
        "systemd",
        "peerassist-review-api.service",
        "peerassist-ui.service",
        "/run/systemd/system",
        "/opt/peerassist",
        "git -C \"${repo_root}\" ls-files -z",
        "tar -C \"${repo_root}\" --null -T - -cf -",
        "environment_file=${environment_dir}/peerassist.env",
        "/var/lib/peerassist",
        "daemon-reload",
        "systemd-analyze verify",
        "trap cleanup EXIT",
        "127.0.0.1:8766",
        "127.0.0.1:8767",
    ):
        assert token in source
    assert "systemd-run" not in source
    assert "status --porcelain=v1" in source


def test_delivered_systemd_units_pass_static_verification(tmp_path: Path) -> None:
    root = tmp_path / "root"
    unit_dir = root / "etc" / "systemd" / "system"
    executable = root / "opt" / "peerassist" / ".venv" / "bin" / "python"
    unit_dir.mkdir(parents=True)
    executable.parent.mkdir(parents=True)
    executable.touch(mode=0o755)
    for name in ("peerassist-review-api.service", "peerassist-ui.service"):
        shutil.copyfile(SYSTEMD / name, unit_dir / name)
    for name in ("sysinit.target", "basic.target", "network-online.target", "multi-user.target"):
        (unit_dir / name).write_text(f"[Unit]\nDescription={name}\n", encoding="utf-8")
    result = subprocess.run(
        [
            "systemd-analyze",
            "verify",
            f"--root={root}",
            "peerassist-review-api.service",
            "peerassist-ui.service",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

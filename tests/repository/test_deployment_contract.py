from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SYSTEMD = ROOT / "deploy" / "systemd"
PLATFORM_BACKUP = SYSTEMD / "peerassist-platform-backup.sh"
PLATFORM_RESTORE_DRILL = SYSTEMD / "peerassist-platform-restore-drill.sh"


def test_systemd_examples_are_portable_private_and_hardened() -> None:
    required = {
        "EnvironmentFile=/etc/peerassist/peerassist.env",
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
    assert "User=peerassist-api" in review
    assert "Group=peerassist-api" in review
    assert "User=peerassist-ui" in workspace
    assert "Group=peerassist-ui" in workspace
    for source in (review, workspace):
        assert "SupplementaryGroups=peerassist" in source
        assert "ProtectProc=invisible" in source
        assert "ProcSubset=pid" in source
    assert "--host 127.0.0.1 --port 8767" in review
    assert "--data-dir /var/lib/peerassist/data" in review
    assert "ReadWritePaths=/var/lib/peerassist/data" in review
    assert "ReadWritePaths=/var/lib/peerassist/workspace" not in review
    assert "InaccessiblePaths=/var/lib/peerassist/workspace" in review
    assert "Environment=DATA_DIR=/var/lib/peerassist/data" in review
    assert "--host 127.0.0.1 --port 8766" in workspace
    assert "--run-dir /var/lib/peerassist/workspace/run" in workspace
    assert "--paper-id current" in workspace
    assert "ReadWritePaths=/var/lib/peerassist/workspace" in workspace
    assert "ReadWritePaths=/var/lib/peerassist/data" not in workspace
    assert "InaccessiblePaths=/var/lib/peerassist/data" in workspace
    assert "Environment=DATA_DIR=/var/lib/peerassist/workspace/data" in workspace


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
        "peerassist-api",
        "peerassist-ui",
        "runuser -u peerassist-ui",
        "runuser -u peerassist-api",
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


def test_platform_backup_and_restore_drill_are_safe_examples() -> None:
    backup = PLATFORM_BACKUP.read_text(encoding="utf-8")
    restore = PLATFORM_RESTORE_DRILL.read_text(encoding="utf-8")

    for script in (PLATFORM_BACKUP, PLATFORM_RESTORE_DRILL):
        assert os.access(script, os.X_OK)
        subprocess.run(["sh", "-n", script], cwd=ROOT, check=True)

    for token in (
        "umask 077",
        "pg_dump -Fc",
        "compose stop keycloak minio api worker",
        "sha256sum",
        "trap cleanup",
        "--network none",
        ":/source:ro",
    ):
        assert token in backup
    assert "platform.env" in backup
    assert "chmod 0600" in backup

    for token in (
        "sha256sum -c SHA256SUMS",
        "--tmpfs /var/lib/postgresql/data",
        "pg_restore",
        "information_schema.tables",
        "trap cleanup",
        "--network none",
    ):
        assert token in restore
    assert "-p " not in restore

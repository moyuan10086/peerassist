from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_live_platform_compose_uses_current_paths_and_persistent_volumes() -> None:
    source = (ROOT / "infrastructure/compose/compose.platform.yml").read_text(
        encoding="utf-8"
    )

    assert "127.0.0.1:8000:8080" in source
    assert "!override" in source
    assert "PEERASSIST_POSTGRES_VOLUME" in source
    assert "PEERASSIST_KEYCLOAK_VOLUME" in source
    assert "PEERASSIST_MINIO_VOLUME" in source
    assert "PEERASSIST_WORKER_SCRATCH_VOLUME" in source
    assert "PEERASSIST_MODEL_SETTINGS_VOLUME" in source
    assert "/tmp/peerassist-m1" not in source
    assert ".worktrees/peerassist-m0" not in source


def test_identity_gateway_uses_a_stable_loopback_port() -> None:
    compose_source = (ROOT / "infrastructure/compose/compose.platform.yml").read_text(
        encoding="utf-8"
    )
    launcher_source = (ROOT / "deploy/systemd/peerassist-ui-start.sh").read_text(
        encoding="utf-8"
    )

    assert '"127.0.0.1:8001:8080"' in compose_source
    assert "http://127.0.0.1:8001" in launcher_source
    assert "docker inspect" not in launcher_source
    assert "com.docker.compose.service=keycloak" not in launcher_source


def test_live_platform_launcher_has_named_service_health_gates() -> None:
    source = (ROOT / "deploy/systemd/peerassist-platform-stack.sh").read_text(
        encoding="utf-8"
    )

    for token in (
        "/etc/peerassist/platform.env",
        "compose.m1.yml",
        "compose.platform.yml",
        "up --build --detach --remove-orphans",
        "http://127.0.0.1:8000/api/v1/ready",
        "ps --status running --quiet worker",
    ):
        assert token in source
    assert ".worktrees/peerassist-m0" not in source
    assert "peerassist-p0-platform-api" not in source
    assert "11e13f252e46" not in source


def test_live_platform_systemd_unit_uses_the_launcher() -> None:
    source = (ROOT / "deploy/systemd/peerassist-platform-stack.service").read_text(
        encoding="utf-8"
    )

    assert "Requires=docker.service" in source
    assert "WorkingDirectory=/root/PeerAssist" in source
    assert "ExecStart=/usr/local/sbin/peerassist-platform-stack start" in source
    assert "ExecStop=/usr/local/sbin/peerassist-platform-stack stop" in source
    assert "Before=peerassist-ui.service" not in source
    assert ".worktrees/peerassist-m0" not in source
    assert "/tmp/peerassist-m1" not in source


def test_live_ui_starts_in_parallel_to_break_oidc_readiness_cycle() -> None:
    source = (ROOT / "deploy/systemd/peerassist-ui-platform.conf").read_text(
        encoding="utf-8"
    )

    assert "After=" in source
    assert "After=network-online.target peerassist-review-api.service" in source
    assert "Wants=peerassist-platform-stack.service" in source
    assert "After=peerassist-platform-stack.service" not in source


def test_platform_installer_uses_only_git_managed_systemd_sources() -> None:
    source = (ROOT / "deploy/systemd/install-platform-stack.sh").read_text(
        encoding="utf-8"
    )

    for token in (
        "peerassist-platform-stack.sh",
        "peerassist-platform-stack.service",
        "peerassist-ui-platform.conf",
        "peerassist-ui-start.sh",
        "/usr/local/sbin/peerassist-platform-stack",
        "/usr/local/sbin/peerassist-ui-start",
        "/etc/systemd/system",
        "systemctl daemon-reload",
        "systemctl enable peerassist-platform-stack.service",
    ):
        assert token in source
    assert ".worktrees/peerassist-m0" not in source
    assert "/tmp/peerassist-m1" not in source

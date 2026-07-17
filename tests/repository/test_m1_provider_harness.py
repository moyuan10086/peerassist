from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = ROOT / "infrastructure/compose/compose.m1.test.yml"
ENV_HELPER = ROOT / "scripts/m1_test_env.sh"
REALM_TEMPLATE = ROOT / "infrastructure/keycloak/realm-template.json"


def _run_helper(
    *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(ENV_HELPER), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def _read_exports(path: Path) -> dict[str, str]:
    exports: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("export "):
            key, value = line.removeprefix("export ").split("=", 1)
            exports[key] = value
    return exports


def test_profile_pins_private_real_providers_with_health_checks() -> None:
    profile = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    services = profile["services"]

    assert {"postgres", "keycloak", "minio", "minio-bootstrap"} <= set(services)
    assert services["postgres"]["image"].startswith("postgres:16.")
    for name in ("postgres", "keycloak", "minio", "minio-bootstrap"):
        service = services[name]
        assert not service["image"].endswith(":latest")
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["cap_drop"] == ["ALL"]
        assert service.get("ports", []) == [] or all(
            str(port).startswith("127.0.0.1:") for port in service["ports"]
        )
    for name in ("postgres", "keycloak", "minio"):
        assert "healthcheck" in services[name]
    keycloak_probe = services["keycloak"]["healthcheck"]["test"][-1]
    assert "Host: 127.0.0.1" in keycloak_probe


def test_profile_uses_generated_values_and_bootstraps_provider_data() -> None:
    compose_text = COMPOSE_PATH.read_text(encoding="utf-8")
    realm_text = REALM_TEMPLATE.read_text(encoding="utf-8")

    required_refs = {
        "M1_TEST_POSTGRES_PASSWORD",
        "M1_TEST_KEYCLOAK_ADMIN_PASSWORD",
        "M1_TEST_OIDC_AUTOMATION_CLIENT_SECRET",
        "M1_TEST_MINIO_ROOT_PASSWORD",
        "M1_TEST_S3_BUCKET",
    }
    assert all(f"${{{name}}}" in compose_text for name in required_refs)
    assert "minio/bootstrap.sh" in compose_text
    assert "keycloak/bootstrap.sh" in compose_text
    assert "password" not in compose_text.lower() or "${M1_TEST_" in compose_text
    assert "publicClient" in realm_text
    assert "pkce.code.challenge.method" in realm_text
    assert "serviceAccountsEnabled" in realm_text
    assert "${M1_TEST_OIDC_AUTOMATION_CLIENT_SECRET}" in realm_text
    assert '"redirectUris": ["${M1_TEST_OIDC_REDIRECT_URI}"]' in realm_text
    assert '"webOrigins": ["${M1_TEST_PUBLIC_ORIGIN}"]' in realm_text
    assert '"http://127.0.0.1/*"' not in realm_text
    assert "minioadmin" not in (compose_text + realm_text).lower()


def test_non_root_bootstraps_can_read_inputs_and_write_only_to_tmpfs() -> None:
    profile = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))

    assert stat.S_IMODE(REALM_TEMPLATE.stat().st_mode) & 0o444 == 0o444
    assert profile["services"]["minio-bootstrap"]["environment"]["HOME"] == "/home/mc"


def test_create_writes_a_source_safe_mode_0600_environment(tmp_path: Path) -> None:
    env_file = tmp_path / "provider.env"

    result = _run_helper("create", str(env_file))

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    exports = _read_exports(env_file)
    required = {
        "M1_TEST_PROJECT_NAME",
        "M1_TEST_DATABASE_URL",
        "M1_TEST_OIDC_ISSUER",
        "M1_TEST_PUBLIC_ORIGIN",
        "M1_TEST_OIDC_REDIRECT_URI",
        "M1_TEST_S3_ENDPOINT",
        "M1_TEST_POSTGRES_USER",
        "M1_TEST_POSTGRES_PASSWORD",
        "M1_TEST_POSTGRES_DB",
        "M1_TEST_OIDC_REALM",
        "M1_TEST_OIDC_AUTOMATION_CLIENT_ID",
        "M1_TEST_OIDC_AUTOMATION_CLIENT_SECRET",
        "M1_TEST_OIDC_PKCE_CLIENT_ID",
        "M1_TEST_OIDC_USERNAME",
        "M1_TEST_OIDC_USER_PASSWORD",
        "M1_TEST_KEYCLOAK_ADMIN",
        "M1_TEST_KEYCLOAK_ADMIN_PASSWORD",
        "M1_TEST_S3_ACCESS_KEY",
        "M1_TEST_S3_SECRET_KEY",
        "M1_TEST_S3_BUCKET",
        "M1_TEST_S3_REGION",
        "M1_TEST_POSTGRES_PORT",
        "M1_TEST_KEYCLOAK_PORT",
        "M1_TEST_MINIO_PORT",
        "M1_TEST_API_CALLBACK_PORT",
    }
    assert required <= exports.keys()
    assert re.fullmatch(r"peerassist-m1-[0-9a-f]{24}", exports["M1_TEST_PROJECT_NAME"])
    assert exports["M1_TEST_DATABASE_URL"].startswith("postgresql://")
    assert exports["M1_TEST_OIDC_ISSUER"].startswith("http://127.0.0.1:")
    assert exports["M1_TEST_PUBLIC_ORIGIN"] == (
        f"http://127.0.0.1:{exports['M1_TEST_API_CALLBACK_PORT']}"
    )
    assert exports["M1_TEST_OIDC_REDIRECT_URI"] == (
        f"{exports['M1_TEST_PUBLIC_ORIGIN']}/api/v1/auth/callback"
    )
    assert exports["M1_TEST_S3_ENDPOINT"].startswith("http://127.0.0.1:")
    assert all(re.fullmatch(r"[A-Za-z0-9_./:@-]+", value) for value in exports.values())

    sourced = subprocess.run(
        [
            "bash",
            "-c",
            'set -eu; source "$1"; test -n "$M1_TEST_DATABASE_URL"; '
            'test -n "$M1_TEST_OIDC_ISSUER"; test -n "$M1_TEST_S3_ENDPOINT"',
            "bash",
            str(env_file),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert sourced.returncode == 0, sourced.stderr


def test_create_is_atomic_and_refuses_existing_or_symlink_targets(tmp_path: Path) -> None:
    env_file = tmp_path / "provider.env"
    env_file.write_text("do-not-overwrite\n", encoding="utf-8")

    existing = _run_helper("create", str(env_file))

    assert existing.returncode != 0
    assert env_file.read_text(encoding="utf-8") == "do-not-overwrite\n"
    target = tmp_path / "target.env"
    target.write_text("do-not-overwrite\n", encoding="utf-8")
    symlink = tmp_path / "link.env"
    symlink.symlink_to(target)
    linked = _run_helper("create", str(symlink))
    assert linked.returncode != 0
    assert target.read_text(encoding="utf-8") == "do-not-overwrite\n"
    assert list(tmp_path.glob(".m1-test-env.*")) == []


@pytest.mark.parametrize("mode", [0o644, 0o400, 0o660])
def test_commands_reject_environment_files_that_are_not_mode_0600(
    tmp_path: Path, mode: int
) -> None:
    env_file = tmp_path / "provider.env"
    assert _run_helper("create", str(env_file)).returncode == 0
    env_file.chmod(mode)

    result = _run_helper("wait", str(env_file), "postgres")

    assert result.returncode != 0
    assert "0600" in result.stderr


def test_commands_reject_tampered_project_names_before_calling_docker(tmp_path: Path) -> None:
    env_file = tmp_path / "provider.env"
    assert _run_helper("create", str(env_file)).returncode == 0
    text = env_file.read_text(encoding="utf-8")
    env_file.write_text(
        re.sub(
            r"^export M1_TEST_PROJECT_NAME=.*$",
            "export M1_TEST_PROJECT_NAME=unrelated-production",
            text,
            flags=re.MULTILINE,
        ),
        encoding="utf-8",
    )
    env_file.chmod(0o600)

    result = _run_helper("down", str(env_file))

    assert result.returncode != 0
    assert env_file.exists()
    assert "unsafe" in result.stderr.lower()


def test_down_is_idempotent_when_environment_is_absent(tmp_path: Path) -> None:
    env_file = tmp_path / "missing.env"

    first = _run_helper("down", str(env_file))
    second = _run_helper("down", str(env_file))

    assert first.returncode == 0
    assert second.returncode == 0


def test_wait_fails_fast_when_a_provider_exits(tmp_path: Path) -> None:
    env_file = tmp_path / "provider.env"
    assert _run_helper("create", str(env_file)).returncode == 0
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "case \" $* \" in\n"
        "  *' ps --all --quiet '*) printf 'provider-id\\n' ;;\n"
        "  *' inspect '*) printf 'exited  1\\n' ;;\n"
        "  *) exit 2 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    env = os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}"}

    result = subprocess.run(
        [str(ENV_HELPER), "wait", str(env_file), "postgres"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=5,
    )

    assert result.returncode != 0
    assert "failed before readiness" in result.stderr


def test_wait_queries_stopped_provider_containers() -> None:
    helper = ENV_HELPER.read_text(encoding="utf-8")

    assert 'ps --all --quiet "$service"' in helper


@pytest.mark.skipif(
    shutil.which("docker") is None,
    reason="Docker CLI is unavailable",
)
def test_real_provider_harness_smoke_and_cleanup(tmp_path: Path) -> None:
    info = subprocess.run(
        ["docker", "info"], check=False, capture_output=True, text=True, timeout=30
    )
    if info.returncode != 0:
        pytest.skip("Docker daemon is unavailable")

    env_file = tmp_path / "provider.env"
    assert _run_helper("create", str(env_file)).returncode == 0
    project = _read_exports(env_file)["M1_TEST_PROJECT_NAME"]
    try:
        up = _run_helper("up", str(env_file))
        assert up.returncode == 0, up.stderr
        ready = _run_helper("wait", str(env_file))
        assert ready.returncode == 0, ready.stderr
        exports = _read_exports(env_file)
        authorize_url = f"{exports['M1_TEST_OIDC_ISSUER']}/protocol/openid-connect/auth"
        common = {
            "client_id": exports["M1_TEST_OIDC_PKCE_CLIENT_ID"],
            "response_type": "code",
            "scope": "openid",
            "state": "m1-smoke-state",
            "nonce": "m1-smoke-nonce",
            "code_challenge": "A" * 43,
            "code_challenge_method": "S256",
        }
        valid_response = urllib.request.urlopen(
            f"{authorize_url}?{urllib.parse.urlencode(common | {'redirect_uri': exports['M1_TEST_OIDC_REDIRECT_URI']})}",
            timeout=10,
        )
        valid_body = valid_response.read().decode("utf-8", errors="replace").lower()
        assert valid_response.status == 200
        assert "invalid_redirect_uri" not in valid_body
        assert "invalid parameter: redirect_uri" not in valid_body
        assert "login" in valid_body or "authenticate" in valid_body

        invalid_redirect = (
            f"http://127.0.0.1:{int(exports['M1_TEST_API_CALLBACK_PORT']) + 1}"
            "/api/v1/auth/callback"
        )
        with pytest.raises(urllib.error.HTTPError) as rejected:
            urllib.request.urlopen(
                f"{authorize_url}?{urllib.parse.urlencode(common | {'redirect_uri': invalid_redirect})}",
                timeout=10,
            )
        assert rejected.value.code == 400
        rejected_body = rejected.value.read().decode("utf-8", errors="replace").lower()
        assert "redirect_uri" in rejected_body
    finally:
        down = _run_helper("down", str(env_file))
        assert down.returncode == 0, down.stderr
    assert not env_file.exists()
    remaining = subprocess.run(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={project}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert remaining.stdout.strip() == ""

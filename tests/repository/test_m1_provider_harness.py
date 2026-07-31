from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
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
KEYCLOAK_BOOTSTRAP = ROOT / "infrastructure/keycloak/bootstrap.sh"
IMPLEMENTATION_PLAN = ROOT / "docs/superpowers/plans/2026-07-17-peerassist-m1-platform-authorization.md"


def test_keycloak_bootstrap_accepts_https_origin_without_explicit_port() -> None:
    bootstrap = KEYCLOAK_BOOTSTRAP.read_text(encoding="utf-8")
    assert r"^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?$" in bootstrap


def test_public_identity_hostname_does_not_enable_http_backchannel() -> None:
    bootstrap = KEYCLOAK_BOOTSTRAP.read_text(encoding="utf-8")
    assert "--hostname-backchannel-dynamic=true" not in bootstrap
    assert "--proxy-headers=xforwarded" in bootstrap
    assert "--proxy-trusted-addresses=172.16.0.0/12" in bootstrap


def _run_helper(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
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


def _write_fake_docker(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "project= env_file=\n"
        "previous=\n"
        'for argument in "$@"; do\n'
        '  case "$previous" in\n'
        "    --project-name) project=$argument ;;\n"
        "    --env-file) env_file=$argument ;;\n"
        "  esac\n"
        "  previous=$argument\n"
        "done\n"
        'case " $* " in\n'
        "  *' compose '*' down '*)\n"
        '    if [ "${FAKE_DOWN_FAIL_ONCE:-0}" = 1 ] && '
        '[ ! -e "$FAKE_STATE/down-failed" ]; then\n'
        '      : > "$FAKE_STATE/down-failed"\n'
        "      exit 7\n"
        "    fi\n"
        "    exit 0\n"
        "    ;;\n"
        "  *' compose '*' port '*)\n"
        "    service=\n"
        "    previous=\n"
        '    for argument in "$@"; do\n'
        '      if [ "$previous" = port ]; then service=$argument; break; fi\n'
        "      previous=$argument\n"
        "    done\n"
        "    base=$((20000 + $(printf '%s' \"$project\" | cksum | cut -d' ' -f1) % 20000))\n"
        '    case "$service" in\n'
        "      postgres) offset=1 ;; keycloak) offset=2 ;; minio) offset=3 ;;\n"
        "      callback-reservation) offset=4 ;; *) exit 9 ;;\n"
        "    esac\n"
        "    printf '127.0.0.1:%s\\n' $((base + offset))\n"
        "    exit 0\n"
        "    ;;\n"
        "  *' compose '*' up '*' keycloak'*)\n"
        "    grep -Eq '^export M1_TEST_API_CALLBACK_PORT=[1-9][0-9]{3,4}$' \"$env_file\"\n"
        "    file_origin=$(sed -n 's/^export M1_TEST_PUBLIC_ORIGIN=//p' \"$env_file\")\n"
        '    [ "${M1_TEST_PUBLIC_ORIGIN:-}" = "$file_origin" ]\n'
        "    exit 0\n"
        "    ;;\n"
        "  *' compose '*' up '*) exit 0 ;;\n"
        "  *' ps -aq --filter '*) kind=container ;;\n"
        "  *' volume ls -q --filter '*) kind=volume ;;\n"
        "  *' network ls -q --filter '*) kind=network ;;\n"
        "  *) exit 10 ;;\n"
        "esac\n"
        'if [ "${FAKE_QUERY_FAILURE:-}" = "$kind" ]; then exit 8; fi\n'
        'if [ "${FAKE_RESIDUAL:-}" = "$kind" ]; then printf \'%s-id\\n\' "$kind"; fi\n',
        encoding="utf-8",
    )
    docker.chmod(0o755)
    state = tmp_path / "fake-state"
    state.mkdir()
    return os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_STATE": str(state),
    }


def test_profile_pins_private_real_providers_with_health_checks() -> None:
    profile = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    services = profile["services"]

    assert {
        "postgres",
        "keycloak",
        "minio",
        "minio-bootstrap",
        "callback-reservation",
    } <= set(services)
    assert services["postgres"]["image"].startswith("postgres:16.")
    for name in (
        "postgres",
        "keycloak",
        "minio",
        "minio-bootstrap",
        "callback-reservation",
    ):
        service = services[name]
        assert not service["image"].endswith(":latest")
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["cap_drop"] == ["ALL"]
        assert service.get("ports", []) == [] or all(
            str(port).startswith("127.0.0.1:") for port in service["ports"]
        )
    assert services["postgres"]["ports"] == ["127.0.0.1::5432"]
    assert services["keycloak"]["ports"] == ["127.0.0.1::8080"]
    assert services["minio"]["ports"] == ["127.0.0.1::9000"]
    assert services["callback-reservation"]["ports"] == ["127.0.0.1::8080"]
    for name in ("postgres", "keycloak", "minio"):
        assert "healthcheck" in services[name]
    keycloak_probe = services["keycloak"]["healthcheck"]["test"][-1]
    assert "Host: 127.0.0.1" in keycloak_probe


def test_callback_reservation_lifecycle_matches_session_and_compose_tasks() -> None:
    compose_text = COMPOSE_PATH.read_text(encoding="utf-8")

    assert "Task 10" in compose_text
    assert "TestClient" in compose_text
    assert "Task 16" in compose_text
    assert "API service replaces" in compose_text
    assert "Task 9 replaces" not in compose_text


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

    realm = json.loads(realm_text)
    assert realm["registrationAllowed"] is True
    for user in realm["users"]:
        assert user["email"]
        assert user["emailVerified"] is True
        assert user["firstName"]
        assert user["lastName"]


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
        "M1_TEST_ENDPOINTS_READY",
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
    assert exports["M1_TEST_ENDPOINTS_READY"] == "0"
    assert re.fullmatch(r"peerassist-m1-[0-9a-f]{24}", exports["M1_TEST_PROJECT_NAME"])
    assert exports["M1_TEST_DATABASE_URL"].startswith("postgresql://")
    assert exports["M1_TEST_OIDC_ISSUER"].startswith("http://127.0.0.1:")
    assert exports["M1_TEST_PUBLIC_ORIGIN"] == (f"http://127.0.0.1:{exports['M1_TEST_API_CALLBACK_PORT']}")
    assert exports["M1_TEST_OIDC_REDIRECT_URI"] == (
        f"{exports['M1_TEST_PUBLIC_ORIGIN']}/api/v1/auth/callback"
    )
    assert exports["M1_TEST_S3_ENDPOINT"].startswith("http://127.0.0.1:")
    assert exports["M1_TEST_DATABASE_URL"].endswith(":0/peerassist")
    assert exports["M1_TEST_OIDC_ISSUER"].startswith("http://127.0.0.1:0/")
    assert exports["M1_TEST_S3_ENDPOINT"] == "http://127.0.0.1:0"
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


def test_create_atomically_replaces_a_safe_mktemp_target(tmp_path: Path) -> None:
    created = subprocess.run(
        ["mktemp", str(tmp_path / "provider.XXXXXXXX")],
        check=True,
        capture_output=True,
        text=True,
    )
    env_file = Path(created.stdout.strip())
    original_inode = env_file.stat().st_ino

    result = _run_helper("create", str(env_file))

    assert result.returncode == 0, result.stderr
    assert env_file.stat().st_ino != original_inode
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert env_file.stat().st_nlink == 1
    assert _read_exports(env_file)["M1_TEST_ENDPOINTS_READY"] == "0"


def test_create_accepts_a_safe_mktemp_target_in_the_sticky_system_tmp() -> None:
    descriptor, raw_path = tempfile.mkstemp(prefix="peerassist-m1-", dir="/tmp")
    os.close(descriptor)
    env_file = Path(raw_path)
    try:
        original_inode = env_file.stat().st_ino

        result = _run_helper("create", str(env_file))

        assert result.returncode == 0, result.stderr
        assert env_file.stat().st_ino != original_inode
        assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    finally:
        env_file.unlink(missing_ok=True)


def test_create_rejects_a_target_in_an_unsafe_shared_directory(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o777)
    shared.chmod(0o777)
    env_file = shared / "provider.env"

    result = _run_helper("create", str(env_file))

    assert result.returncode != 0
    assert not env_file.exists()
    assert list(shared.glob(".m1-test-env.*")) == []


def test_create_rejects_a_parent_reached_through_a_symlinked_ancestor(
    tmp_path: Path,
) -> None:
    physical_parent = tmp_path / "physical" / "environment"
    physical_parent.mkdir(parents=True)
    ambiguous_ancestor = tmp_path / "alias"
    ambiguous_ancestor.symlink_to(physical_parent.parent, target_is_directory=True)
    env_file = ambiguous_ancestor / physical_parent.name / "provider.env"

    result = _run_helper("create", str(env_file))

    assert result.returncode != 0
    assert not (physical_parent / env_file.name).exists()
    assert list(physical_parent.glob(".m1-test-env.*")) == []


def test_create_detects_replacement_of_the_validated_empty_target(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "provider.env"
    env_file.touch(mode=0o600)
    original_inode = env_file.stat().st_ino
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_python = bin_dir / "python3"
    fake_python.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'if [ ! -e "$M1_REPLACED_MARKER" ]; then\n'
        '  replacement="$M1_REPLACEMENT_TARGET.replacement"\n'
        '  : > "$replacement"\n'
        '  chmod 0600 "$replacement"\n'
        '  rm -f -- "$M1_REPLACEMENT_TARGET"\n'
        '  mv -- "$replacement" "$M1_REPLACEMENT_TARGET"\n'
        '  : > "$M1_REPLACED_MARKER"\n'
        "fi\n"
        'exec /usr/bin/python3 "$@"\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    marker = tmp_path / "replaced"
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "M1_REPLACEMENT_TARGET": str(env_file),
        "M1_REPLACED_MARKER": str(marker),
    }

    result = _run_helper("create", str(env_file), env=env)

    assert result.returncode != 0
    assert marker.exists()
    assert env_file.stat().st_ino != original_inode
    assert env_file.stat().st_size == 0
    assert list(tmp_path.glob(".m1-test-env.*")) == []


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


@pytest.mark.parametrize("unsafe", ["mode", "hardlink"])
def test_create_does_not_replace_unsafe_empty_targets(tmp_path: Path, unsafe: str) -> None:
    env_file = tmp_path / "provider.env"
    env_file.touch(mode=0o600)
    linked: Path | None = None
    if unsafe == "mode":
        env_file.chmod(0o644)
    else:
        linked = tmp_path / "provider.link"
        os.link(env_file, linked)
    original_inode = env_file.stat().st_ino

    result = _run_helper("create", str(env_file))

    assert result.returncode != 0
    assert env_file.stat().st_ino == original_inode
    assert env_file.stat().st_size == 0
    if linked is not None:
        assert linked.stat().st_ino == original_inode


@pytest.mark.skipif(os.geteuid() != 0, reason="requires ownership change permission")
def test_create_does_not_replace_an_empty_target_owned_by_another_user(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "provider.env"
    env_file.touch(mode=0o600)
    os.chown(env_file, 65534, -1)
    original_inode = env_file.stat().st_ino

    result = _run_helper("create", str(env_file))

    assert result.returncode != 0
    assert env_file.stat().st_ino == original_inode
    assert env_file.stat().st_size == 0


@pytest.mark.parametrize("mode", [0o644, 0o400, 0o660])
def test_commands_reject_environment_files_that_are_not_mode_0600(tmp_path: Path, mode: int) -> None:
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


def test_down_failure_preserves_descriptor_for_retry(tmp_path: Path) -> None:
    env_file = tmp_path / "provider.env"
    assert _run_helper("create", str(env_file)).returncode == 0
    env = _write_fake_docker(tmp_path) | {"FAKE_DOWN_FAIL_ONCE": "1"}

    failed = _run_helper("down", str(env_file), env=env)

    assert failed.returncode == 7
    assert env_file.exists()
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    retried = _run_helper("down", str(env_file), env=env)
    assert retried.returncode == 0, retried.stderr
    assert not env_file.exists()
    assert failed.stdout == retried.stdout == ""


@pytest.mark.parametrize("kind", ["container", "volume", "network"])
@pytest.mark.parametrize("failure", ["query", "residual"])
def test_down_preserves_descriptor_unless_all_project_resources_are_absent(
    tmp_path: Path, kind: str, failure: str
) -> None:
    env_file = tmp_path / "provider.env"
    assert _run_helper("create", str(env_file)).returncode == 0
    setting = "FAKE_QUERY_FAILURE" if failure == "query" else "FAKE_RESIDUAL"
    env = _write_fake_docker(tmp_path) | {setting: kind}

    result = _run_helper("down", str(env_file), env=env)

    assert result.returncode != 0
    assert env_file.exists()
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert result.stdout == ""


def test_up_resolves_compose_ports_and_updates_environment_atomically(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "provider.env"
    assert _run_helper("create", str(env_file)).returncode == 0
    before = _read_exports(env_file)
    assert {
        before["M1_TEST_POSTGRES_PORT"],
        before["M1_TEST_KEYCLOAK_PORT"],
        before["M1_TEST_MINIO_PORT"],
        before["M1_TEST_API_CALLBACK_PORT"],
    } == {"0"}
    env = _write_fake_docker(tmp_path)

    result = _run_helper("up", str(env_file), env=env)

    assert result.returncode == 0, result.stderr
    exports = _read_exports(env_file)
    assert exports["M1_TEST_ENDPOINTS_READY"] == "1"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert all(
        re.fullmatch(r"[1-9][0-9]{3,4}", exports[name])
        for name in (
            "M1_TEST_POSTGRES_PORT",
            "M1_TEST_KEYCLOAK_PORT",
            "M1_TEST_MINIO_PORT",
            "M1_TEST_API_CALLBACK_PORT",
        )
    )
    assert exports["M1_TEST_DATABASE_URL"].endswith(
        f"@127.0.0.1:{exports['M1_TEST_POSTGRES_PORT']}/peerassist"
    )
    assert exports["M1_TEST_OIDC_ISSUER"].startswith(f"http://127.0.0.1:{exports['M1_TEST_KEYCLOAK_PORT']}/")
    assert exports["M1_TEST_S3_ENDPOINT"] == (f"http://127.0.0.1:{exports['M1_TEST_MINIO_PORT']}")
    assert exports["M1_TEST_OIDC_REDIRECT_URI"] == (
        f"http://127.0.0.1:{exports['M1_TEST_API_CALLBACK_PORT']}/api/v1/auth/callback"
    )


def test_parent_shell_must_resource_environment_after_up(tmp_path: Path) -> None:
    env_file = tmp_path / "provider.env"
    assert _run_helper("create", str(env_file)).returncode == 0
    env = _write_fake_docker(tmp_path)

    result = subprocess.run(
        [
            "bash",
            "-c",
            'set -eu; source "$1"; stale=$M1_TEST_DATABASE_URL; '
            'test "$M1_TEST_ENDPOINTS_READY" = 0; "$2" up "$1"; '
            'test "$M1_TEST_ENDPOINTS_READY" = 0; '
            'test "$M1_TEST_DATABASE_URL" = "$stale"; source "$1"; '
            'test "$M1_TEST_ENDPOINTS_READY" = 1; '
            'test "$M1_TEST_POSTGRES_PORT" != 0; "$2" require-ready "$1"; '
            '"$2" down "$1"',
            "bash",
            str(env_file),
            str(ENV_HELPER),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert not env_file.exists()


@pytest.mark.parametrize("command", ["wait", "require-ready"])
def test_consumer_commands_reject_unresolved_endpoints(tmp_path: Path, command: str) -> None:
    env_file = tmp_path / "provider.env"
    assert _run_helper("create", str(env_file)).returncode == 0
    env = _write_fake_docker(tmp_path)

    result = _run_helper(command, str(env_file), env=env)

    assert result.returncode != 0
    assert "not ready" in result.stderr.lower()


def test_real_provider_preludes_resource_resolved_endpoints() -> None:
    plan = IMPLEMENTATION_PLAN.read_text(encoding="utf-8")
    provider_blocks = [
        block
        for index, block in enumerate(plan.split("```bash"))
        if index and "$M1_TEST_" in block.split("```", 1)[0]
    ]

    assert provider_blocks
    for block in provider_blocks:
        commands = block.split("```", 1)[0]
        create = commands.index('bash scripts/m1_test_env.sh create "$M1_ENV_FILE"')
        first_source = commands.index('source "$M1_ENV_FILE"', create)
        up = commands.index('bash scripts/m1_test_env.sh up "$M1_ENV_FILE"', first_source)
        second_source = commands.index('source "$M1_ENV_FILE"', up)
        ready = commands.index(
            'bash scripts/m1_test_env.sh require-ready "$M1_ENV_FILE"',
            second_source,
        )
        wait = commands.index('bash scripts/m1_test_env.sh wait "$M1_ENV_FILE"', ready)
        consumer = commands.index("$M1_TEST_", wait)
        assert create < first_source < up < second_source < ready < wait < consumer


def test_two_projects_receive_distinct_compose_ports(tmp_path: Path) -> None:
    env = _write_fake_docker(tmp_path)
    env_files = [tmp_path / "provider-a.env", tmp_path / "provider-b.env"]
    for env_file in env_files:
        assert _run_helper("create", str(env_file)).returncode == 0
        result = _run_helper("up", str(env_file), env=env)
        assert result.returncode == 0, result.stderr

    first, second = (_read_exports(path) for path in env_files)
    port_names = {
        "M1_TEST_POSTGRES_PORT",
        "M1_TEST_KEYCLOAK_PORT",
        "M1_TEST_MINIO_PORT",
        "M1_TEST_API_CALLBACK_PORT",
    }
    assert {first[name] for name in port_names}.isdisjoint({second[name] for name in port_names})


def test_wait_fails_fast_when_a_provider_exits(tmp_path: Path) -> None:
    env_file = tmp_path / "provider.env"
    assert _run_helper("create", str(env_file)).returncode == 0
    env = _write_fake_docker(tmp_path)
    assert _run_helper("up", str(env_file), "postgres", env=env).returncode == 0
    bin_dir = tmp_path / "bin"
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'case " $* " in\n'
        "  *' ps --all --quiet '*) printf 'provider-id\\n' ;;\n"
        "  *' inspect '*) printf 'exited  1\\n' ;;\n"
        "  *) exit 2 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)

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
    assert "free_ports" not in helper


@pytest.mark.skipif(
    shutil.which("docker") is None,
    reason="Docker CLI is unavailable",
)
def test_real_provider_harness_smoke_and_cleanup(tmp_path: Path) -> None:
    info = subprocess.run(["docker", "info"], check=False, capture_output=True, text=True, timeout=30)
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
            f"http://127.0.0.1:{int(exports['M1_TEST_API_CALLBACK_PORT']) + 1}/api/v1/auth/callback"
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
        primary = sys.exc_info()[1]
        down = _run_helper("down", str(env_file))
        if down.returncode != 0:
            message = f"provider harness cleanup failed: {down.stderr}"
            if primary is None:
                pytest.fail(message)
            primary.add_note(message)
    assert not env_file.exists()
    for resource_command in (
        ["ps", "-aq"],
        ["volume", "ls", "-q"],
        ["network", "ls", "-q"],
    ):
        remaining = subprocess.run(
            [
                "docker",
                *resource_command,
                "--filter",
                f"label=com.docker.compose.project={project}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        assert remaining.stdout.strip() == ""

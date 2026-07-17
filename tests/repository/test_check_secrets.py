from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from scripts.check_secrets import Finding, RepositoryScanError, scan_files

REPOSITORY_ROOT = Path(__file__).parents[2]


def _github_token() -> str:
    return "gh" + "p_" + "A" * 36


def _github_token_variants() -> tuple[str, ...]:
    return tuple("gh" + prefix + "_" + "A" * 36 for prefix in ("o", "u", "s", "r"))


def _openai_token() -> str:
    return "s" + "k-" + "B" * 40


def _aws_access_key() -> str:
    return "AK" + "IA" + "C" * 16


def _aws_session_key() -> str:
    return "AS" + "IA" + "D" * 16


def _private_key_header() -> str:
    return "-----BEGIN " + "PRIVATE KEY-----"


def test_scan_files_detects_supported_rules_without_exposing_values(tmp_path: Path) -> None:
    sample = tmp_path / "config.txt"
    secrets = (_github_token(), _openai_token(), _aws_access_key(), "correct horse battery staple")
    sample.write_text(
        f"github={secrets[0]}\n"
        f"openai={secrets[1]}\n"
        f"aws={secrets[2]}\n"
        f"password={secrets[3]!r}\n"
        f"{_private_key_header()}\n",
        encoding="utf-8",
    )

    findings = scan_files(tmp_path, [sample])

    assert [(item.line, item.rule) for item in findings] == [
        (1, "github-token"),
        (2, "openai-api-key"),
        (3, "aws-access-key"),
        (4, "password-assignment"),
        (5, "private-key"),
    ]
    rendered = "\n".join(item.render() for item in findings)
    assert all(secret not in rendered for secret in secrets)
    assert all(repr(item) not in rendered for item in findings)


def test_scan_files_detects_all_official_github_token_prefixes_without_exposing_values(tmp_path: Path) -> None:
    sample = tmp_path / "tokens.txt"
    secrets = _github_token_variants()
    sample.write_text("\n".join(secrets), encoding="utf-8")

    findings = scan_files(tmp_path, [sample])

    assert findings == [Finding(Path("tokens.txt"), line, "github-token") for line in range(1, 5)]
    rendered = "\n".join(item.render() for item in findings)
    assert all(secret not in rendered for secret in secrets)


def test_scan_files_allows_documented_password_placeholders(tmp_path: Path) -> None:
    sample = tmp_path / "example.env"
    sample.write_text(
        "PASSWORD=\n"
        "password=example\n"
        "db_password=changeme\n"
        "admin-password=<your-password>\n"
        "PASSWORD=${PASSWORD}\n",
        encoding="utf-8",
    )

    assert scan_files(tmp_path, [sample]) == []


def test_scan_files_rejects_shell_default_password_expansions(tmp_path: Path) -> None:
    sample = tmp_path / "config.env"
    sample.write_text(
        "PASSWORD=${DB_PASSWORD:-RealSecret123!}\n"
        "SECOND_PASSWORD=${DB_PASSWORD:=RealSecret123!}\n"
        "THIRD_PASSWORD=${DB_PASSWORD-RealSecret123!}\n",
        encoding="utf-8",
    )

    assert [(item.line, item.rule) for item in scan_files(tmp_path, [sample])] == [
        (1, "password-assignment"),
        (2, "password-assignment"),
        (3, "password-assignment"),
    ]


def test_scan_files_detects_quoted_json_and_toml_password_assignments(tmp_path: Path) -> None:
    sample = tmp_path / "config.txt"
    sample.write_text(
        '{"password": "RealSecret123!"}\n'
        "'admin_password' = 'SecondSecret456!'\n",
        encoding="utf-8",
    )

    assert [(item.line, item.rule) for item in scan_files(tmp_path, [sample])] == [
        (1, "password-assignment"),
        (2, "password-assignment"),
    ]


def test_scan_files_checks_every_password_assignment_on_a_line(tmp_path: Path) -> None:
    sample = tmp_path / "config.env"
    sample.write_text("password=${PASSWORD} admin_password=RealSecret123!\n", encoding="utf-8")

    assert scan_files(tmp_path, [sample]) == [Finding(Path("config.env"), 1, "password-assignment")]


def test_password_assignment_path_render_redacts_quoted_keys_and_values() -> None:
    finding = Finding(Path('prefix-"password":"RealSecret123!".txt'), 4, "github-token")

    rendered = finding.render()

    assert rendered == 'prefix-"password":<redacted>.txt:4: github-token'
    assert "RealSecret123!" not in rendered


def test_scan_files_detects_aws_session_access_key(tmp_path: Path) -> None:
    sample = tmp_path / "config.txt"
    secret = _aws_session_key()
    sample.write_text(secret, encoding="utf-8")

    findings = scan_files(tmp_path, [sample])

    assert findings == [Finding(Path("config.txt"), 1, "aws-access-key")]
    assert secret not in findings[0].render()


def test_scan_files_is_stable_deduplicated_and_skips_unsafe_inputs(tmp_path: Path) -> None:
    secret = _github_token()
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    binary = tmp_path / "binary.dat"
    lfs = tmp_path / "pointer.txt"
    fixture = tmp_path / "tests" / "fixtures" / "secret.txt"
    dist = tmp_path / "web" / "peerassist-workspace" / "dist" / "bundle.js"
    first.write_text(f"token={secret}\ntoken={secret}\n", encoding="utf-8")
    second.write_text(f"token={secret}\n", encoding="utf-8")
    binary.write_bytes(b"prefix\x00" + secret.encode())
    lfs.write_text(
        "version https://git-lfs.github.com/spec/v1\n"
        "oid sha256:" + "d" * 64 + "\nsize 123\n",
        encoding="utf-8",
    )
    fixture.parent.mkdir(parents=True)
    fixture.write_text(secret, encoding="utf-8")
    dist.parent.mkdir(parents=True)
    dist.write_text(secret, encoding="utf-8")

    findings = scan_files(tmp_path, [second, first, first, binary, lfs, fixture, dist])

    assert findings == [
        Finding(Path("a.txt"), 1, "github-token"),
        Finding(Path("a.txt"), 2, "github-token"),
        Finding(Path("b.txt"), 1, "github-token"),
    ]


def test_scan_files_skips_oversized_but_fails_closed_on_unreadable_file(tmp_path: Path, monkeypatch) -> None:
    oversized = tmp_path / "large.txt"
    unreadable = tmp_path / "unreadable.txt"
    oversized.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    unreadable.write_text(_openai_token(), encoding="utf-8")
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path == unreadable:
            raise OSError("simulated read failure")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    assert scan_files(tmp_path, [oversized]) == []
    with pytest.raises(RepositoryScanError):
        scan_files(tmp_path, [unreadable])


def test_invalid_utf8_secret_input_fails_closed_for_api_default_and_explicit_cli(tmp_path: Path) -> None:
    source = tmp_path / "bad.txt"
    source.write_bytes(b"\xff\xfe" + _github_token().encode())
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "bad.txt"], cwd=tmp_path, check=True)

    with pytest.raises(RepositoryScanError):
        scan_files(tmp_path, [source])

    for args in ([], ["bad.txt"]):
        result = subprocess.run(
            [sys.executable, str(REPOSITORY_ROOT / "scripts" / "check_secrets.py"), *args],
            cwd=tmp_path,
            check=False,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 2
        assert result.stdout == ""
        assert result.stderr == "repository scan failed: unable to read secret-scan input\n"
        assert _github_token() not in result.stderr
        assert "Traceback" not in result.stderr


def test_default_scan_skips_policy_sources_but_explicit_scan_still_checks_them(tmp_path: Path) -> None:
    policy_script = tmp_path / "scripts" / "check_secrets.py"
    policy_test = tmp_path / "tests" / "repository" / "test_check_secrets.py"
    ordinary = tmp_path / "config.txt"
    secret = _github_token()
    policy_script.parent.mkdir(parents=True)
    policy_test.parent.mkdir(parents=True)
    policy_script.write_text(secret, encoding="utf-8")
    policy_test.write_text(secret, encoding="utf-8")
    ordinary.write_text(secret, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)

    result = subprocess.run(
        [sys.executable, str(REPOSITORY_ROOT / "scripts" / "check_secrets.py")],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert result.stdout == "config.txt:1: github-token\n"
    assert scan_files(tmp_path, [policy_script, policy_test]) == [
        Finding(Path("scripts/check_secrets.py"), 1, "github-token"),
        Finding(Path("tests/repository/test_check_secrets.py"), 1, "github-token"),
    ]


def test_check_secrets_cli_never_prints_matched_value(tmp_path: Path) -> None:
    sample = tmp_path / "config.txt"
    secret = _openai_token()
    sample.write_text(f"token={secret}\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(REPOSITORY_ROOT / "scripts" / "check_secrets.py"), str(sample)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert result.stdout == "config.txt:1: openai-api-key\n"
    assert secret not in result.stdout
    assert result.stderr == ""


def test_secret_shaped_filename_is_redacted_only_when_rendered(tmp_path: Path) -> None:
    secret = _github_token()
    sample = tmp_path / f"captured-{secret}.txt"
    sample.write_text(secret, encoding="utf-8")

    findings = scan_files(tmp_path, [sample])

    assert findings == [Finding(Path(sample.name), 1, "github-token")]
    assert findings[0].path == Path(sample.name)
    assert findings[0].render() == "captured-<redacted>.txt:1: github-token"
    assert secret not in findings[0].render()

    result = subprocess.run(
        [sys.executable, str(REPOSITORY_ROOT / "scripts" / "check_secrets.py"), str(sample)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert result.stdout == "captured-<redacted>.txt:1: github-token\n"
    assert secret not in result.stdout
    assert result.stderr == ""


def test_password_assignment_filename_is_redacted_without_changing_path(tmp_path: Path) -> None:
    password = "very-private-password"
    sample = tmp_path / f"password={password}.txt"
    sample.write_text(_github_token(), encoding="utf-8")

    finding = scan_files(tmp_path, [sample])[0]

    assert finding.path == Path(sample.name)
    assert finding.render() == "password=<redacted>:1: github-token"
    assert password not in finding.render()

    result = subprocess.run(
        [sys.executable, str(REPOSITORY_ROOT / "scripts" / "check_secrets.py"), str(sample)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert result.stdout == "password=<redacted>:1: github-token\n"
    assert password not in result.stdout
    assert result.stderr == ""


def test_password_word_without_assignment_is_not_redacted(tmp_path: Path) -> None:
    sample = tmp_path / "password-policy.md"
    sample.write_text(_github_token(), encoding="utf-8")

    finding = scan_files(tmp_path, [sample])[0]

    assert finding.render() == "password-policy.md:1: github-token"


def test_symlink_loop_is_skipped_by_api_and_default_cli(tmp_path: Path) -> None:
    loop = tmp_path / "loop.txt"
    try:
        loop.symlink_to("loop.txt")
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are not supported on this platform")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "loop.txt"], cwd=tmp_path, check=True)

    assert scan_files(tmp_path, [loop]) == []

    result = subprocess.run(
        [sys.executable, str(REPOSITORY_ROOT / "scripts" / "check_secrets.py")],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""

    explicit = subprocess.run(
        [sys.executable, str(REPOSITORY_ROOT / "scripts" / "check_secrets.py"), "loop.txt"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert explicit.returncode == 0
    assert explicit.stdout == ""
    assert explicit.stderr == ""


def test_tracked_symlink_payload_is_scanned_without_following_target(tmp_path: Path) -> None:
    secret = _github_token()
    link = tmp_path / "leak-link"
    try:
        link.symlink_to(secret)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are not supported on this platform")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "leak-link"], cwd=tmp_path, check=True)

    findings = scan_files(tmp_path, [link])

    assert findings == [Finding(Path("leak-link"), 1, "github-token")]
    assert secret not in findings[0].render()

    result = subprocess.run(
        [sys.executable, str(REPOSITORY_ROOT / "scripts" / "check_secrets.py")],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert result.stdout == "leak-link:1: github-token\n"
    assert secret not in result.stdout
    assert result.stderr == ""


def test_intermediate_symlink_escape_is_not_read_and_explicit_cli_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    secret = _github_token()
    external = outside / "outside.txt"
    external.write_text(secret, encoding="utf-8")
    link = root / "sub"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are not supported on this platform")

    assert scan_files(root, [link / "outside.txt"]) == []

    result = subprocess.run(
        [sys.executable, str(REPOSITORY_ROOT / "scripts" / "check_secrets.py"), "sub/outside.txt"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "repository scan failed\n"
    assert secret not in result.stderr
    assert "Traceback" not in result.stderr

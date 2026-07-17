from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts.check_secrets import Finding, scan_files

REPOSITORY_ROOT = Path(__file__).parents[2]


def _github_token() -> str:
    return "gh" + "p_" + "A" * 36


def _github_token_variants() -> tuple[str, ...]:
    return tuple("gh" + prefix + "_" + "A" * 36 for prefix in ("o", "u", "s", "r"))


def _openai_token() -> str:
    return "s" + "k-" + "B" * 40


def _aws_access_key() -> str:
    return "AK" + "IA" + "C" * 16


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


def test_scan_files_skips_oversized_and_unreadable_files(tmp_path: Path, monkeypatch) -> None:
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

    assert scan_files(tmp_path, [oversized, unreadable]) == []


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

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from scripts.check_docs import find_broken_links

REPOSITORY_ROOT = Path(__file__).parents[2]


def test_find_broken_links_resolves_spaces_urls_queries_and_anchors(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "Target File.md").write_text("# Target\n", encoding="utf-8")
    source = docs / "index.md"
    source.write_text(
        "[plain](Target File.md)\n"
        "[encoded](Target%20File.md#target)\n"
        "[query](Target%20File.md?download=1#target)\n"
        '[title](Target%20File.md "A title")\n'
        "[paren title](Target%20File.md (title))\n"
        '[angle title](<Target File.md> "title")\n'
        "[local](#section)\n"
        "[web](https://example.invalid/docs)\n"
        "[mail](mailto:security@example.invalid)\n",
        encoding="utf-8",
    )

    assert find_broken_links(tmp_path, [source]) == []


def test_find_broken_links_reports_missing_and_root_escape_in_stable_order(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    first = docs / "a.md"
    second = docs / "b.md"
    first.write_text("[escape](../../outside.md)\n[missing](z.md)\n", encoding="utf-8")
    second.write_text("[missing](<a missing.md>)\n", encoding="utf-8")

    assert find_broken_links(tmp_path, [second, first]) == [
        (Path("docs/a.md"), "../../outside.md"),
        (Path("docs/a.md"), "z.md"),
        (Path("docs/b.md"), "a%20missing.md"),
    ]


def test_find_broken_links_ignores_images_and_fenced_code(tmp_path: Path) -> None:
    source = tmp_path / "README.md"
    source.write_text(
        "![missing image](missing.png)\n"
        "```markdown\n"
        "[example](not-real.md)\n"
        "```\n"
        "~~~\n"
        "[second example](also-not-real.md)\n"
        "~~~\n",
        encoding="utf-8",
    )

    assert find_broken_links(tmp_path, [source]) == []


def test_shorter_fence_marker_does_not_close_longer_fence(tmp_path: Path) -> None:
    source = tmp_path / "README.md"
    source.write_text(
        "````markdown\n"
        "```\n"
        "[example](not-real.md)\n"
        "````\n",
        encoding="utf-8",
    )

    assert find_broken_links(tmp_path, [source]) == []


def test_fence_marker_with_trailing_text_does_not_close_fence(tmp_path: Path) -> None:
    source = tmp_path / "README.md"
    source.write_text(
        "````markdown\n"
        "````not-a-closing-fence\n"
        "[example](not-real.md)\n"
        "````\n",
        encoding="utf-8",
    )

    assert find_broken_links(tmp_path, [source]) == []


def test_check_docs_cli_prints_only_path_and_target(tmp_path: Path) -> None:
    source = tmp_path / "README.md"
    source.write_text("[missing](missing.md)\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(REPOSITORY_ROOT / "scripts" / "check_docs.py"), str(source)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert result.stdout == "README.md: missing.md\n"
    assert result.stderr == ""


def test_overlong_local_destination_is_reported_without_filesystem_exception(tmp_path: Path) -> None:
    raw_target = "x" * 5000
    source = tmp_path / "README.md"
    source.write_text(f"[overlong]({raw_target})\n", encoding="utf-8")

    assert find_broken_links(tmp_path, [source]) == [(Path("README.md"), raw_target)]

    result = subprocess.run(
        [sys.executable, str(REPOSITORY_ROOT / "scripts" / "check_docs.py"), str(source)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert result.stdout == f"README.md: {raw_target}\n"
    assert result.stderr == ""


def test_symlink_loop_is_skipped_by_api_and_default_cli(tmp_path: Path) -> None:
    loop = tmp_path / "loop.md"
    try:
        loop.symlink_to("loop.md")
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are not supported on this platform")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "loop.md"], cwd=tmp_path, check=True)

    assert find_broken_links(tmp_path, [loop]) == []

    result = subprocess.run(
        [sys.executable, str(REPOSITORY_ROOT / "scripts" / "check_docs.py")],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""

    explicit = subprocess.run(
        [sys.executable, str(REPOSITORY_ROOT / "scripts" / "check_docs.py"), "loop.md"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert explicit.returncode == 0
    assert explicit.stdout == ""
    assert explicit.stderr == ""


def test_commonmark_parser_handles_code_escapes_images_and_indented_fences(tmp_path: Path) -> None:
    source = tmp_path / "README.md"
    source.write_text(
        "`[code](missing-code.md)`\n"
        "\\[escaped](missing-escaped.md)\n"
        "\\![link](missing-link.md)\n"
        "[![badge](image.svg)](missing-outer.md)\n"
        "    ```\n"
        "[real](missing-real.md)\n",
        encoding="utf-8",
    )

    assert find_broken_links(tmp_path, [source]) == [
        (Path("README.md"), "missing-link.md"),
        (Path("README.md"), "missing-outer.md"),
        (Path("README.md"), "missing-real.md"),
    ]


def test_commonmark_parser_handles_multiline_reference_and_apostrophe_destinations(tmp_path: Path) -> None:
    (tmp_path / "Bob's.md").write_text("# Bob\n", encoding="utf-8")
    (tmp_path / "Target File.md").write_text("# Target\n", encoding="utf-8")
    source = tmp_path / "README.md"
    source.write_text(
        "[apostrophe](Bob's.md)\n"
        "[multiline](Target%20File.md\n"
        '  "A title")\n'
        "[reference][target]\n\n"
        "[target]: Target%20File.md 'Reference title'\n",
        encoding="utf-8",
    )

    assert find_broken_links(tmp_path, [source]) == []


@pytest.mark.parametrize("script_name", ["check_docs.py", "check_secrets.py"])
def test_cli_fails_closed_outside_git_and_for_missing_explicit_path(tmp_path: Path, script_name: str) -> None:
    script = REPOSITORY_ROOT / "scripts" / script_name

    default = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    missing = subprocess.run(
        [sys.executable, str(script), "missing.txt"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert default.returncode == 2
    assert default.stdout == ""
    assert default.stderr == "repository scan failed\n"
    assert "Traceback" not in default.stderr
    assert missing.returncode == 2
    assert missing.stdout == ""
    assert missing.stderr == "repository scan failed\n"
    assert "Traceback" not in missing.stderr

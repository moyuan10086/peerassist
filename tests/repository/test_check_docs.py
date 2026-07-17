from __future__ import annotations

import subprocess
import sys
from pathlib import Path

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
    second.write_text("[missing](a missing.md)\n", encoding="utf-8")

    assert find_broken_links(tmp_path, [second, first]) == [
        (Path("docs/a.md"), "../../outside.md"),
        (Path("docs/a.md"), "z.md"),
        (Path("docs/b.md"), "a missing.md"),
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

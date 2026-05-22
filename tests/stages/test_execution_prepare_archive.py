from __future__ import annotations

import zipfile
from io import BytesIO

from fact_generation.execution.nodes.prepare import _extract_archive_bytes


def test_extract_archive_shortens_long_member_paths(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("EXECUTION_ARCHIVE_FORCE_SHORT_PATHS", "1")
    monkeypatch.setenv("EXECUTION_ARCHIVE_MAX_PATH", "120")
    long_dir = "nested_" + ("x" * 160)
    blob = BytesIO()
    with zipfile.ZipFile(blob, "w") as zf:
        zf.writestr(f"project/{long_dir}/result.txt", "ok")
        zf.writestr("other.txt", "keep root")

    manifest = _extract_archive_bytes(blob.getvalue(), tmp_path / "source")

    assert manifest["files"] == 2
    assert manifest["path_rewrite_count"] == 1
    rewritten = manifest["path_rewrites"][0]
    assert rewritten["from"].endswith("/result.txt")
    assert rewritten["to"] != rewritten["from"]
    assert (tmp_path / "source" / rewritten["to"]).read_text(encoding="utf-8") == "ok"

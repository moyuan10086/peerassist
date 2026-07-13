from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pipeline_full


def test_preallocated_run_directory_is_used_without_nested_generated_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_dir = tmp_path / "jobs" / "job-123" / "run"
    args = Namespace(run_root=tmp_path / "runs", run_dir_override=str(run_dir))

    def fail_generated_path(*args: object, **kwargs: object) -> None:
        raise AssertionError("generated run layout must not be used")

    monkeypatch.setattr(pipeline_full, "make_run_id", fail_generated_path)
    monkeypatch.setattr(pipeline_full, "build_run_dir", fail_generated_path)

    run_id, resolved = pipeline_full._resolve_run_directory(args, "paper-key")

    assert resolved == run_dir
    assert run_id == "job-123"
    assert not run_dir.exists()


def test_default_run_directory_behavior_is_preserved(tmp_path: Path, monkeypatch) -> None:
    args = Namespace(run_root=tmp_path / "runs")
    expected = tmp_path / "runs" / "paper-key" / "generated-id"
    monkeypatch.setattr(pipeline_full, "make_run_id", lambda: "generated-id")
    monkeypatch.setattr(
        pipeline_full,
        "build_run_dir",
        lambda run_root, paper_key, run_id: expected,
    )

    run_id, resolved = pipeline_full._resolve_run_directory(args, "paper-key")

    assert run_id == "generated-id"
    assert resolved == expected

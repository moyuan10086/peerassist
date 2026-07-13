from __future__ import annotations

import json
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from common.pipeline_context import init_full_pipeline_context, peerassist_stage_dir, write_json_file
from peerassist.confirmation_cli import main as confirmation_cli_main
from peerassist.confirmation_workflow import (
    apply_confirmation_decision,
    finalize_confirmed_report,
    load_confirmation_state,
)
from schemas.peerassist import Concern, ConcernLevel, ConcernStatus


def _seed_peerassist_stage(run_dir: Path) -> Path:
    init_full_pipeline_context(run_dir=run_dir)
    out_dir = peerassist_stage_dir(run_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    concern = Concern(
        id="concern_pending_001",
        level=ConcernLevel.CLARIFICATION_NEEDED,
        category="statistics",
        title="Reported percentage needs clarification",
        evidence_ids=["P01-L001"],
        impact="May affect support for the result.",
        benign_explanation="A different denominator may have been used.",
        author_action="Please clarify the calculation basis.",
        status=ConcernStatus.PENDING_HUMAN_CONFIRMATION,
        source_agent_ids=["statistics_agent"],
    )
    write_json_file(
        out_dir / "peerassist_concerns.json",
        {"schema_version": "peerassist.concerns.v1", "mode": "fast", "concerns": [concern.model_dump(mode="json")]},
    )
    write_json_file(
        out_dir / "confirmation_bundle.json",
        {
            "schema_version": "peerassist.confirmation_bundle.v1",
            "counts_by_status": {"pending_human_confirmation": 1},
            "groups": {
                "pending_human_confirmation": [
                    {
                        **concern.model_dump(mode="json"),
                        "evidence": [{"id": "P01-L001", "locator": "p.1 line 1"}],
                    }
                ],
                "confirmed": [],
                "downgraded": [],
                "rewritten": [],
                "deleted": [],
            },
        },
    )
    write_json_file(
        out_dir / "confirmation_review_queue.json",
        {
            "schema_version": "peerassist.confirmation_review_queue.v1",
            "items": [{"id": "concern_pending_001", "position": 1}],
        },
    )
    write_json_file(
        out_dir / "human_confirmations.json",
        {"schema_version": "peerassist.human_confirmations.v1", "actions": []},
    )
    return out_dir


def test_load_confirmation_state_reads_queue_and_existing_actions(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _seed_peerassist_stage(run_dir)

    state = load_confirmation_state(run_dir=run_dir)

    assert state["schema_version"] == "peerassist.confirmation_state.v1"
    assert state["queue"]["items"][0]["id"] == "concern_pending_001"
    assert state["actions"] == []
    assert state["pending_count"] == 1
    assert state["confirmation_revision"] == 0


def test_apply_confirmation_decision_appends_action_and_refreshes_reports(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    out_dir = _seed_peerassist_stage(run_dir)

    result = apply_confirmation_decision(
        run_dir=run_dir,
        paper_id="demo",
        concern_id="concern_pending_001",
        action="rewrite",
        reviewer_id="reviewer-1",
        timestamp="2026-07-10T00:00:00Z",
        previous_text="Please clarify the calculation basis.",
        new_text="Please clarify the denominator used for this percentage.",
        reason="More precise wording.",
    )

    confirmations = json.loads((out_dir / "human_confirmations.json").read_text(encoding="utf-8"))
    assert confirmations["actions"][0]["action"] == "rewrite"
    assert confirmations["actions"][0]["new_text"] == "Please clarify the denominator used for this percentage."
    assert confirmations["revision"] == 1
    assert confirmations["actions"][0]["finding_lineage_id"].startswith("fln_")
    assert confirmations["actions"][0]["finding_id"].startswith("fnd_")
    assert confirmations["actions"][0]["revision"] == 1
    report = json.loads((out_dir / "peerassist_report.json").read_text(encoding="utf-8"))
    assert report["confirmed_count"] == 1
    assert report["pending_count"] == 0
    assert "Please clarify the denominator used for this percentage." in (
        out_dir / "peerassist_report.md"
    ).read_text(encoding="utf-8")
    assert result["report_json"] == str(out_dir / "peerassist_report.json")


def test_legacy_confirmation_action_remains_readable(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    out_dir = _seed_peerassist_stage(run_dir)
    write_json_file(
        out_dir / "human_confirmations.json",
        {
            "schema_version": "peerassist.human_confirmations.v1",
            "actions": [
                {
                    "concern_id": "concern_pending_001",
                    "action": "confirm",
                    "reviewer_id": "legacy-reviewer",
                    "timestamp": "2026-07-10T00:00:00Z",
                }
            ],
        },
    )

    state = load_confirmation_state(run_dir=run_dir)

    assert state["actions"][0]["concern_id"] == "concern_pending_001"
    assert state["confirmation_revision"] == 0


def test_finalize_rejects_unresolved_core_without_override(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    out_dir = _seed_peerassist_stage(run_dir)
    concerns = json.loads((out_dir / "peerassist_concerns.json").read_text(encoding="utf-8"))
    concerns["concerns"][0]["importance"] = "core"
    write_json_file(out_dir / "peerassist_concerns.json", concerns)

    result = finalize_confirmed_report(
        run_dir=run_dir,
        paper_id="demo",
        expected_confirmation_revision=0,
    )

    assert result["status"] == "blocked"
    assert result["error_code"] == "unresolved_core_findings"
    assert result["unresolved_core_count"] == 1
    assert not (out_dir / "current_final_report.json").exists()


def test_finalize_versions_localized_reports_and_is_idempotent(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    out_dir = _seed_peerassist_stage(run_dir)

    first = finalize_confirmed_report(
        run_dir=run_dir,
        paper_id="demo",
        expected_confirmation_revision=0,
        override_reason="Reviewer accepted the remaining core uncertainty.",
    )
    repeated = finalize_confirmed_report(
        run_dir=run_dir,
        paper_id="demo",
        expected_confirmation_revision=0,
        override_reason="Reviewer accepted the remaining core uncertainty.",
    )

    assert first["status"] == "ok"
    assert repeated["status"] == "ok"
    assert repeated["idempotent"] is True
    assert repeated["manifest_path"] == first["manifest_path"]
    assert load_confirmation_state(run_dir=run_dir)["confirmation_revision"] == 0
    manifest = json.loads(Path(first["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["confirmation_revision"] == 0
    assert manifest["override_reason"]
    for key in ("report_en_json", "report_zh_json", "report_en_md", "report_zh_md"):
        assert Path(out_dir / manifest["artifacts"][key]).exists()

    decision = apply_confirmation_decision(
        run_dir=run_dir,
        paper_id="demo",
        concern_id="concern_pending_001",
        action="confirm",
        reviewer_id="reviewer-1",
        timestamp="2026-07-10T01:00:00Z",
        expected_revision=0,
    )
    second = finalize_confirmed_report(
        run_dir=run_dir,
        paper_id="demo",
        expected_confirmation_revision=decision["confirmation_revision"],
    )
    assert second["status"] == "ok"
    assert second["manifest_path"] != first["manifest_path"]
    assert Path(first["manifest_path"]).exists()
    assert json.loads((out_dir / "current_final_report.json").read_text(encoding="utf-8"))[
        "manifest_path"
    ] == str(Path(second["manifest_path"]).relative_to(out_dir))

    stale = finalize_confirmed_report(
        run_dir=run_dir,
        paper_id="demo",
        expected_confirmation_revision=0,
        override_reason="stale",
    )
    assert stale["status"] == "revision_conflict"


def test_finalize_export_failure_preserves_previous_pointer(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "run"
    out_dir = _seed_peerassist_stage(run_dir)
    first = finalize_confirmed_report(
        run_dir=run_dir,
        paper_id="demo",
        expected_confirmation_revision=0,
        override_reason="Initial export.",
    )
    pointer_before = (out_dir / "current_final_report.json").read_text(encoding="utf-8")
    decision = apply_confirmation_decision(
        run_dir=run_dir,
        paper_id="demo",
        concern_id="concern_pending_001",
        action="confirm",
        reviewer_id="reviewer-1",
        timestamp="2026-07-10T01:00:00Z",
        expected_revision=0,
    )

    def fail_export(**_kwargs):
        raise RuntimeError("synthetic export failure")

    monkeypatch.setattr("peerassist.confirmation_workflow.export_peerassist_report", fail_export)
    failed = finalize_confirmed_report(
        run_dir=run_dir,
        paper_id="demo",
        expected_confirmation_revision=decision["confirmation_revision"],
    )

    assert first["status"] == "ok"
    assert failed["status"] == "failed"
    assert failed["error_code"] == "report_export_failed"
    assert (out_dir / "current_final_report.json").read_text(encoding="utf-8") == pointer_before


@pytest.mark.parametrize("winner", ["confirmation", "finalize"])
def test_confirmation_cas_and_finalize_pointer_commit_have_single_winner(
    tmp_path: Path,
    winner: str,
) -> None:
    run_dir = tmp_path / "run"
    out_dir = _seed_peerassist_stage(run_dir)
    barrier = Barrier(2)

    def confirmation_hook() -> None:
        barrier.wait()
        if winner == "finalize":
            barrier.wait()

    def finalize_hook() -> None:
        barrier.wait()
        if winner == "confirmation":
            barrier.wait()

    def confirm() -> dict[str, object]:
        result = apply_confirmation_decision(
            run_dir=run_dir,
            paper_id="demo",
            concern_id="concern_pending_001",
            action="confirm",
            reviewer_id="reviewer-1",
            timestamp="2026-07-10T02:00:00Z",
            expected_revision=0,
            before_commit=confirmation_hook,
        )
        if winner == "confirmation":
            barrier.wait()
        return result

    def finalize() -> dict[str, object]:
        result = finalize_confirmed_report(
            run_dir=run_dir,
            paper_id="demo",
            expected_confirmation_revision=0,
            override_reason="Race test override.",
            before_pointer_commit=finalize_hook,
        )
        if winner == "finalize":
            barrier.wait()
        return result

    with ThreadPoolExecutor(max_workers=2) as executor:
        confirmation_future = executor.submit(confirm)
        finalize_future = executor.submit(finalize)
        confirmation_result = confirmation_future.result(timeout=5)
        finalize_result = finalize_future.result(timeout=5)

    pointer_path = out_dir / "current_final_report.json"
    if winner == "finalize":
        assert finalize_result["status"] == "ok"
        assert confirmation_result["status"] == "revision_conflict"
        manifest = json.loads(Path(finalize_result["manifest_path"]).read_text(encoding="utf-8"))
        assert manifest["confirmation_revision"] == 0
        assert pointer_path.exists()
    else:
        assert confirmation_result["status"] == "ok"
        assert finalize_result["status"] == "revision_conflict"
        assert not pointer_path.exists()
    if pointer_path.exists():
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        assert (out_dir / pointer["manifest_path"]).exists()


def test_confirmation_cli_applies_decision(tmp_path: Path, capsys) -> None:
    run_dir = tmp_path / "run"
    out_dir = _seed_peerassist_stage(run_dir)

    exit_code = confirmation_cli_main(
        [
            "--run-dir",
            str(run_dir),
            "--paper-id",
            "demo",
            "--concern-id",
            "concern_pending_001",
            "--action",
            "confirm",
            "--reviewer-id",
            "reviewer-1",
            "--timestamp",
            "2026-07-10T00:00:00Z",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["actions_count"] == 1
    assert json.loads((out_dir / "peerassist_report.json").read_text(encoding="utf-8"))["confirmed_count"] == 1


def test_apply_confirmation_decision_rejects_confirming_item_without_evidence(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    out_dir = _seed_peerassist_stage(run_dir)
    payload = json.loads((out_dir / "peerassist_concerns.json").read_text(encoding="utf-8"))
    payload["concerns"][0]["evidence_ids"] = []
    write_json_file(out_dir / "peerassist_concerns.json", payload)

    with pytest.raises(ValueError, match=r"confirmed concern .* lacks evidence"):
        apply_confirmation_decision(
            run_dir=run_dir,
            paper_id="demo",
            concern_id="concern_pending_001",
            action="confirm",
            reviewer_id="reviewer-1",
            timestamp="2026-07-10T00:00:00Z",
        )
    confirmations = json.loads((out_dir / "human_confirmations.json").read_text(encoding="utf-8"))
    assert confirmations["actions"] == []


def test_peerassist_confirm_console_script_is_registered() -> None:
    payload = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert payload["project"]["scripts"]["peerassist-confirm"] == "peerassist.confirmation_cli:main"

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from common.pipeline_context import init_full_pipeline_context, peerassist_stage_dir, write_json_file
from peerassist.confirmation_workflow import apply_confirmation_decision, load_confirmation_state
from peerassist.confirmation_cli import main as confirmation_cli_main
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
    report = json.loads((out_dir / "peerassist_report.json").read_text(encoding="utf-8"))
    assert report["confirmed_count"] == 1
    assert report["pending_count"] == 0
    assert "Please clarify the denominator used for this percentage." in (
        out_dir / "peerassist_report.md"
    ).read_text(encoding="utf-8")
    assert result["report_json"] == str(out_dir / "peerassist_report.json")


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

    with pytest.raises(ValueError, match="confirmed concern .* lacks evidence"):
        apply_confirmation_decision(
            run_dir=run_dir,
            paper_id="demo",
            concern_id="concern_pending_001",
            action="confirm",
            reviewer_id="reviewer-1",
            timestamp="2026-07-10T00:00:00Z",
        )


def test_peerassist_confirm_console_script_is_registered() -> None:
    payload = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert payload["project"]["scripts"]["peerassist-confirm"] == "peerassist.confirmation_cli:main"

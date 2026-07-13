from __future__ import annotations

import json
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from common.pipeline_context import init_full_pipeline_context, peerassist_stage_dir, write_json_file
from peerassist.concerns import reconcile_finding_revisions
from peerassist.confirmation_cli import main as confirmation_cli_main
from peerassist.confirmation_workflow import (
    apply_confirmation_decision,
    finalize_confirmed_report,
    load_confirmation_state,
)
from peerassist.confirmations import apply_confirmations, reconcile_citation_confirmations
from schemas.citation import CitationAudit
from schemas.peerassist import Concern, ConcernLevel, ConcernStatus, HumanConfirmationAction


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


def _append_concern(out_dir: Path, concern: Concern) -> None:
    concerns_payload = json.loads((out_dir / "peerassist_concerns.json").read_text(encoding="utf-8"))
    concerns_payload["concerns"].append(concern.model_dump(mode="json"))
    write_json_file(out_dir / "peerassist_concerns.json", concerns_payload)
    bundle = json.loads((out_dir / "confirmation_bundle.json").read_text(encoding="utf-8"))
    row = {
        **concern.model_dump(mode="json"),
        "evidence": [
            {"id": evidence_id, "locator": f"locator:{evidence_id}"}
            for evidence_id in concern.evidence_ids
        ],
    }
    bundle["groups"][concern.status.value].append(row)
    bundle["counts_by_status"][concern.status.value] += 1
    write_json_file(out_dir / "confirmation_bundle.json", bundle)


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


def test_finding_reconciliation_increments_revision_for_substantive_change() -> None:
    previous = Concern(
        id="concern_stats",
        level=ConcernLevel.CLARIFICATION_NEEDED,
        category="statistics",
        title="Check the denominator",
        evidence_ids=["E1"],
        source_agent_ids=["statistics_agent"],
        metadata={
            "issue_anchor": "percentage-check-1",
            "finding_check_type": "percentage_consistency",
            "finding_semantic_key": "denominator_mismatch",
            "producer_version": "v1",
        },
    )
    candidate = Concern(
        id="concern_stats_regenerated",
        level=ConcernLevel.MAJOR_CONCERN,
        category="statistics",
        title="Check the denominator",
        evidence_ids=["E2"],
        source_agent_ids=["statistics_agent"],
        metadata={
            "issue_anchor": "percentage-check-1",
            "finding_check_type": "percentage_consistency",
            "finding_semantic_key": "denominator_mismatch",
            "producer_version": "v2",
        },
    )

    reconciled = reconcile_finding_revisions([previous], [candidate])[0]

    assert reconciled.finding_lineage_id == previous.finding_lineage_id
    assert reconciled.finding_id != previous.finding_id
    assert reconciled.revision == previous.revision + 1
    assert previous.finding_id in reconciled.supersedes


def test_finding_reconciliation_keeps_identity_for_display_only_wording_change() -> None:
    previous = Concern(
        id="concern_method",
        level=ConcernLevel.CLARIFICATION_NEEDED,
        category="methodology",
        title="Clarify the method",
        evidence_ids=["E1"],
        impact="Readers need one detail.",
        author_action="Add the missing detail.",
        source_agent_ids=["methodology_agent"],
        metadata={
            "issue_anchor": "method-check-1",
            "finding_check_type": "method_completeness",
            "finding_semantic_key": "missing_method_detail",
            "producer_version": "v1",
        },
    )
    candidate = previous.model_copy(
        update={
            "id": "concern_method_regenerated",
            "title": "Please clarify this method detail",
            "impact": "The detail is needed for interpretation.",
            "author_action": "State the detail explicitly.",
            "finding_lineage_id": "",
            "finding_id": "",
        }
    )
    candidate = Concern.model_validate(candidate.model_dump(mode="python"))

    reconciled = reconcile_finding_revisions([previous], [candidate])[0]

    assert candidate.finding_id == previous.finding_id
    assert reconciled.revision == previous.revision
    assert reconciled.display_revision == previous.display_revision + 1
    assert reconciled.supersedes == previous.supersedes


def test_bound_confirmation_action_uses_finding_triple_before_concern_id() -> None:
    concern = Concern(
        id="concern_new_id",
        level=ConcernLevel.CLARIFICATION_NEEDED,
        category="statistics",
        title="Check result",
        evidence_ids=["E1"],
        metadata={"issue_anchor": "stats-1"},
    )
    action = HumanConfirmationAction(
        concern_id="concern_old_id",
        finding_lineage_id=concern.finding_lineage_id,
        finding_id=concern.finding_id,
        revision=concern.revision,
        action="confirm",
        timestamp="2026-07-10T00:00:00Z",
    )

    applied = apply_confirmations([concern], [action])[0]

    assert applied.status is ConcernStatus.CONFIRMED


def test_partial_finding_binding_never_falls_back_to_legacy_concern_id() -> None:
    concern = Concern(
        id="concern_partial",
        level=ConcernLevel.CLARIFICATION_NEEDED,
        category="statistics",
        title="Check result",
        evidence_ids=["E1"],
        metadata={"issue_anchor": "stats-partial"},
    )
    partial = HumanConfirmationAction(
        concern_id=concern.id,
        finding_lineage_id=concern.finding_lineage_id,
        action="confirm",
        timestamp="2026-07-10T00:00:00Z",
    )
    empty_audit = CitationAudit(
        schema_version="peerassist.citation_audit.v1",
        paper_id="paper",
        parse_version="v1",
    )

    applied = apply_confirmations([concern], [partial])[0]
    reconciliation = reconcile_citation_confirmations([concern], [partial], empty_audit)

    assert applied.status is ConcernStatus.PENDING_HUMAN_CONFIRMATION
    assert reconciliation.replayable_actions == []
    assert reconciliation.unresolved_historical_actions == [partial]


def test_legacy_action_is_not_replayed_after_finding_revision_changes() -> None:
    concern = Concern(
        id="concern_legacy",
        finding_lineage_id="fln_legacy",
        finding_id="fnd_new",
        revision=2,
        supersedes=["fnd_old"],
        level=ConcernLevel.CLARIFICATION_NEEDED,
        category="statistics",
        title="Changed result concern",
        evidence_ids=["E2"],
    )
    legacy = HumanConfirmationAction(
        concern_id=concern.id,
        action="confirm",
        timestamp="2026-07-10T00:00:00Z",
    )
    empty_audit = CitationAudit(
        schema_version="peerassist.citation_audit.v1",
        paper_id="paper",
        parse_version="v1",
    )

    applied = apply_confirmations([concern], [legacy])[0]
    reconciliation = reconcile_citation_confirmations([concern], [legacy], empty_audit)

    assert applied.status is ConcernStatus.PENDING_HUMAN_CONFIRMATION
    assert reconciliation.replayable_actions == []
    assert reconciliation.unresolved_historical_actions == [legacy]
    assert reconciliation.concern_ids_needing_reconciliation == [concern.id]


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


def test_finalize_same_revision_does_not_reuse_manifest_after_core_snapshot_change(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    out_dir = _seed_peerassist_stage(run_dir)
    first = finalize_confirmed_report(
        run_dir=run_dir,
        paper_id="demo",
        expected_confirmation_revision=0,
        override_reason="Initial reviewer override.",
    )
    pointer_before = (out_dir / "current_final_report.json").read_text(encoding="utf-8")
    _append_concern(
        out_dir,
        Concern(
            id="concern_new_core",
            level=ConcernLevel.MAJOR_CONCERN,
            category="methodology",
            title="New core method concern",
            evidence_ids=["P02-L001"],
            impact="Changes the core validity assessment.",
            author_action="Clarify the core method.",
            source_agent_ids=["methodology_agent"],
            metadata={"issue_anchor": "method-core-1", "producer_version": "v1"},
        ),
    )

    blocked = finalize_confirmed_report(
        run_dir=run_dir,
        paper_id="demo",
        expected_confirmation_revision=0,
    )

    assert first["status"] == "ok"
    assert blocked["status"] == "blocked"
    assert blocked["error_code"] == "unresolved_core_findings"
    assert blocked.get("idempotent") is not True
    assert (out_dir / "current_final_report.json").read_text(encoding="utf-8") == pointer_before


def test_finalize_same_revision_returns_snapshot_conflict_for_changed_noncore_finding(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    out_dir = _seed_peerassist_stage(run_dir)
    first = finalize_confirmed_report(
        run_dir=run_dir,
        paper_id="demo",
        expected_confirmation_revision=0,
        override_reason="Initial reviewer override.",
    )
    payload = json.loads((out_dir / "peerassist_concerns.json").read_text(encoding="utf-8"))
    payload["concerns"][0]["evidence_ids"] = ["P01-L999"]
    payload["concerns"][0].pop("finding_id", None)
    write_json_file(out_dir / "peerassist_concerns.json", payload)

    conflict = finalize_confirmed_report(
        run_dir=run_dir,
        paper_id="demo",
        expected_confirmation_revision=0,
        override_reason="Do not silently reuse the old snapshot.",
    )

    assert first["status"] == "ok"
    assert conflict["status"] == "revision_conflict"
    assert conflict["error_code"] == "finding_snapshot_conflict"


def test_final_manifest_freezes_confirmation_actions_and_all_finding_revisions(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    out_dir = _seed_peerassist_stage(run_dir)
    second_concern = Concern(
        id="concern_pending_002",
        level=ConcernLevel.MINOR_CONCERN,
        category="writing",
        title="Secondary wording concern",
        evidence_ids=["P02-L001"],
        impact="May reduce clarity.",
        author_action="Clarify the wording.",
        source_agent_ids=["structure_agent"],
        metadata={"issue_anchor": "wording-2", "producer_version": "v1"},
    )
    _append_concern(out_dir, second_concern)
    first_action = apply_confirmation_decision(
        run_dir=run_dir,
        paper_id="demo",
        concern_id="concern_pending_001",
        action="rewrite",
        reviewer_id="reviewer-1",
        timestamp="2026-07-10T03:00:00Z",
        new_text="State the exact denominator.",
        expected_revision=0,
    )
    second_action = apply_confirmation_decision(
        run_dir=run_dir,
        paper_id="demo",
        concern_id=second_concern.id,
        action="delete",
        reviewer_id="reviewer-1",
        timestamp="2026-07-10T03:01:00Z",
        reason="Not material after manual review.",
        expected_revision=first_action["confirmation_revision"],
    )

    finalized = finalize_confirmed_report(
        run_dir=run_dir,
        paper_id="demo",
        expected_confirmation_revision=second_action["confirmation_revision"],
    )

    manifest = json.loads(Path(finalized["manifest_path"]).read_text(encoding="utf-8"))
    assert [row["action"] for row in manifest["confirmation_actions"]] == ["rewrite", "delete"]
    revisions = {row["finding_id"]: row for row in manifest["finding_revisions"]}
    assert revisions[first_action["action"]["finding_id"]]["status"] == "rewritten"
    assert revisions[second_action["action"]["finding_id"]]["status"] == "deleted"
    assert manifest["confirmation_actions_sha256"]
    assert manifest["finding_snapshot_sha256"]


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

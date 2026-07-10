from __future__ import annotations

import json
import threading
import tomllib
import urllib.request
from pathlib import Path

from common.pipeline_context import init_full_pipeline_context, peerassist_stage_dir, write_json_file
from peerassist.confirmation_server import create_confirmation_server, render_confirmation_page
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
    concern_row = concern.model_dump(mode="json")
    write_json_file(
        out_dir / "peerassist_concerns.json",
        {"schema_version": "peerassist.concerns.v1", "mode": "fast", "concerns": [concern_row]},
    )
    write_json_file(
        out_dir / "confirmation_bundle.json",
        {
            "schema_version": "peerassist.confirmation_bundle.v1",
            "counts_by_status": {"pending_human_confirmation": 1},
            "groups": {
                "pending_human_confirmation": [
                    {**concern_row, "evidence": [{"id": "P01-L001", "locator": "p.1 line 1"}]}
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
            "items": [
                {
                    **concern_row,
                    "position": 1,
                    "evidence": [{"id": "P01-L001", "locator": "p.1 line 1"}],
                    "allowed_actions": ["confirm", "rewrite", "downgrade", "delete", "mark_pending"],
                }
            ],
        },
    )
    write_json_file(
        out_dir / "human_confirmations.json",
        {"schema_version": "peerassist.human_confirmations.v1", "actions": []},
    )
    write_json_file(
        out_dir / "agent_results.json",
        {
            "schema_version": "peerassist.agent_results.v1",
            "mode": "fast",
            "results": [
                {
                    "agent_id": "statistics_agent",
                    "status": "completed",
                    "drafts": [concern_row],
                    "warnings": [],
                    "metadata": {"lead_count": 1},
                },
                {
                    "agent_id": "defense_agent",
                    "status": "completed",
                    "drafts": [],
                    "warnings": [],
                    "metadata": {"pressure_tests": ["check_001: pressure-test against denominator"]},
                },
            ],
        },
    )
    write_json_file(
        out_dir / "capability_invocations.json",
        {
            "schema_version": "peerassist.capability_invocations.v1",
            "mode": "fast",
            "results": [
                {
                    "task_id": "demo",
                    "call_id": "percentage_consistency_check",
                    "agent_id": "statistics_agent",
                    "capability_name": "percentage_consistency_check",
                    "source": "builtin",
                    "status": "completed",
                    "artifact_ids": ["deterministic_checks"],
                    "attempts": 1,
                    "duration_ms": 42,
                    "evidence_ids": ["P01-L001"],
                }
            ],
        },
    )
    (out_dir / "tool_trace.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "task_id": "demo",
                        "call_id": "percentage_consistency_check",
                        "agent_id": "statistics_agent",
                        "source": "builtin",
                        "tool": "percentage_consistency_check",
                        "status": "queued",
                        "ts": "2026-07-10T00:00:00Z",
                        "input_summary": "run deterministic percentage consistency checks",
                        "evidence_ids": ["P01-L001"],
                    }
                ),
                json.dumps(
                    {
                        "task_id": "demo",
                        "call_id": "percentage_consistency_check",
                        "agent_id": "statistics_agent",
                        "source": "builtin",
                        "tool": "percentage_consistency_check",
                        "status": "completed",
                        "ts": "2026-07-10T00:00:01Z",
                        "output_summary": "1 check completed",
                        "artifact_ids": ["deterministic_checks"],
                        "duration_ms": 42,
                        "evidence_ids": ["P01-L001"],
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return out_dir


def test_render_confirmation_page_contains_evidence_and_actions(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _seed_peerassist_stage(run_dir)

    html = render_confirmation_page(run_dir=run_dir, paper_id="demo")

    assert "PeerAssist Review Console" in html
    assert "data-peerassist-agent-console" in html
    assert 'data-panel="review-queue"' in html
    assert 'data-panel="tool-trace"' in html
    assert 'data-panel="human-confirmation"' in html
    assert 'data-panel="stream-console"' in html
    assert "data-agent-runtime-strip" in html
    assert "stream-chip" in html
    assert "agent-timeline" in html
    assert 'data-stream-state="connecting"' in html
    assert "new EventSource('/api/events')" in html
    assert "Statistics Agent" in html
    assert "Reported percentage needs clarification" in html
    assert "p.1 line 1" in html
    assert "data-action=\"confirm\"" in html
    assert "data-action=\"rewrite\"" in html


def test_confirmation_server_state_and_decision_endpoints(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    out_dir = _seed_peerassist_stage(run_dir)
    server = create_confirmation_server(
        run_dir=run_dir,
        paper_id="demo",
        host="127.0.0.1",
        port=0,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(f"{base_url}/api/state", timeout=5) as response:
            state = json.loads(response.read().decode("utf-8"))
        assert state["pending_count"] == 1
        assert state["runtime"]["mode"] == "fast"
        assert state["agent_runs"][0]["agent_id"] == "statistics_agent"
        assert state["agent_runs"][0]["draft_count"] == 1
        assert state["tool_trace"]["counts_by_status"]["completed"] == 1
        assert state["tool_trace"]["latest_status_by_call"]["percentage_consistency_check"] == "completed"
        assert state["capability_invocations"][0]["capability_name"] == "percentage_consistency_check"

        with urllib.request.urlopen(f"{base_url}/api/events", timeout=5) as response:
            event_text = response.read().decode("utf-8")
            content_type = response.headers["Content-Type"]
        assert content_type.startswith("text/event-stream")
        assert "event: state" in event_text
        assert '"schema_version": "peerassist.confirmation_state.v1"' in event_text

        payload = json.dumps(
            {
                "concern_id": "concern_pending_001",
                "action": "confirm",
                "reviewer_id": "reviewer-1",
                "timestamp": "2026-07-10T00:00:00Z",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{base_url}/api/decision",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            result = json.loads(response.read().decode("utf-8"))

        assert result["actions_count"] == 1
        report = json.loads((out_dir / "peerassist_report.json").read_text(encoding="utf-8"))
        assert report["confirmed_count"] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_peerassist_confirm_server_console_script_is_registered() -> None:
    payload = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert (
        payload["project"]["scripts"]["peerassist-confirm-server"]
        == "peerassist.confirmation_server:main"
    )


def test_confirmation_server_module_has_python_m_entrypoint() -> None:
    source = Path("src/peerassist/confirmation_server.py").read_text(encoding="utf-8")

    assert 'if __name__ == "__main__"' in source
    assert "raise SystemExit(main())" in source

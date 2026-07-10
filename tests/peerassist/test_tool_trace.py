from __future__ import annotations

import json

from peerassist.tool_trace import ToolTraceRecorder
from schemas.peerassist import ToolTraceStatus


def test_tool_trace_recorder_writes_lifecycle_events(tmp_path) -> None:
    trace_path = tmp_path / "tool_trace.jsonl"
    recorder = ToolTraceRecorder(trace_path)

    recorder.record(
        task_id="task",
        call_id="call",
        agent_id="statistics_agent",
        source="builtin",
        tool="percentage_check",
        status=ToolTraceStatus.STARTED,
        input_summary="check percentages",
    )
    recorder.record(
        task_id="task",
        call_id="call",
        agent_id="statistics_agent",
        source="builtin",
        tool="percentage_check",
        status=ToolTraceStatus.COMPLETED,
        output_summary="1 lead",
        artifact_ids=["check_percentage_001"],
        duration_ms=12,
    )

    rows = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert [row["status"] for row in rows] == ["started", "completed"]
    assert rows[1]["artifact_ids"] == ["check_percentage_001"]
    assert rows[1]["duration_ms"] == 12

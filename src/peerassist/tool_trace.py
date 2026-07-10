"""Append-only PeerAssist tool trace events."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from schemas.peerassist import ToolTraceEvent, ToolTraceStatus


class ToolTraceRecorder:
    def __init__(self, path: Path) -> None:
        self.path = path

    def record(
        self,
        *,
        task_id: str,
        call_id: str,
        source: str,
        tool: str,
        status: ToolTraceStatus,
        agent_id: str = "",
        input_summary: str = "",
        output_summary: str = "",
        artifact_ids: list[str] | None = None,
        duration_ms: int | None = None,
        error_code: str = "",
        evidence_ids: list[str] | None = None,
    ) -> ToolTraceEvent:
        event = ToolTraceEvent(
            task_id=task_id,
            call_id=call_id,
            agent_id=agent_id,
            source=source,
            tool=tool,
            status=status,
            ts=datetime.now(UTC).isoformat(),
            input_summary=input_summary,
            output_summary=output_summary,
            artifact_ids=list(artifact_ids or []),
            duration_ms=duration_ms,
            error_code=error_code,
            evidence_ids=list(evidence_ids or []),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.model_dump(mode="json"), ensure_ascii=False) + "\n")
        return event

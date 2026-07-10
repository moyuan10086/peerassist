"""Audited PeerAssist capability invocation boundary."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from time import monotonic
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from peerassist.capabilities import CapabilityRegistry, CapabilitySpec
from peerassist.tool_trace import ToolTraceRecorder
from schemas.peerassist import ToolTraceStatus

CapabilityHandler = Callable[[dict[str, Any]], Mapping[str, Any]]


class CapabilityInvocationRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_id: str
    call_id: str
    agent_id: str
    capability_name: str
    input_summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    approved: bool = False
    timeout_ms: int | None = None
    max_retries: int = 0


class CapabilityInvocationResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_id: str
    call_id: str
    agent_id: str
    capability_name: str
    source: str = ""
    status: ToolTraceStatus
    output: dict[str, Any] = Field(default_factory=dict)
    artifact_ids: list[str] = Field(default_factory=list)
    attempts: int = 0
    duration_ms: int | None = None
    error_code: str = ""
    error_message: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class CapabilityInvoker:
    def __init__(
        self,
        *,
        registry: CapabilityRegistry,
        trace: ToolTraceRecorder,
        handlers: dict[str, CapabilityHandler] | None = None,
    ) -> None:
        self.registry = registry
        self.trace = trace
        self.handlers = dict(handlers or {})

    def invoke(self, request: CapabilityInvocationRequest) -> CapabilityInvocationResult:
        started = monotonic()
        capability = self.registry.find(request.capability_name)
        source = capability.source.value if capability else ""
        self.trace.record(
            task_id=request.task_id,
            call_id=request.call_id,
            agent_id=request.agent_id,
            source=source,
            tool=request.capability_name,
            status=ToolTraceStatus.QUEUED,
            input_summary=request.input_summary,
            evidence_ids=request.evidence_ids,
        )

        if capability is None:
            return self._fail(
                request,
                capability=None,
                started=started,
                error_code="capability_not_found",
                error_message=f"Unknown capability: {request.capability_name}",
            )

        if capability.approval_required and not request.approved:
            duration_ms = _elapsed_ms(started)
            self.trace.record(
                task_id=request.task_id,
                call_id=request.call_id,
                agent_id=request.agent_id,
                source=source,
                tool=request.capability_name,
                status=ToolTraceStatus.APPROVAL_REQUIRED,
                input_summary=request.input_summary,
                output_summary="Human approval required before executing this capability.",
                duration_ms=duration_ms,
                error_code="approval_required",
                evidence_ids=request.evidence_ids,
            )
            return CapabilityInvocationResult(
                task_id=request.task_id,
                call_id=request.call_id,
                agent_id=request.agent_id,
                capability_name=request.capability_name,
                source=source,
                status=ToolTraceStatus.APPROVAL_REQUIRED,
                attempts=0,
                duration_ms=duration_ms,
                error_code="approval_required",
                error_message="Human approval required before executing this capability.",
                evidence_ids=list(request.evidence_ids),
            )

        handler = self.handlers.get(request.capability_name)
        if handler is None:
            return self._fail(
                request,
                capability=capability,
                started=started,
                error_code="executor_unavailable",
                error_message=f"No executor registered for capability: {request.capability_name}",
            )

        attempts = 0
        max_attempts = max(1, request.max_retries + 1)
        while attempts < max_attempts:
            attempts += 1
            self.trace.record(
                task_id=request.task_id,
                call_id=request.call_id,
                agent_id=request.agent_id,
                source=source,
                tool=request.capability_name,
                status=ToolTraceStatus.STARTED,
                input_summary=request.input_summary,
                evidence_ids=request.evidence_ids,
            )
            try:
                output = handler(dict(request.payload))
            except TimeoutError as exc:
                if attempts < max_attempts:
                    self._progress_retry(request, capability, attempts, "timeout")
                    continue
                return self._fail(
                    request,
                    capability=capability,
                    started=started,
                    attempts=attempts,
                    error_code="timeout",
                    error_message=str(exc) or "Capability invocation timed out.",
                )
            except Exception as exc:  # pragma: no cover - defensive path is still fail-closed.
                if attempts < max_attempts:
                    self._progress_retry(request, capability, attempts, exc.__class__.__name__)
                    continue
                return self._fail(
                    request,
                    capability=capability,
                    started=started,
                    attempts=attempts,
                    error_code="executor_failed",
                    error_message=str(exc),
                )

            if not isinstance(output, Mapping):
                return self._fail(
                    request,
                    capability=capability,
                    started=started,
                    attempts=attempts,
                    error_code="schema_validation_failed",
                    error_message="Capability output must be a JSON object.",
                )

            normalized_output = dict(output)
            artifact_ids = _artifact_ids(normalized_output)
            duration_ms = _elapsed_ms(started)
            self.trace.record(
                task_id=request.task_id,
                call_id=request.call_id,
                agent_id=request.agent_id,
                source=source,
                tool=request.capability_name,
                status=ToolTraceStatus.COMPLETED,
                output_summary=capability.output_summary,
                artifact_ids=artifact_ids,
                duration_ms=duration_ms,
                evidence_ids=request.evidence_ids,
            )
            return CapabilityInvocationResult(
                task_id=request.task_id,
                call_id=request.call_id,
                agent_id=request.agent_id,
                capability_name=request.capability_name,
                source=source,
                status=ToolTraceStatus.COMPLETED,
                output=normalized_output,
                artifact_ids=artifact_ids,
                attempts=attempts,
                duration_ms=duration_ms,
                evidence_ids=list(request.evidence_ids),
            )

        return self._fail(
            request,
            capability=capability,
            started=started,
            attempts=attempts,
            error_code="executor_failed",
            error_message="Capability invocation did not produce a result.",
        )

    def _progress_retry(
        self, request: CapabilityInvocationRequest, capability: CapabilitySpec, attempts: int, reason: str
    ) -> None:
        self.trace.record(
            task_id=request.task_id,
            call_id=request.call_id,
            agent_id=request.agent_id,
            source=capability.source.value,
            tool=request.capability_name,
            status=ToolTraceStatus.PROGRESS,
            output_summary=f"retrying after attempt {attempts}: {reason}",
            evidence_ids=request.evidence_ids,
        )

    def _fail(
        self,
        request: CapabilityInvocationRequest,
        *,
        capability: CapabilitySpec | None,
        started: float,
        error_code: str,
        error_message: str,
        attempts: int = 0,
    ) -> CapabilityInvocationResult:
        source = capability.source.value if capability else ""
        duration_ms = _elapsed_ms(started)
        self.trace.record(
            task_id=request.task_id,
            call_id=request.call_id,
            agent_id=request.agent_id,
            source=source,
            tool=request.capability_name,
            status=ToolTraceStatus.FAILED,
            output_summary=error_message,
            duration_ms=duration_ms,
            error_code=error_code,
            evidence_ids=request.evidence_ids,
        )
        return CapabilityInvocationResult(
            task_id=request.task_id,
            call_id=request.call_id,
            agent_id=request.agent_id,
            capability_name=request.capability_name,
            source=source,
            status=ToolTraceStatus.FAILED,
            attempts=attempts,
            duration_ms=duration_ms,
            error_code=error_code,
            error_message=error_message,
            evidence_ids=list(request.evidence_ids),
        )


def _elapsed_ms(started: float) -> int:
    return max(0, int((monotonic() - started) * 1000))


def _artifact_ids(output: dict[str, Any]) -> list[str]:
    raw = output.get("artifact_ids", [])
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw]

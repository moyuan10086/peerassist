"""Liveness and dependency readiness routes."""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1", tags=["system"])


class HealthView(BaseModel):
    status: Literal["ok"]


class ReadinessView(BaseModel):
    status: Literal["ready", "unavailable"]
    dependencies: dict[str, Literal["ready", "unavailable"]]


@router.get("/health", operation_id="v1_health", response_model=HealthView)
async def health() -> HealthView:
    return HealthView(status="ok")


@router.get(
    "/ready",
    operation_id="v1_ready",
    responses={503: {"model": ReadinessView}},
    response_model=ReadinessView,
)
async def readiness(request: Request) -> ReadinessView | JSONResponse:
    dependencies = request.app.state.dependencies
    probes = request.app.state.readiness_probe_slots
    tasks = [
        asyncio.create_task(probe.run(dependencies.readiness_check_timeout_seconds))
        for probe in probes
    ]
    done, pending = await asyncio.wait(
        tasks,
        timeout=dependencies.readiness_overall_timeout_seconds,
    )
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    statuses: dict[str, Literal["ready", "unavailable"]] = {
        probe.name: "ready" if task in done and _available(task) else "unavailable"
        for probe, task in zip(probes, tasks, strict=True)
    }
    ready = all(value == "ready" for value in statuses.values())
    body = ReadinessView(
        status="ready" if ready else "unavailable",
        dependencies=statuses,
    )
    if ready:
        return body
    return JSONResponse(status_code=503, content=body.model_dump())


def _available(task: asyncio.Task[bool]) -> bool:
    try:
        return bool(task.result())
    except BaseException:
        return False

"""Liveness and dependency readiness routes."""

from __future__ import annotations

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
    statuses: dict[str, Literal["ready", "unavailable"]] = {}
    for check in request.app.state.dependencies.readiness_checks:
        try:
            available = await check.check()
        except Exception:
            available = False
        statuses[check.name] = "ready" if available else "unavailable"
    ready = all(value == "ready" for value in statuses.values())
    body = ReadinessView(
        status="ready" if ready else "unavailable",
        dependencies=statuses,
    )
    if ready:
        return body
    return JSONResponse(status_code=503, content=body.model_dump())

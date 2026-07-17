"""Typed boundary contracts used by the FastAPI composition root."""

from __future__ import annotations

from typing import Protocol

from fastapi import Request


class ReadinessCheck(Protocol):
    """A provider-neutral dependency health probe."""

    name: str

    async def check(self) -> bool: ...


class LifecycleResource(Protocol):
    """A provider-neutral resource managed by the application lifespan."""

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


def request_id(request: Request) -> str:
    """Return the validated request ID assigned by boundary middleware."""

    return request.state.request_id

"""Typed boundary contracts used by the FastAPI composition root."""

from __future__ import annotations

import asyncio
import threading
from typing import Protocol

from fastapi import Request


class ReadinessCheck(Protocol):
    """A thread-safe probe callable from an independent event loop."""

    name: str

    async def check(self) -> bool: ...


class LifecycleResource(Protocol):
    """A resource whose stop method is safe on an independent event loop."""

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class IsolatedReadinessProbe:
    """Run at most one provider probe in a disposable daemon thread."""

    def __init__(self, check: ReadinessCheck) -> None:
        self.name = check.name
        self._check = check
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._completed = threading.Event()
        self._available = False

    async def run(self, timeout: float) -> bool:
        completed = self._begin()
        deadline = asyncio.get_running_loop().time() + timeout
        while not completed.is_set():
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(0.005, remaining))
        with self._lock:
            return self._available if completed is self._completed else False

    def _begin(self) -> threading.Event:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self._completed
            completed = threading.Event()
            self._completed = completed
            self._available = False
            self._thread = threading.Thread(
                target=self._execute,
                args=(completed,),
                name=f"readiness-{self.name}",
                daemon=True,
            )
            self._thread.start()
            return completed

    def _execute(self, completed: threading.Event) -> None:
        try:
            available = bool(asyncio.run(self._check.check()))
        except BaseException:
            available = False
        with self._lock:
            if completed is self._completed:
                self._available = available
        completed.set()


def request_id(request: Request) -> str:
    """Return the validated request ID assigned by boundary middleware."""

    return request.state.request_id

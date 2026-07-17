"""Fixed router registry for deterministic application composition."""

from fastapi import APIRouter

from .auth import router as auth_router
from .system import router as system_router


def platform_routers() -> tuple[APIRouter, ...]:
    """Return every public platform router in a deliberate stable order."""

    return system_router, auth_router


__all__ = ["platform_routers"]

"""Fixed router registry for deterministic application composition."""

from fastapi import APIRouter

from .artifacts import router as artifacts_router
from .auth import router as auth_router
from .organizations import router as organizations_router
from .papers import router as papers_router
from .projects import router as projects_router
from .review_jobs import router as review_jobs_router
from .system import router as system_router


def platform_routers() -> tuple[APIRouter, ...]:
    """Return every public platform router in a deliberate stable order."""

    return (
        system_router,
        auth_router,
        organizations_router,
        projects_router,
        papers_router,
        review_jobs_router,
        artifacts_router,
    )


__all__ = ["platform_routers"]

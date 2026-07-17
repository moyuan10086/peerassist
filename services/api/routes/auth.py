"""Stable authentication placeholders for the later OIDC milestone."""

from fastapi import APIRouter

from ..errors import NotConfiguredError

router = APIRouter(prefix="/api/v1", tags=["authentication"])


def _not_configured() -> None:
    raise NotConfiguredError


@router.get("/auth/login", operation_id="v1_auth_login")
async def login() -> None:
    _not_configured()


@router.get("/auth/callback", operation_id="v1_auth_callback")
async def callback() -> None:
    _not_configured()


@router.get("/auth/session", operation_id="v1_auth_session")
async def session() -> None:
    _not_configured()


@router.post("/auth/logout", operation_id="v1_auth_logout")
async def logout() -> None:
    _not_configured()


@router.get("/me", operation_id="v1_me")
async def me() -> None:
    _not_configured()

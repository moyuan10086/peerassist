"""OIDC browser login, session inspection, and logout routes."""

from urllib.parse import urlsplit

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import RedirectResponse

from peerassist.platform.errors import AuthenticationRequired

from ..dependencies import optional_request_actor, require_request_actor

router = APIRouter(prefix="/api/v1", tags=["authentication"])


@router.get("/auth/login", operation_id="v1_auth_login")
def login(request: Request, return_path: str = Query(default="/")) -> RedirectResponse:
    started = request.app.state.dependencies.session_service.begin(return_path)
    return RedirectResponse(started.authorization_url, status_code=307)


@router.get("/auth/callback", operation_id="v1_auth_callback")
def callback(request: Request, state: str, code: str) -> RedirectResponse:
    completed = request.app.state.dependencies.session_service.complete(state, code)
    response = RedirectResponse(completed.return_path, status_code=303)
    secure = urlsplit(request.app.state.settings.public_base_url).scheme == "https"
    response.set_cookie(
        "peerassist_session",
        completed.session_token,
        max_age=8 * 60 * 60,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        "peerassist_csrf",
        completed.csrf_token,
        max_age=8 * 60 * 60,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/auth/session", operation_id="v1_auth_session")
def session(request: Request) -> dict[str, object]:
    actor = optional_request_actor(request)
    if actor is None:
        return {"authenticated": False, "user": None}
    return {"authenticated": True, "user": _user_payload(request, actor)}


@router.post("/auth/logout", operation_id="v1_auth_logout")
def logout(
    request: Request,
    csrf_header: str = Header(alias="X-CSRF-Token", min_length=1, max_length=512),
) -> RedirectResponse:
    session_token = request.cookies.get("peerassist_session", "")
    csrf_cookie = request.cookies.get("peerassist_csrf", "")
    if not csrf_cookie or csrf_cookie != csrf_header:
        raise AuthenticationRequired()
    request.app.state.dependencies.session_service.logout(session_token, csrf_header)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("peerassist_session", path="/")
    response.delete_cookie("peerassist_csrf", path="/")
    return response


@router.get("/me", operation_id="v1_me")
def me(request: Request) -> dict[str, object]:
    return _user_payload(request, require_request_actor(request))


def _user_payload(request: Request, actor) -> dict[str, object]:
    with request.app.state.dependencies.uow_factory(actor) as uow:
        user = uow.users.get(actor.actor_id)
        if user is None:
            raise AuthenticationRequired()
        return {
            "id": str(user.id),
            "display_name": user.display_name,
            "status": user.status,
        }

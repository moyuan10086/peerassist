"""Authenticated model provider settings and discovery routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from peerassist.model_settings import (
    ModelSettingsConflict,
    ModelSettingsError,
    ModelSettingsInput,
    discover_models,
    load_model_settings,
    save_model_settings,
)
from peerassist.platform.models import Actor, Role
from services.api.dependencies import require_request_actor

router = APIRouter(tags=["model-settings"])
ModelSettingsActor = Annotated[Actor, Depends(require_request_actor)]


class ModelSettingsBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: str = ""
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    clear_api_key: bool = False
    expected_revision: int | None = None
    enabled: bool = True
    policy_version: str = "peerassist.model-policy.v1"

    def as_input(self) -> ModelSettingsInput:
        return ModelSettingsInput(
            provider=self.provider,
            base_url=self.base_url,
            model=self.model,
            api_key=self.api_key,
            clear_api_key=self.clear_api_key,
            expected_revision=self.expected_revision,
            enabled=self.enabled,
            policy_version=self.policy_version,
        )


def _empty_settings() -> dict[str, object]:
    return {
        "provider": "openai-compatible",
        "base_url": "",
        "model": "",
        "api_mode": "chat_completions",
        "api_key_configured": False,
        "api_key_hint": "",
        "revision": 0,
        "enabled": False,
        "policy_version": "peerassist.model-policy.v1",
        "configuration_id": "legacy",
    }


def _is_organization_admin(actor: Actor, request: Request) -> bool:
    with request.app.state.dependencies.uow_factory(actor) as uow:
        return any(
            membership.status == "active"
            and membership.revoked_at is None
            and membership.role is Role.ORGANIZATION_ADMIN
            for membership in uow.organizations.list_for_user(actor.actor_id)
        )


def _require_admin(actor: Actor, request: Request) -> JSONResponse | None:
    if _is_organization_admin(actor, request):
        return None
    # Treat this global resource as absent to avoid leaking settings existence.
    return _safe_error(request, "资源不存在。", status_code=404)


def _safe_error(request: Request, message: str, *, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": "model_settings_error",
                "message": message,
                "request_id": request.state.request_id,
                "details": {},
                "retryable": False,
            }
        },
    )


@router.get("/api/model-settings", include_in_schema=False, response_model=None)
@router.get(
    "/api/v1/model-settings",
    operation_id="v1_get_model_settings",
    response_model=None,
)
def get_model_settings(_actor: ModelSettingsActor, request: Request) -> dict[str, object] | JSONResponse:
    if not _is_organization_admin(_actor, request):
        return {"settings": {"api_key_configured": False, "enabled": False}}
    try:
        stored = load_model_settings()
    except ModelSettingsError as exc:
        return _safe_error(request, str(exc))
    return {"settings": stored.public_view() if stored is not None else _empty_settings()}


@router.post("/api/model-settings/discover", include_in_schema=False, response_model=None)
@router.post(
    "/api/v1/model-settings/discover",
    operation_id="v1_discover_model_settings",
    response_model=None,
)
def discover_model_settings(
    body: ModelSettingsBody,
    _actor: ModelSettingsActor,
    request: Request,
) -> dict[str, object] | JSONResponse:
    denied = _require_admin(_actor, request)
    if denied is not None:
        return denied
    try:
        return {"models": discover_models(body.as_input())}
    except ModelSettingsError as exc:
        return _safe_error(request, str(exc))
    except Exception:
        return _safe_error(request, "模型列表拉取失败。")


@router.post("/api/model-settings", include_in_schema=False, response_model=None)
@router.post(
    "/api/v1/model-settings",
    operation_id="v1_save_model_settings",
    response_model=None,
)
def save_model_settings_route(
    body: ModelSettingsBody,
    _actor: ModelSettingsActor,
    request: Request,
) -> dict[str, object] | JSONResponse:
    denied = _require_admin(_actor, request)
    if denied is not None:
        return denied
    try:
        stored = save_model_settings(body.as_input())
    except ModelSettingsConflict as exc:
        return _safe_error(request, str(exc), status_code=409)
    except ModelSettingsError as exc:
        return _safe_error(request, str(exc))
    except Exception:
        return _safe_error(request, "模型设置保存失败。")
    return {"settings": stored.public_view()}

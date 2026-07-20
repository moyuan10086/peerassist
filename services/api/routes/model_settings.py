"""Authenticated model provider settings and discovery routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from peerassist.model_settings import (
    ModelSettingsError,
    ModelSettingsInput,
    discover_models,
    load_model_settings,
    save_model_settings,
)
from peerassist.platform.models import Actor
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

    def as_input(self) -> ModelSettingsInput:
        return ModelSettingsInput(
            provider=self.provider,
            base_url=self.base_url,
            model=self.model,
            api_key=self.api_key,
            clear_api_key=self.clear_api_key,
        )


def _empty_settings() -> dict[str, object]:
    return {
        "provider": "openai-compatible",
        "base_url": "",
        "model": "",
        "api_mode": "chat_completions",
        "api_key_configured": False,
        "api_key_hint": "",
    }


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
    try:
        stored = save_model_settings(body.as_input())
    except ModelSettingsError as exc:
        return _safe_error(request, str(exc))
    except Exception:
        return _safe_error(request, "模型设置保存失败。")
    return {"settings": stored.public_view()}

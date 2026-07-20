"""Local, server-owned model provider settings and model discovery."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from llm.codex_auth import load_cached_codex_auth

_SCHEMA_VERSION = "peerassist.model_settings.v1"
_PROVIDERS = frozenset({"openai-compatible", "openai-codex"})
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_MAX_RESPONSE_BYTES = 1024 * 1024


class ModelSettingsError(ValueError):
    """Safe model settings or provider discovery failure."""


@dataclass(frozen=True)
class ModelSettingsInput:
    provider: str
    base_url: str
    model: str = ""
    api_key: str = field(default="", repr=False)
    clear_api_key: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _validate_provider(self.provider))
        object.__setattr__(self, "base_url", _validate_base_url(self.base_url))
        object.__setattr__(self, "model", _validate_model(self.model, required=False))
        api_key = str(self.api_key or "").strip()
        if len(api_key) > 4096:
            raise ModelSettingsError("API Key 长度无效。")
        object.__setattr__(self, "api_key", api_key)


@dataclass(frozen=True)
class StoredModelSettings:
    provider: str
    base_url: str
    model: str
    api_key: str = field(default="", repr=False)

    @property
    def api_mode(self) -> str:
        return "responses" if self.provider == "openai-codex" else "chat_completions"

    def public_view(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "api_mode": self.api_mode,
            "api_key_configured": bool(self.api_key) or _cached_key(self.provider) is not None,
            "api_key_hint": _key_hint(self.api_key),
        }


def model_settings_path() -> Path:
    override = os.getenv("PEERASSIST_MODEL_SETTINGS_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "peerassist" / "model-settings.json"


def load_model_settings(*, path: Path | None = None) -> StoredModelSettings | None:
    target = path or model_settings_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError, UnicodeError):
        raise ModelSettingsError("模型配置文件不可读。") from None
    if not isinstance(payload, dict) or payload.get("schema_version") != _SCHEMA_VERSION:
        raise ModelSettingsError("模型配置文件格式无效。")
    try:
        return StoredModelSettings(
            provider=_validate_provider(str(payload.get("provider") or "")),
            base_url=_validate_base_url(str(payload.get("base_url") or "")),
            model=_validate_model(str(payload.get("model") or ""), required=True),
            api_key=str(payload.get("api_key") or "").strip(),
        )
    except ModelSettingsError:
        raise
    except Exception:
        raise ModelSettingsError("模型配置文件格式无效。") from None


def save_model_settings(
    value: ModelSettingsInput,
    *,
    path: Path | None = None,
) -> StoredModelSettings:
    target = path or model_settings_path()
    existing = load_model_settings(path=target)
    api_key = "" if value.clear_api_key else value.api_key or (existing.api_key if existing else "")
    stored = StoredModelSettings(
        provider=value.provider,
        base_url=value.base_url,
        model=_validate_model(value.model, required=True),
        api_key=api_key,
    )
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "provider": stored.provider,
        "base_url": stored.base_url,
        "model": stored.model,
        "api_key": stored.api_key,
    }
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    file_mode = _settings_file_mode()
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, file_mode)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, file_mode)
        os.replace(temporary, target)
        os.chmod(target, file_mode)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return stored


def _settings_file_mode() -> int:
    value = os.getenv("PEERASSIST_MODEL_SETTINGS_FILE_MODE", "0600").strip()
    try:
        mode = int(value, 8)
    except ValueError:
        return 0o600
    return mode if mode in {0o600, 0o640} else 0o600


def discover_models(
    value: ModelSettingsInput | None = None,
    *,
    path: Path | None = None,
    opener: Callable[..., Any] = urlopen,
) -> list[str]:
    stored = load_model_settings(path=path)
    provider = value.provider if value is not None else stored.provider if stored else ""
    base_url = value.base_url if value is not None else stored.base_url if stored else ""
    supplied_key = value.api_key if value is not None else ""
    saved_key = stored.api_key if stored is not None else ""
    api_key = supplied_key or saved_key or _cached_key(provider)
    if not provider or not base_url:
        raise ModelSettingsError("请先填写模型供应商和 Base URL。")
    if not api_key:
        raise ModelSettingsError("请填写 API Key 或完成 Codex 登录。")
    request = Request(
        _validate_base_url(base_url).rstrip("/") + "/models",
        headers={"Authorization": f"Bearer {api_key}", "User-Agent": "PeerAssist-model-discovery"},
    )
    try:
        with opener(request, timeout=20.0) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise ModelSettingsError("模型服务认证失败，请检查 API Key。") from None
        raise ModelSettingsError(f"模型列表接口返回 HTTP {exc.code}。") from None
    except (URLError, TimeoutError, OSError):
        raise ModelSettingsError("无法连接模型列表接口。") from None
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise ModelSettingsError("模型列表响应过大。")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeError):
        raise ModelSettingsError("模型列表响应格式无效。") from None
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ModelSettingsError("模型列表响应缺少 data。")
    models = {
        str(row.get("id"))
        for row in rows[:500]
        if isinstance(row, dict) and _MODEL_ID.fullmatch(str(row.get("id") or ""))
    }
    if not models:
        raise ModelSettingsError("模型服务未返回可用模型。")
    return sorted(models, key=str.casefold)


def _cached_key(provider: str) -> str | None:
    if provider == "openai-codex":
        auth = load_cached_codex_auth()
        return auth.access_token if auth is not None else None
    for name in ("PEERASSIST_OPENAI_API_KEY", "EXECUTION_OPENAI_API_KEY", "OPENAI_API_KEY"):
        value = os.getenv(name, "").strip()
        if value:
            return value
    return None


def _validate_provider(value: str) -> str:
    provider = str(value or "").strip().casefold()
    if provider not in _PROVIDERS:
        raise ModelSettingsError("模型供应商无效。")
    return provider


def _validate_base_url(value: str) -> str:
    normalized = str(value or "").strip().rstrip("/")
    if len(normalized) > 2048:
        raise ModelSettingsError("Base URL 长度无效。")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ModelSettingsError("Base URL 必须是完整的 HTTP(S) 地址。")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ModelSettingsError("Base URL 不能包含凭据、查询参数或片段。")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ModelSettingsError("非本机模型接口必须使用 HTTPS。")
    return normalized


def _validate_model(value: str, *, required: bool) -> str:
    model = str(value or "").strip()
    if not model and not required:
        return ""
    if not _MODEL_ID.fullmatch(model):
        raise ModelSettingsError("请选择有效模型。")
    return model


def _key_hint(api_key: str) -> str:
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "已配置"
    return f"{api_key[:3]}...{api_key[-4:]}"

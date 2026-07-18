from __future__ import annotations

import json
import stat
from pathlib import Path

from peerassist.model_settings import (
    ModelSettingsInput,
    discover_models,
    load_model_settings,
    save_model_settings,
)


class _ModelsResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self, _limit: int = -1) -> bytes:
        return json.dumps(
            {
                "data": [
                    {"id": "gpt-5.6-sol"},
                    {"id": "gpt-5.5"},
                    {"id": "gpt-5.6-sol"},
                ]
            }
        ).encode()


def test_model_settings_persist_secret_server_side_and_return_only_masked_state(
    tmp_path: Path,
) -> None:
    path = tmp_path / "model-settings.json"
    saved = save_model_settings(
        ModelSettingsInput(
            provider="openai-codex",
            base_url="https://provider.example/v1",
            model="gpt-5.6-sol",
            api_key="sk-synthetic-private",
        ),
        path=path,
    )

    assert saved.public_view() == {
        "provider": "openai-codex",
        "base_url": "https://provider.example/v1",
        "model": "gpt-5.6-sol",
        "api_mode": "responses",
        "api_key_configured": True,
        "api_key_hint": "sk-...vate",
    }
    assert load_model_settings(path=path) == saved
    assert "sk-synthetic-private" not in repr(saved)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_model_discovery_uses_saved_secret_and_returns_sorted_unique_ids(tmp_path: Path) -> None:
    path = tmp_path / "model-settings.json"
    save_model_settings(
        ModelSettingsInput(
            provider="openai-compatible",
            base_url="https://provider.example/v1",
            model="gpt-5.5",
            api_key="sk-synthetic-private",
        ),
        path=path,
    )
    observed: dict[str, object] = {}

    def open_models(request, *, timeout: float):
        observed["url"] = request.full_url
        observed["authorization"] = request.headers.get("Authorization")
        observed["timeout"] = timeout
        return _ModelsResponse()

    models = discover_models(path=path, opener=open_models)

    assert models == ["gpt-5.5", "gpt-5.6-sol"]
    assert observed == {
        "url": "https://provider.example/v1/models",
        "authorization": "Bearer sk-synthetic-private",
        "timeout": 20.0,
    }

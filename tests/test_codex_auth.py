from __future__ import annotations

import json

from llm.codex_auth import _extract_from_auth_json


def test_codex_cli_openai_api_key_cache_is_recognized(tmp_path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(json.dumps({"OPENAI_API_KEY": "sk-synthetic-test-key"}))

    auth = _extract_from_auth_json(auth_file)

    assert auth is not None
    assert auth.access_token == "sk-synthetic-test-key"
    assert auth.source == str(auth_file)

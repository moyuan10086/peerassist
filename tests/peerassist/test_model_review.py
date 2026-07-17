from __future__ import annotations

from typing import Any

from common.config import get_settings
from peerassist.model_review import (
    ModelReviewConfig,
    resolve_model_review_config,
    run_batched_model_review,
)


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"concerns":[{"agent_id":"structure_agent",'
                            '"level":"clarification_needed","category":"structure",'
                            '"title":"Clarify scope","evidence_ids":["E1"],'
                            '"impact":"Scope affects interpretation.",'
                            '"benign_explanation":"It may be stated elsewhere.",'
                            '"author_action":"State the scope."}]}'
                        )
                    }
                }
            ],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
        }


def test_model_review_uses_typed_workspace_settings(monkeypatch) -> None:
    monkeypatch.setenv("PEERASSIST_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("PEERASSIST_OPENAI_BASE_URL", "https://example.test/v1/")
    monkeypatch.setenv("PEERASSIST_OPENAI_MODEL", "gpt-test")
    monkeypatch.setenv("PEERASSIST_OPENAI_TIMEOUT_SECONDS", "180")
    get_settings.cache_clear()
    try:
        config = resolve_model_review_config()

        assert config == ModelReviewConfig(
            api_key="test-key",
            base_url="https://example.test/v1",
            model="gpt-test",
            max_tokens=900,
            timeout_seconds=180.0,
        )
    finally:
        get_settings.cache_clear()


def test_batched_model_review_enforces_fast_token_budget_and_returns_usage() -> None:
    captured: dict[str, Any] = {}

    def post(url: str, **kwargs: Any) -> _Response:
        captured.update({"url": url, **kwargs})
        return _Response()

    result = run_batched_model_review(
        {"selected_evidence": [{"id": "E1", "evidence_ids": ["E1"], "text": "Claim"}]},
        ModelReviewConfig(
            api_key="runtime-test-key",
            base_url="https://example.test/v1",
            model="gpt-test",
            max_tokens=900,
            timeout_seconds=20,
        ),
        post=post,
    )

    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["json"]["max_tokens"] == 900
    assert captured["json"]["temperature"] == 0.1
    assert result["concerns"][0]["agent_id"] == "structure_agent"
    assert result["usage"]["total_tokens"] == 150
    assert "runtime-test-key" not in str(result)

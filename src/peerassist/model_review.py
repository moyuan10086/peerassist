"""Token-bounded OpenAI-compatible review enhancement for professional agents."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import requests

from common.config import get_settings
from llm.codex_auth import CodexAuth, get_codex_auth, load_cached_codex_auth
from llm.codex_client import invoke_codex
from llm.provider_capabilities import is_codex_provider
from peerassist.model_settings import load_model_settings


@dataclass(frozen=True)
class ModelReviewConfig:
    api_key: str | None = field(repr=False)
    base_url: str
    model: str
    provider: str = "openai"
    max_tokens: int = 900
    timeout_seconds: float = 240


def resolve_model_review_config() -> ModelReviewConfig | None:
    settings = get_settings()
    common = {
        "max_tokens": min(1200, max(256, settings.peerassist_review_max_tokens)),
        "timeout_seconds": min(300.0, max(10.0, settings.peerassist_openai_timeout_seconds)),
    }
    stored = load_model_settings()
    if stored is not None:
        provider = "openai-codex" if stored.provider == "openai-codex" else "openai"
        cached = load_cached_codex_auth() if provider == "openai-codex" else None
        api_key = stored.api_key or (cached.access_token if cached is not None else "")
        if not api_key:
            return None
        return ModelReviewConfig(
            api_key=api_key,
            base_url=stored.base_url,
            model=stored.model,
            provider=provider,
            **common,
        )
    api_key = str(settings.peerassist_openai_api_key or "").strip()
    if api_key:
        return ModelReviewConfig(
            api_key=api_key,
            base_url=settings.peerassist_openai_base_url,
            model=settings.peerassist_openai_model.strip(),
            **common,
        )
    if is_codex_provider(settings.model_provider) and load_cached_codex_auth() is not None:
        return ModelReviewConfig(
            api_key=None,
            base_url=settings.peerassist_openai_base_url,
            model=settings.peerassist_openai_model.strip(),
            provider="openai-codex",
            **common,
        )
    return None


def run_model_review_text(
    *,
    system: str,
    prompt: str,
    config: ModelReviewConfig,
    return_usage: bool = False,
) -> str | tuple[str, dict[str, int]]:
    """Invoke the configured review provider without retaining reusable credentials."""

    if config.provider != "openai-codex":
        raise ValueError("model_review_provider_not_supported")
    auth = (
        CodexAuth(config.api_key, None, "model-settings")
        if config.api_key
        else get_codex_auth(allow_browser_login=False)
    )
    return invoke_codex(
        prompt=prompt,
        system=system,
        auth=auth,
        model=config.model,
        base_url=config.base_url,
        return_usage=return_usage,
    )


def _extract_json(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if "```" in candidate:
        for part in candidate.split("```"):
            cleaned = part.strip().removeprefix("json").strip()
            if cleaned.startswith("{") and cleaned.endswith("}"):
                candidate = cleaned
                break
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("model_review_invalid_json") from None
        payload = json.loads(candidate[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("model_review_invalid_schema")
    return payload


def _usage(payload: dict[str, Any]) -> dict[str, int]:
    raw = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    input_tokens = int(raw.get("prompt_tokens", raw.get("input_tokens", 0)) or 0)
    output_tokens = int(raw.get("completion_tokens", raw.get("output_tokens", 0)) or 0)
    total_tokens = int(raw.get("total_tokens") or 0) or input_tokens + output_tokens
    return {
        "input_tokens": max(0, input_tokens),
        "output_tokens": max(0, output_tokens),
        "total_tokens": max(0, total_tokens),
    }


def run_batched_model_review(
    context: dict[str, Any],
    config: ModelReviewConfig,
    *,
    post: Callable[..., Any] = requests.post,
) -> dict[str, Any]:
    """Run all specialist roles in one bounded request and return untrusted draft rows."""

    endpoint = config.base_url.rstrip("/") + "/chat/completions"
    system = (
        "你是 PeerAssist 的专业论文审稿代理编排器。分别从结构、方法、实验、统计、引用、伦理、"
        "可复现性七个角色检查论文。你只生成供人工确认的候选问题，不作录用决定。每条问题必须"
        "引用 selected_evidence 中真实存在的 evidence_ids，并给出影响、善意解释和作者行动。"
        "证据不足时不要生成问题。只返回严格 JSON。"
    )
    task = {
        "review_roles": [
            "structure_agent",
            "methodology_agent",
            "experiment_agent",
            "statistics_agent",
            "citation_agent",
            "ethics_agent",
            "reproducibility_agent",
        ],
        "output_schema": {
            "concerns": [
                {
                    "agent_id": "one review role",
                    "level": "major_concern | minor_concern | clarification_needed | editor_note",
                    "category": "structure | methodology | experiment | statistics | citation | ethics | reproducibility",
                    "title": "concise Chinese title",
                    "evidence_ids": ["IDs copied from selected_evidence.evidence_ids"],
                    "impact": "scientific consequence",
                    "benign_explanation": "plausible non-problem explanation",
                    "author_action": "specific requested change or clarification",
                }
            ]
        },
        "limits": {
            "prioritize_core_validity": True,
            "maximum_concerns": 10,
            "ignore_cosmetic_wording": True,
        },
        "context": context,
    }
    if config.provider == "openai-codex":
        codex_result = run_model_review_text(
            system=system,
            prompt=json.dumps(task, ensure_ascii=False),
            config=config,
            return_usage=True,
        )
        if not isinstance(codex_result, tuple):
            raise ValueError("model_review_empty_response")
        content, usage = codex_result
        result = _extract_json(content)
        concerns = result.get("concerns") if isinstance(result.get("concerns"), list) else []
        return {
            "concerns": concerns[:10],
            "usage": usage,
            "model": config.model,
            "prompt_version": "peerassist.professional_agents.v1",
        }

    if not config.api_key:
        raise ValueError("model_review_credentials_missing")
    response = post(
        endpoint,
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(task, ensure_ascii=False)},
            ],
            "temperature": 0.1,
            "max_tokens": min(1200, max(256, int(config.max_tokens))),
        },
        timeout=min(300.0, max(10.0, float(config.timeout_seconds))),
    )
    response.raise_for_status()
    response_payload = response.json()
    choices = response_payload.get("choices") if isinstance(response_payload, dict) else None
    if not isinstance(choices, list) or not choices:
        raise ValueError("model_review_empty_response")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise ValueError("model_review_empty_content")
    result = _extract_json(content)
    concerns = result.get("concerns") if isinstance(result.get("concerns"), list) else []
    return {
        "concerns": concerns[:10],
        "usage": _usage(response_payload),
        "model": config.model,
        "prompt_version": "peerassist.professional_agents.v1",
    }

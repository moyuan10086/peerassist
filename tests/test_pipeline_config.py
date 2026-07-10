from __future__ import annotations

import os
import sys
from argparse import Namespace

from common.config import Settings
from pipeline_full import _apply_cli_env_overrides, parse_args


def _args(*, disable_semantic_scholar: bool) -> Namespace:
    return Namespace(
        llm_provider="",
        llm_model="",
        mineru_api_token="",
        gemini_api_key="",
        teaser_mode="auto",
        disable_semantic_scholar=disable_semantic_scholar,
    )


def test_disable_semantic_scholar_flag_sets_env(monkeypatch) -> None:
    monkeypatch.delenv("SEMANTIC_SCHOLAR_ENABLED", raising=False)

    _apply_cli_env_overrides(_args(disable_semantic_scholar=True))

    assert os.environ["SEMANTIC_SCHOLAR_ENABLED"] == "false"


def test_semantic_scholar_env_is_not_overwritten_without_flag(monkeypatch) -> None:
    monkeypatch.setenv("SEMANTIC_SCHOLAR_ENABLED", "true")

    _apply_cli_env_overrides(_args(disable_semantic_scholar=False))

    assert os.environ["SEMANTIC_SCHOLAR_ENABLED"] == "true"


def test_paper_search_is_enabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("PAPER_SEARCH_ENABLED", raising=False)
    monkeypatch.delenv("PAPER_SEARCH_BASE_URL", raising=False)

    settings = Settings(_env_file=None)

    assert settings.paper_search_enabled is True
    assert settings.paper_search_provider == "arxiv"
    assert settings.paper_search_base_url is None


def test_peerassist_mode_defaults_to_off(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["pipeline", "paper.pdf"])

    args = parse_args()

    assert args.peerassist_mode == "off"


def test_peerassist_mode_accepts_fast(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["pipeline", "paper.pdf", "--peerassist-mode", "fast"])

    args = parse_args()

    assert args.peerassist_mode == "fast"


def test_peerassist_mcp_manifest_argument_is_available(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["pipeline", "paper.pdf", "--peerassist-mode", "standard", "--peerassist-mcp-manifest", "mcp.json"],
    )

    args = parse_args()

    assert args.peerassist_mcp_manifest == "mcp.json"


def test_peerassist_skill_root_argument_is_repeatable(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pipeline",
            "paper.pdf",
            "--peerassist-mode",
            "standard",
            "--peerassist-skill-root",
            "skills/a",
            "--peerassist-skill-root",
            "skills/b",
        ],
    )

    args = parse_args()

    assert args.peerassist_skill_root == ["skills/a", "skills/b"]

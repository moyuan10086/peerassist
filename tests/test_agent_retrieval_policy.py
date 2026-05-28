from __future__ import annotations

from agent_runtime.agent_prompt import build_review_agent_system_prompt
from agent_runtime.agent_tools import _missing_required_read_paper_call
from common.types import PaperSearchUsage


def test_active_prompt_requires_read_paper_after_effective_paper_search() -> None:
    prompt = build_review_agent_system_prompt(
        source_file_id="job",
        source_file_name="paper.pdf",
        paper_markdown="# Paper\n\nA method paper.",
        paper_search_runtime_state={"enabled": True, "started": True, "availability": "ready"},
        semantic_scholar_context="(empty)",
    )

    assert "must call read_paper" in prompt
    assert "before writing Section 2" in prompt
    assert "If no readable candidate is available" in prompt


def test_read_paper_gate_requires_success_after_effective_search() -> None:
    usage = PaperSearchUsage(
        total_calls=1,
        successful_calls=1,
        effective_calls=1,
        papers_found=3,
        distinct_queries=1,
    )

    assert _missing_required_read_paper_call(usage, retrieval_not_started=False) is True

    usage.read_paper_successful_calls = 1

    assert _missing_required_read_paper_call(usage, retrieval_not_started=False) is False


def test_read_paper_gate_is_waived_when_search_never_started() -> None:
    usage = PaperSearchUsage(
        total_calls=1,
        successful_calls=0,
        effective_calls=0,
        papers_found=0,
        distinct_queries=1,
    )

    assert _missing_required_read_paper_call(usage, retrieval_not_started=True) is False

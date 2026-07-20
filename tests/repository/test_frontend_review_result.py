from pathlib import Path


def test_platform_workspace_hydrates_structured_review_result() -> None:
    source = Path("web/peerassist-workspace/src/main.tsx").read_text(encoding="utf-8")

    assert 'logical_name === "review_result.json"' in source
    assert "queue: { items:" in source
    assert "agent_runs:" in source
    assert "citation_audit:" in source

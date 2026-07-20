from pathlib import Path


def test_platform_workspace_hydrates_structured_review_result() -> None:
    source = Path("web/peerassist-workspace/src/main.tsx").read_text(encoding="utf-8")

    assert 'logical_name === "review_result.json"' in source
    assert "queue: { items:" in source
    assert "agent_runs:" in source
    assert "citation_audit:" in source


def test_platform_workspace_uses_server_owned_draft_endpoint() -> None:
    source = Path("web/peerassist-workspace/src/main.tsx").read_text(encoding="utf-8")

    assert '`${base}/draft`' in source
    assert 'method: "PATCH"' in source
    assert 'expected_version: activeReviewJob.revision' in source


def test_workspace_waits_for_job_list_before_restoring_a_job() -> None:
    source = Path("web/peerassist-workspace/src/main.tsx").read_text(encoding="utf-8")

    assert "reviewJobs.some((job) => job.id === activeJobId)" in source

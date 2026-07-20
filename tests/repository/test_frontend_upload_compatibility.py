from pathlib import Path


def test_frontend_does_not_require_secure_context_for_idempotency_keys() -> None:
    source = Path("web/peerassist-workspace/src/main.tsx").read_text(encoding="utf-8")

    assert "function idempotencyKey(" in source
    assert "crypto.randomUUID()" not in source
    assert source.count("idempotencyKey()") >= 7


def test_authenticated_platform_workspace_does_not_fall_back_to_legacy_jobs() -> None:
    source = Path("web/peerassist-workspace/src/main.tsx").read_text(encoding="utf-8")

    assert "if (authSession.authenticated) {\n      setReviewJobs([]);\n      return [];\n    }" in source
    assert 'fetch("/api/jobs"' in source  # retained only for unauthenticated legacy mode
    assert 'fetch("/api/bootstrap")' in source  # retained only for unauthenticated legacy mode

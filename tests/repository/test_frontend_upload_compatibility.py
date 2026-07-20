from pathlib import Path


def test_frontend_does_not_require_secure_context_for_idempotency_keys() -> None:
    source = Path("web/peerassist-workspace/src/main.tsx").read_text(encoding="utf-8")

    assert "function idempotencyKey(" in source
    assert "crypto.randomUUID()" not in source
    assert source.count("idempotencyKey()") >= 7

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / "web/peerassist-workspace/src/main.tsx"


def test_review_draft_is_editable_copyable_and_downloadable() -> None:
    source = WORKSPACE.read_text(encoding="utf-8")

    assert "function ReviewDraftEditor" in source
    assert "navigator.clipboard.writeText" in source
    assert "URL.createObjectURL" in source
    assert "peerassist.reviewDraft." in source

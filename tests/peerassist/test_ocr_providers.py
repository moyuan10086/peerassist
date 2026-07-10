from __future__ import annotations

from pathlib import Path

from peerassist.ocr_providers import (
    BaiduDocumentParseProvider,
    BaiduPaddleOCRVLProvider,
    BaiduUnlimitedOCRProvider,
    MinerUParseProvider,
    PaddleOCRStructureProvider,
    ParseProviderKind,
)


def test_mineru_provider_resolves_existing_parse_artifacts(tmp_path: Path) -> None:
    markdown = tmp_path / "mineru_full.md"
    content = tmp_path / "mineru_content_list.json"
    markdown.write_text("# Abstract\n", encoding="utf-8")
    content.write_text("[]", encoding="utf-8")

    provider = MinerUParseProvider()
    result = provider.from_parse_payload(
        {
            "mineru_markdown_path": str(markdown),
            "mineru_content_list_path": str(content),
            "markdown_provider": "mineru-cloud",
        },
        repo_root=tmp_path,
    )

    assert provider.provider_name == "mineru"
    assert provider.kind is ParseProviderKind.MINERU
    assert provider.external_upload_required is False
    assert result.enabled is True
    assert result.markdown_path == markdown
    assert result.content_list_path == content
    assert result.metadata["markdown_provider"] == "mineru-cloud"


def test_mineru_provider_warns_when_markdown_missing(tmp_path: Path) -> None:
    result = MinerUParseProvider().from_parse_payload({}, repo_root=tmp_path)

    assert result.enabled is True
    assert result.markdown_path is None
    assert "mineru_markdown_missing" in result.warnings


def test_paddleocr_provider_is_local_stub_until_enabled(tmp_path: Path) -> None:
    provider = PaddleOCRStructureProvider(enabled=False)
    result = provider.resolve_parse_result(paper_pdf=tmp_path / "paper.pdf")

    assert provider.provider_name == "paddleocr_structure_v3"
    assert provider.external_upload_required is False
    assert result.enabled is False
    assert "provider_not_enabled" in result.warnings


def test_baidu_providers_fail_closed_without_explicit_enablement(tmp_path: Path) -> None:
    for provider in (
        BaiduDocumentParseProvider(enabled=False),
        BaiduPaddleOCRVLProvider(enabled=False),
        BaiduUnlimitedOCRProvider(enabled=False),
    ):
        result = provider.resolve_parse_result(paper_pdf=tmp_path / "paper.pdf")

        assert provider.external_upload_required is True
        assert result.enabled is False
        assert "external_upload_requires_explicit_enablement" in result.warnings

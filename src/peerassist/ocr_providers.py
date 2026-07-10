"""OCR and document parsing provider contracts for PeerAssist."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from common.pipeline_context import resolve_artifact_path


class ParseProviderKind(StrEnum):
    MINERU = "mineru"
    PADDLEOCR_STRUCTURE_V3 = "paddleocr_structure_v3"
    BAIDU_DOC_PARSER = "baidu_doc_parser"
    BAIDU_PADDLEOCR_VL = "baidu_paddleocr_vl"
    BAIDU_UNLIMITED_OCR = "baidu_unlimited_ocr"


class ParseProviderMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider_name: str
    kind: ParseProviderKind
    external_upload_required: bool = False
    enabled: bool = True
    version: str = ""
    request_mode: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class ParseProviderResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="ignore")

    provider_name: str
    kind: ParseProviderKind
    markdown_path: Path | None = None
    content_list_path: Path | None = None
    structured_json_path: Path | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    external_upload_required: bool = False
    enabled: bool = True


class MinerUParseProvider:
    provider_name = "mineru"
    kind = ParseProviderKind.MINERU
    external_upload_required = False

    def metadata(self) -> ParseProviderMetadata:
        return ParseProviderMetadata(
            provider_name=self.provider_name,
            kind=self.kind,
            external_upload_required=self.external_upload_required,
            enabled=True,
            request_mode="existing_factreview_parse_artifacts",
        )

    def from_parse_payload(self, payload: dict[str, Any], *, repo_root: Path) -> ParseProviderResult:
        markdown_path = resolve_artifact_path(repo_root, payload.get("mineru_markdown_path"))
        content_path = resolve_artifact_path(repo_root, payload.get("mineru_content_list_path"))
        warnings: list[str] = []
        if markdown_path is None or not markdown_path.exists():
            warnings.append("mineru_markdown_missing")
            markdown_path = None
        if content_path is not None and not content_path.exists():
            warnings.append("mineru_content_list_missing")
            content_path = None
        metadata = {
            "markdown_provider": payload.get("markdown_provider") or "",
            "mineru_batch_id": payload.get("mineru_batch_id") or "",
            "parse_warning": payload.get("parse_warning") or "",
        }
        return ParseProviderResult(
            provider_name=self.provider_name,
            kind=self.kind,
            markdown_path=markdown_path,
            content_list_path=content_path,
            metadata=metadata,
            warnings=warnings,
            external_upload_required=False,
            enabled=True,
        )


class _StubParseProvider:
    provider_name: str
    kind: ParseProviderKind
    external_upload_required: bool

    def __init__(self, *, enabled: bool = False, credentials_available: bool = False) -> None:
        self.enabled = bool(enabled)
        self.credentials_available = bool(credentials_available)

    def metadata(self) -> ParseProviderMetadata:
        return ParseProviderMetadata(
            provider_name=self.provider_name,
            kind=self.kind,
            external_upload_required=self.external_upload_required,
            enabled=self.enabled,
            request_mode="adapter_stub",
            warnings=[] if self.enabled else ["provider_not_enabled"],
        )

    def resolve_parse_result(self, *, paper_pdf: Path) -> ParseProviderResult:
        warnings: list[str] = []
        if not self.enabled:
            warnings.append(
                "external_upload_requires_explicit_enablement"
                if self.external_upload_required
                else "provider_not_enabled"
            )
        if self.enabled and self.external_upload_required and not self.credentials_available:
            warnings.append("credentials_required")
        if self.enabled and not paper_pdf.exists():
            warnings.append("source_pdf_missing")
        usable = self.enabled and not warnings
        return ParseProviderResult(
            provider_name=self.provider_name,
            kind=self.kind,
            metadata={"paper_pdf": str(paper_pdf)},
            warnings=warnings,
            external_upload_required=self.external_upload_required,
            enabled=usable,
        )


class PaddleOCRStructureProvider(_StubParseProvider):
    provider_name = "paddleocr_structure_v3"
    kind = ParseProviderKind.PADDLEOCR_STRUCTURE_V3
    external_upload_required = False


class BaiduDocumentParseProvider(_StubParseProvider):
    provider_name = "baidu_doc_parser"
    kind = ParseProviderKind.BAIDU_DOC_PARSER
    external_upload_required = True


class BaiduPaddleOCRVLProvider(_StubParseProvider):
    provider_name = "baidu_paddleocr_vl"
    kind = ParseProviderKind.BAIDU_PADDLEOCR_VL
    external_upload_required = True


class BaiduUnlimitedOCRProvider(_StubParseProvider):
    provider_name = "baidu_unlimited_ocr"
    kind = ParseProviderKind.BAIDU_UNLIMITED_OCR
    external_upload_required = True

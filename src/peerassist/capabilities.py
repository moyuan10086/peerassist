"""PeerAssist capability registry.

The registry exposes only capability metadata and compact input/output
summaries. Full skill bodies or provider-specific instructions are loaded later
by an approved executor, not by default prompt construction.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class PermissionClass(StrEnum):
    READ_ARTIFACT = "read_artifact"
    WRITE_ARTIFACT = "write_artifact"
    EXTERNAL_REQUEST = "external_request"
    MANUSCRIPT_UPLOAD = "manuscript_upload"
    CODE_EXECUTION = "code_execution"


class CapabilitySource(StrEnum):
    BUILTIN = "builtin"
    MCP = "mcp"
    SKILL = "skill"


APPROVAL_PERMISSIONS = {
    PermissionClass.WRITE_ARTIFACT,
    PermissionClass.EXTERNAL_REQUEST,
    PermissionClass.MANUSCRIPT_UPLOAD,
    PermissionClass.CODE_EXECUTION,
}


class CapabilitySpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    description: str
    source: CapabilitySource
    version: str = "v1"
    modes: list[str] = Field(default_factory=lambda: ["fast", "standard", "deep"])
    permissions: list[PermissionClass] = Field(default_factory=list)
    input_summary: str = ""
    output_summary: str = ""

    @property
    def approval_required(self) -> bool:
        return any(permission in APPROVAL_PERMISSIONS for permission in self.permissions)

    def to_exposed_schema(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "source": self.source.value,
            "version": self.version,
            "permissions": [permission.value for permission in self.permissions],
            "approval_required": self.approval_required,
            "input_summary": self.input_summary,
            "output_summary": self.output_summary,
        }


class CapabilityRegistry:
    def __init__(self, capabilities: list[CapabilitySpec] | None = None) -> None:
        self._capabilities = list(capabilities or [])

    def register(self, capability: CapabilitySpec) -> None:
        self._capabilities.append(capability)

    def find(self, name: str) -> CapabilitySpec | None:
        for capability in self._capabilities:
            if capability.name == name:
                return capability
        return None

    def expose(
        self,
        *,
        mode: str,
        allow_external: bool = False,
        allow_manuscript_upload: bool = False,
        allow_code_execution: bool = False,
    ) -> list[CapabilitySpec]:
        normalized_mode = str(mode or "fast").strip().lower()
        exposed: list[CapabilitySpec] = []
        for capability in self._capabilities:
            if normalized_mode not in capability.modes:
                continue
            perms = set(capability.permissions)
            if PermissionClass.EXTERNAL_REQUEST in perms and not allow_external:
                continue
            if PermissionClass.MANUSCRIPT_UPLOAD in perms and not allow_manuscript_upload:
                continue
            if PermissionClass.CODE_EXECUTION in perms and not allow_code_execution:
                continue
            exposed.append(capability)
        return exposed


def default_capability_registry() -> CapabilityRegistry:
    return CapabilityRegistry(
        [
            CapabilitySpec(
                name="build_evidence_ledger",
                description="Build a stable evidence ledger from parser artifacts.",
                source=CapabilitySource.BUILTIN,
                permissions=[PermissionClass.READ_ARTIFACT, PermissionClass.WRITE_ARTIFACT],
                input_summary="paper id, source PDF path, parser markdown/content paths",
                output_summary="evidence_ledger.json",
            ),
            CapabilitySpec(
                name="percentage_consistency_check",
                description="Check count/denominator plus percentage consistency.",
                source=CapabilitySource.BUILTIN,
                permissions=[PermissionClass.READ_ARTIFACT, PermissionClass.WRITE_ARTIFACT],
                input_summary="evidence ledger text/table items",
                output_summary="deterministic check leads",
            ),
            CapabilitySpec(
                name="peerassist_local_agents",
                description="Run the deterministic local PeerAssist review agent backbone.",
                source=CapabilitySource.BUILTIN,
                permissions=[PermissionClass.READ_ARTIFACT, PermissionClass.WRITE_ARTIFACT],
                input_summary="evidence ledger, deterministic checks, exposed capability names",
                output_summary="agent_results.json",
            ),
            CapabilitySpec(
                name="mineru_parse_artifacts",
                description="Use existing FactReview MinerU parser artifacts.",
                source=CapabilitySource.BUILTIN,
                permissions=[PermissionClass.READ_ARTIFACT],
                input_summary="parse stage paper.json",
                output_summary="markdown/content artifact paths",
            ),
            CapabilitySpec(
                name="paddleocr_structure_v3",
                description="Local or self-hosted PP-StructureV3-style document parsing adapter.",
                source=CapabilitySource.BUILTIN,
                modes=["standard", "deep"],
                permissions=[PermissionClass.READ_ARTIFACT, PermissionClass.WRITE_ARTIFACT],
                input_summary="source PDF or document images",
                output_summary="Markdown and structured layout JSON",
            ),
            CapabilitySpec(
                name="baidu_doc_parser",
                description="Optional Baidu document parsing OCR adapter.",
                source=CapabilitySource.BUILTIN,
                modes=["standard", "deep"],
                permissions=[
                    PermissionClass.EXTERNAL_REQUEST,
                    PermissionClass.MANUSCRIPT_UPLOAD,
                    PermissionClass.WRITE_ARTIFACT,
                ],
                input_summary="source PDF submitted to configured Baidu OCR endpoint",
                output_summary="Markdown and structured OCR JSON",
            ),
            CapabilitySpec(
                name="baidu_paddleocr_vl",
                description="Optional Baidu-hosted PaddleOCR-VL parsing adapter.",
                source=CapabilitySource.BUILTIN,
                modes=["deep"],
                permissions=[
                    PermissionClass.EXTERNAL_REQUEST,
                    PermissionClass.MANUSCRIPT_UPLOAD,
                    PermissionClass.WRITE_ARTIFACT,
                ],
                input_summary="source PDF or page images submitted to configured Baidu endpoint",
                output_summary="multimodal document parse JSON",
            ),
            CapabilitySpec(
                name="baidu_unlimited_ocr",
                description="Optional Baidu Unlimited-OCR adapter.",
                source=CapabilitySource.BUILTIN,
                modes=["deep"],
                permissions=[
                    PermissionClass.EXTERNAL_REQUEST,
                    PermissionClass.MANUSCRIPT_UPLOAD,
                    PermissionClass.WRITE_ARTIFACT,
                ],
                input_summary="source PDF submitted to configured Baidu endpoint",
                output_summary="document parse artifacts",
            ),
        ]
    )

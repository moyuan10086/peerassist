from __future__ import annotations

import json

from peerassist.capabilities import CapabilityRegistry, CapabilitySource, PermissionClass
from peerassist.skill_executor import build_skill_handlers
from peerassist.skill_loader import SkillCatalog, register_skill_capabilities
from peerassist.tool_invocations import CapabilityInvocationRequest, CapabilityInvoker
from peerassist.tool_trace import ToolTraceRecorder
from schemas.peerassist import ToolTraceStatus


def _write_skill(root, name: str, body: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")


def test_skill_catalog_discovers_metadata_without_loading_body(tmp_path) -> None:
    _write_skill(
        tmp_path,
        "citation-skill",
        """---
name: citation-skill
description: Check citation metadata.
version: v2
permissions:
  - external_request
---

# Citation Skill

FULL SECRET INSTRUCTIONS SHOULD NOT BE EXPOSED IN METADATA.
""",
    )

    catalog = SkillCatalog([tmp_path])
    specs = catalog.discover()

    assert len(specs) == 1
    assert specs[0].name == "citation-skill"
    assert specs[0].source is CapabilitySource.SKILL
    assert specs[0].version == "v2"
    assert specs[0].permissions == [PermissionClass.EXTERNAL_REQUEST]
    exposed = specs[0].to_exposed_schema()
    assert exposed["description"] == "Check citation metadata."
    assert "FULL SECRET" not in json.dumps(exposed)


def test_register_skill_capabilities_filters_by_permission(tmp_path) -> None:
    _write_skill(
        tmp_path,
        "local-evidence-skill",
        """---
name: local-evidence-skill
description: Local evidence helper.
permissions:
  - read_artifact
---
body
""",
    )
    _write_skill(
        tmp_path,
        "external-skill",
        """---
name: external-skill
description: External helper.
permissions:
  - external_request
---
body
""",
    )
    registry = CapabilityRegistry()

    register_skill_capabilities(registry, SkillCatalog([tmp_path]))

    names = {cap.name for cap in registry.expose(mode="fast")}
    assert names == {"local-evidence-skill"}
    external_names = {
        cap.name for cap in registry.expose(mode="fast", allow_external=True)
    }
    assert external_names == {"local-evidence-skill", "external-skill"}


def test_skill_body_is_loaded_only_inside_approved_invocation(tmp_path) -> None:
    _write_skill(
        tmp_path,
        "write-skill",
        """---
name: write-skill
description: Writes a derived artifact.
permissions:
  - write_artifact
---
FULL SKILL BODY
""",
    )
    catalog = SkillCatalog([tmp_path])
    registry = CapabilityRegistry(catalog.discover())
    trace_path = tmp_path / "tool_trace.jsonl"
    body_loads = 0

    def handler(_payload):
        nonlocal body_loads
        body_loads += 1
        return {"skill_body": catalog.load_body("write-skill")}

    invoker = CapabilityInvoker(
        registry=registry,
        trace=ToolTraceRecorder(trace_path),
        handlers={"write-skill": handler},
    )

    needs_approval = invoker.invoke(
        CapabilityInvocationRequest(
            task_id="paper-1",
            call_id="skill-call",
            agent_id="method_agent",
            capability_name="write-skill",
            input_summary="load skill",
            payload={},
        )
    )

    assert needs_approval.status is ToolTraceStatus.APPROVAL_REQUIRED
    assert body_loads == 0

    approved = invoker.invoke(
        CapabilityInvocationRequest(
            task_id="paper-1",
            call_id="skill-call-approved",
            agent_id="method_agent",
            capability_name="write-skill",
            input_summary="load skill after approval",
            payload={},
            approved=True,
        )
    )

    assert approved.status is ToolTraceStatus.COMPLETED
    assert approved.output["skill_body"].endswith("FULL SKILL BODY\n")
    assert body_loads == 1


def test_skill_executor_loads_full_body_only_after_invoker_approval(tmp_path) -> None:
    _write_skill(
        tmp_path,
        "method-skill",
        """---
name: method-skill
description: Methodology helper.
permissions:
  - read_artifact
---
# Method Skill

Full methodology instructions.
""",
    )
    catalog = SkillCatalog([tmp_path])
    specs = catalog.discover()
    registry = CapabilityRegistry(specs)
    trace_path = tmp_path / "tool_trace.jsonl"
    invoker = CapabilityInvoker(
        registry=registry,
        trace=ToolTraceRecorder(trace_path),
        handlers=build_skill_handlers(catalog, specs),
    )

    result = invoker.invoke(
        CapabilityInvocationRequest(
            task_id="paper-1",
            call_id="skill-call",
            agent_id="method_agent",
            capability_name="method-skill",
            input_summary="load methodology skill",
            payload={"evidence_ids": ["P01-L001"]},
            approved=True,
            evidence_ids=["P01-L001"],
        )
    )

    assert result.status is ToolTraceStatus.COMPLETED
    assert result.output["skill_name"] == "method-skill"
    assert result.output["skill_body"].endswith("Full methodology instructions.\n")
    assert result.output["input_payload"] == {"evidence_ids": ["P01-L001"]}

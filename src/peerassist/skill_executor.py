"""Audited local Skill invocation helpers for PeerAssist."""

from __future__ import annotations

from typing import Any

from peerassist.capabilities import CapabilitySpec
from peerassist.skill_loader import SkillCatalog
from peerassist.tool_invocations import CapabilityHandler


def build_skill_handlers(
    catalog: SkillCatalog, capabilities: list[CapabilitySpec]
) -> dict[str, CapabilityHandler]:
    handlers: dict[str, CapabilityHandler] = {}
    for capability in capabilities:
        handlers[capability.name] = _handler_for(catalog, capability)
    return handlers


def _handler_for(catalog: SkillCatalog, capability: CapabilitySpec) -> CapabilityHandler:
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "skill_name": capability.name,
            "skill_version": capability.version,
            "skill_body": catalog.load_body(capability.name),
            "input_payload": dict(payload),
        }

    return handler

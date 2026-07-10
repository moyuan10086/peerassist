"""Progressive local Skill metadata loader for PeerAssist."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from peerassist.capabilities import (
    CapabilityRegistry,
    CapabilitySource,
    CapabilitySpec,
    PermissionClass,
)


class SkillCatalog:
    def __init__(self, roots: list[Path]) -> None:
        self.roots = [Path(root) for root in roots]
        self._skill_paths: dict[str, Path] = {}

    def discover(self) -> list[CapabilitySpec]:
        specs: list[CapabilitySpec] = []
        self._skill_paths = {}
        for skill_path in self._iter_skill_files():
            metadata = _front_matter(skill_path)
            name = str(metadata.get("name") or skill_path.parent.name).strip()
            if not name:
                continue
            self._skill_paths[name] = skill_path
            specs.append(
                CapabilitySpec(
                    name=name,
                    description=str(metadata.get("description") or "").strip(),
                    source=CapabilitySource.SKILL,
                    version=str(metadata.get("version") or "v1").strip() or "v1",
                    modes=_string_list(metadata.get("modes")) or ["fast", "standard", "deep"],
                    permissions=_permissions(metadata.get("permissions")),
                    input_summary=str(metadata.get("input_summary") or "skill-specific input").strip(),
                    output_summary=str(metadata.get("output_summary") or "skill-specific output").strip(),
                )
            )
        return specs

    def load_body(self, name: str) -> str:
        path = self._skill_paths.get(name)
        if path is None:
            self.discover()
            path = self._skill_paths.get(name)
        if path is None:
            raise KeyError(f"unknown skill: {name}")
        return path.read_text(encoding="utf-8")

    def _iter_skill_files(self) -> list[Path]:
        paths: list[Path] = []
        for root in self.roots:
            if not root.exists():
                continue
            if root.name == "SKILL.md" and root.is_file():
                paths.append(root)
                continue
            paths.extend(sorted(root.glob("*/SKILL.md")))
        return paths


def register_skill_capabilities(registry: CapabilityRegistry, catalog: SkillCatalog) -> list[CapabilitySpec]:
    specs = catalog.discover()
    for spec in specs:
        registry.register(spec)
    return specs


def _front_matter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    return _parse_simple_yaml(text[4:end])


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("- ") and current_list_key:
            result.setdefault(current_list_key, []).append(stripped[2:].strip().strip("\"'"))
            continue
        current_list_key = None
        if ":" not in stripped:
            continue
        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if not value:
            result[key] = []
            current_list_key = key
        elif value.startswith("[") and value.endswith("]"):
            result[key] = [
                item.strip().strip("\"'")
                for item in value[1:-1].split(",")
                if item.strip()
            ]
        else:
            result[key] = value.strip("\"'")
    return result


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _permissions(value: Any) -> list[PermissionClass]:
    permissions: list[PermissionClass] = []
    for item in _string_list(value):
        try:
            permissions.append(PermissionClass(item))
        except ValueError:
            continue
    return permissions

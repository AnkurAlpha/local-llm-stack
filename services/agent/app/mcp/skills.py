from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ManualSkill:
    id: str
    description: str
    instructions: str
    capability: str | None = None


def _front_matter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    marker = text.find("\n---", 4)
    if marker < 0:
        return {}, text
    header = text[4:marker]
    body = text[marker + len("\n---") :].lstrip("\n")
    values: dict[str, str] = {}
    for line in header.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values, body


def _fallback_description(identifier: str, body: str) -> str:
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("```"):
            continue
        return line[:240]
    return f"Manual behavioral skill: {identifier}."


class SkillCatalog:
    def __init__(self, capabilities: dict[str, dict[str, Any]], manual_path: Path) -> None:
        self.capabilities = capabilities
        self.manual_path = manual_path
        self.manual: dict[str, ManualSkill] = {}
        self.reload_manual()

    def reload_manual(self) -> None:
        self.manual = {}
        if not self.manual_path.exists():
            return
        for path in sorted(self.manual_path.glob("*.md")):
            if path.name.lower() == "readme.md":
                continue
            values, body = _front_matter(path.read_text(encoding="utf-8"))
            identifier = values.get("id") or path.stem
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", identifier):
                continue
            description = values.get("description") or _fallback_description(identifier, body)
            capability = values.get("capability") or None
            self.manual[identifier] = ManualSkill(identifier, description, body.strip(), capability)

    def skills(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for identifier, capability in sorted(self.capabilities.items()):
            result.append(
                {
                    "id": identifier,
                    "kind": "auto",
                    "description": capability["description"],
                    "providers": [provider["id"] for provider in capability["providers"]],
                    "capability": identifier,
                    "available": bool(capability["available"]),
                    "tool_count": len(capability["tools"]),
                }
            )
        for skill in sorted(self.manual.values(), key=lambda item: item.id):
            result.append(
                {
                    "id": skill.id,
                    "kind": "manual",
                    "description": skill.description,
                    "providers": [],
                    "capability": skill.capability,
                    "available": skill.capability in self.capabilities if skill.capability else True,
                    "tool_count": len(self.capabilities.get(skill.capability or "", {}).get("tools", [])),
                }
            )
        return result

    def get(self, identifier: str) -> dict[str, Any]:
        for item in self.skills():
            if item["id"] == identifier:
                return item
        raise KeyError(identifier)

    def instructions(self, identifier: str) -> str:
        return self.manual.get(identifier, ManualSkill(identifier, "", "")).instructions

    def prompt(self) -> str:
        skills = self.skills()
        lines = [
            "LMCTL dynamic skill registry (compact):",
            "Activate a skill with the lmctl_activate_skill function before using its MCP tools.",
        ]
        if not skills:
            lines.append("No skills are currently available; continue without MCP tools.")
        for item in skills:
            providers = ", ".join(item["providers"]) or "behavior instructions"
            state = "available" if item["available"] else "partially unavailable"
            lines.append(
                f"- {item['id']}: {item['description']} Providers: {providers}. Status: {state}."
            )
        lines.append("Do not invent MCP tool names. Load the relevant skill when needed.")
        return "\n".join(lines)

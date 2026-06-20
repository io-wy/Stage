"""read_skill — return a skill's full SKILL.md doc (L2 progressive disclosure).

Pairs with the L1 catalog (injected at session start via ``hooks/skill_loader``).
An agent that sees a methodology skill in its catalog calls ``read_skill`` to read
the full playbook (inputs / outputs / method), then follows it itself — these are
read-and-follow methodology skills, not executable tools.
"""

from __future__ import annotations

from typing import Any

from openagents.errors.exceptions import PermanentToolError, ToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin

from openagents_orchestration.skills_registry import SkillRegistry


class ReadSkillTool(ToolPlugin):
    """Read a skill package's full SKILL.md documentation."""

    name = "read_skill"
    description = (
        "Read a methodology skill's full SKILL.md playbook (inputs, outputs, method), "
        "then follow it yourself. Available skills are listed in your system prompt."
    )

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(
            concurrency_safe=True,
            side_effects="readonly",
            reads_files=True,
            default_timeout_ms=10_000,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Name of the skill to read documentation for.",
                },
            },
            "required": ["skill_name"],
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        skill_name = str(params.get("skill_name", "")).strip()
        if not skill_name:
            raise PermanentToolError("skill_name is required", tool_name=self.name)
        registry = SkillRegistry()
        doc = registry.get_doc(skill_name)
        if doc is None:
            available = registry.names()
            raise ToolError(
                f"Skill '{skill_name}' not found. Available: {available}",
                tool_name=self.name,
            )
        return {"skill_name": skill_name, "doc": doc}

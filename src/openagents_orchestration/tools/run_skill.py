"""run_skill — execute a local skill package.

Discovers skill packages under `skills/` (SKILL.md + src/<pkg>/entrypoint.py)
and invokes their `run_openagent_skill(payload)` entrypoint.
"""

from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path
from typing import Any

from openagents.errors.exceptions import PermanentToolError, ToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


class RunSkillTool(ToolPlugin):
    """Execute a local skill package by name.

    Skill packages are discovered under `./skills/`:
    - Each subdirectory with a `SKILL.md` (YAML frontmatter) is a skill
    - Must contain exactly one `src/<package>/entrypoint.py`
    - Entrypoint must define `async def run_openagent_skill(payload: dict) -> dict`
    """

    name = "run_skill"
    description = (
        "Execute a local skill package. Provide the skill_name and a payload dict. "
        "Available skills: code-review-pipeline, data-processing-pipeline, "
        "web-research-pipeline."
    )
    durable_idempotent = False

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(
            concurrency_safe=False,
            side_effects="external",
            default_timeout_ms=5 * 60 * 1_000,
            interrupt_behavior="cancel",
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Name of the skill package to run.",
                },
                "payload": {
                    "type": "object",
                    "description": "Payload dict passed to the skill entrypoint.",
                },
            },
            "required": ["skill_name", "payload"],
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        skill_name = str(params.get("skill_name", "")).strip()
        payload = params.get("payload") or {}
        if not skill_name:
            raise PermanentToolError("skill_name is required", tool_name=self.name)
        if not isinstance(payload, dict):
            raise PermanentToolError("payload must be a dict", tool_name=self.name)

        # Discover skill
        skills_dir = Path("skills")
        if not skills_dir.exists():
            raise ToolError(
                f"Skills directory not found: {skills_dir.absolute()}",
                tool_name=self.name,
            )

        skill_root = skills_dir / skill_name
        if not skill_root.exists():
            available = [p.name for p in skills_dir.iterdir() if p.is_dir() and (p / "SKILL.md").exists()]
            raise ToolError(
                f"Skill '{skill_name}' not found. Available: {available}",
                tool_name=self.name,
            )

        entrypoints = list(skill_root.glob("src/*/entrypoint.py"))
        if len(entrypoints) != 1:
            raise ToolError(
                f"Skill '{skill_name}' must contain exactly one src/*/entrypoint.py",
                tool_name=self.name,
            )

        src_root = skill_root / "src"
        package_name = entrypoints[0].parent.name
        module_path = f"{package_name}.entrypoint"

        added = False
        if str(src_root) not in sys.path:
            sys.path.insert(0, str(src_root))
            added = True

        try:
            module = importlib.import_module(module_path)
            fn = getattr(module, "run_openagent_skill", None)
            if not callable(fn):
                raise ToolError(
                    f"Skill '{skill_name}' entrypoint must define 'run_openagent_skill'",
                    tool_name=self.name,
                )
            result = fn(payload)
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, dict):
                raise ToolError(
                    f"Skill '{skill_name}' returned {type(result).__name__}, expected dict",
                    tool_name=self.name,
                )
        except Exception as exc:
            if isinstance(exc, ToolError):
                raise
            raise ToolError(
                f"Skill '{skill_name}' failed: {exc}",
                tool_name=self.name,
            ) from exc
        finally:
            if added:
                try:
                    sys.path.remove(str(src_root))
                except ValueError:
                    pass

        return {
            "skill_name": skill_name,
            "status": "completed",
            "result": result,
        }

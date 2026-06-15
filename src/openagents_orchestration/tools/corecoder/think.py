"""Explicit reasoning tool.

Claude Code's ``Think`` tool: a no-op tool that lets the model write out its
reasoning before acting. The content is echoed back so the model can build on
it, and it is tracked in the exploration cache so the agent can see its own
recent reasoning.
"""

from __future__ import annotations

from typing import Any

from openagents.errors.exceptions import ToolError
from openagents.interfaces.run_context import RunContext
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


class ThinkTool(ToolPlugin):
    """A tool for explicit step-by-step reasoning."""

    name = "think"
    description = (
        "Use this to think step-by-step about a problem before choosing the next "
        "tool. Write your reasoning in the `thought` parameter. This is especially "
        "useful when you are unsure, need to plan multiple steps, or need to analyze "
        "tool output before acting."
    )
    durable_idempotent = True

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(
            concurrency_safe=True,
            side_effects="none",
            default_timeout_ms=1_000,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "thought": {
                    "type": "string",
                    "description": "Your step-by-step reasoning. Be concise but complete.",
                },
            },
            "required": ["thought"],
        }

    async def invoke(
        self, params: dict[str, Any], context: RunContext[Any] | None
    ) -> dict[str, Any]:
        thought = str(params.get("thought", "")).strip()
        if not thought:
            raise ToolError("thought is required", tool_name=self.name)

        if context is not None:
            thoughts = context.scratch.setdefault("_recent_thoughts", [])
            if isinstance(thoughts, list):
                thoughts.append(thought)
                if len(thoughts) > 5:
                    thoughts.pop(0)

        return {
            "thought": thought,
            "message": f"Thought recorded: {thought[:200]}{'...' if len(thought) > 200 else ''}",
        }

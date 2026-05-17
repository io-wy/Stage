"""finalize — signal completion and stop the orchestrator loop."""

from __future__ import annotations

from typing import Any

from openagents.errors.exceptions import PermanentToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


class FinalizeTool(ToolPlugin):
    """Signal that the objective is complete and stop the Director loop."""

    name = "finalize"
    description = (
        "Deliver the final result and stop the orchestration. "
        "Call ONLY when all tasks are complete or the objective "
        "cannot be furthered. Include a summary of what was done, "
        "what remains, and any tasks needing human intervention."
    )
    durable_idempotent = False

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(
            concurrency_safe=False,
            side_effects="writes_state",
            approval_mode="always",
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": (
                        "What was accomplished, what remains, and why. "
                        "Reference specific task results."
                    ),
                },
            },
            "required": ["summary"],
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> str:
        summary = str(params.get("summary", "")).strip()
        if not summary:
            raise PermanentToolError("summary is required", tool_name=self.name)

        deps = getattr(context, "deps", None)
        board = getattr(deps, "state_board", None) if deps else None
        if board is None:
            raise PermanentToolError("StateBoard not available", tool_name=self.name)

        board._final_summary = summary
        board.log_event("orchestrator.finalized", message=summary)
        return summary

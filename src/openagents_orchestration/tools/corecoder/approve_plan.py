"""approve_plan — signal that the generated plan has been approved.

Used in plan mode: the agent stops after generating `.agent_plan.md` and
waits for this tool to be invoked before continuing with execution.
"""

from __future__ import annotations

from typing import Any

from openagents.errors.exceptions import PermanentToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


class ApprovePlanTool(ToolPlugin):
    """Approve the current plan and resume execution."""

    name = "approve_plan"
    description = (
        "Approve the plan generated in plan mode and allow the agent to proceed "
        "with execution. Call this after reviewing .agent_plan.md."
    )
    durable_idempotent = True

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(
            concurrency_safe=True,
            side_effects="writes_state",
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "notes": {
                    "type": "string",
                    "description": "Optional notes or modifications to the approved plan.",
                },
            },
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        notes = str(params.get("notes", "")).strip()
        ctx = context
        if ctx is None:
            raise PermanentToolError("RunContext required", tool_name=self.name)
        ctx.state["__plan_approved__"] = True
        if notes:
            ctx.state["__plan_notes__"] = notes
        return {
            "approved": True,
            "message": "Plan approved. The agent may now proceed with execution.",
        }

"""read_resident_state — read the status and latest output of a resident agent."""

from __future__ import annotations

from typing import Any

from openagents.errors.exceptions import PermanentToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


class ReadResidentStateTool(ToolPlugin):
    """Read the current state of a resident agent.

    Returns status, latest output, token usage, and uptime.
    """

    name = "read_resident_state"
    description = (
        "Read the current state of a persistent resident agent. "
        "Returns status (idle/busy/error/stopped), latest output, "
        "token usage, message count, and uptime."
    )
    durable_idempotent = True

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(concurrency_safe=True, side_effects="read_only")

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "resident_id": {
                    "type": "string",
                    "description": "The resident ID.",
                },
            },
            "required": ["resident_id"],
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        resident_id = str(params.get("resident_id", "")).strip()
        if not resident_id:
            raise PermanentToolError("resident_id is required", tool_name=self.name)

        deps = getattr(context, "deps", None)
        board = getattr(deps, "state_board", None) if deps else None
        if board is None:
            raise PermanentToolError("StateBoard not available", tool_name=self.name)

        resident = board.get_resident(resident_id)
        if resident is None:
            raise PermanentToolError(
                f"Resident '{resident_id}' not found", tool_name=self.name
            )

        return {
            "resident_id": resident_id,
            "agent_type": resident.agent_type,
            **resident.to_dict(),
        }

"""spawn_resident — start a persistent resident agent."""

from __future__ import annotations

from typing import Any

from openagents.errors.exceptions import PermanentToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


class SpawnResidentTool(ToolPlugin):
    """Start a persistent resident agent that stays in memory.

    Unlike spawn_agent (one-shot), resident agents loop waiting for messages
    and process them with persistent transcript history.
    """

    name = "spawn_resident"
    description = (
        "Start a persistent resident agent. The agent stays in memory, "
        "waits for messages via send_to_resident, and processes them with "
        "persistent conversation history. Use for roles that need ongoing "
        "collaboration (e.g. a lead coder that handles multiple tasks)."
    )
    durable_idempotent = False

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(
            concurrency_safe=False,
            side_effects="external",
            default_timeout_ms=30_000,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "agent_type": {
                    "type": "string",
                    "description": "Agent type to start (coder, reviewer, tester, researcher).",
                },
            },
            "required": ["agent_type"],
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        agent_type = str(params.get("agent_type", "")).strip()
        if not agent_type:
            raise PermanentToolError("agent_type is required", tool_name=self.name)

        deps = getattr(context, "deps", None)
        runner = getattr(deps, "runner", None) if deps else None
        if runner is None:
            raise PermanentToolError("Runner not available", tool_name=self.name)

        try:
            resident_id = await runner.start_resident(agent_type)
        except Exception as exc:
            raise PermanentToolError(
                f"Failed to start resident: {exc}", tool_name=self.name
            ) from exc

        return {
            "resident_id": resident_id,
            "agent_type": agent_type,
            "status": "started",
            "message": f"Resident {agent_type} started as {resident_id}",
        }

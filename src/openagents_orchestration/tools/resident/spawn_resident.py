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
        "Start a persistent resident agent that stays in memory and processes "
        "messages asynchronously. Unlike spawn_agent (one-shot), a resident keeps "
        "its transcript and state across multiple turns.\n\n"
        "Use spawn_resident when:\n"
        "- A task needs sustained iteration (debugging, complex refactoring, multi-turn coding).\n"
        "- You want a dedicated worker that handles a stream of related sub-tasks.\n"
        "- A spawn_agent task failed because it needed more back-and-forth than a single context allows.\n\n"
        "Do NOT use spawn_resident when:\n"
        "- The task is a single, bounded unit of work — use spawn_agent instead.\n"
        "- You only need a one-time answer — use spawn_agent instead.\n"
        "- You are not ready to send work immediately — an idle resident may time out.\n\n"
        "After spawning, you MUST immediately call send_to_resident with the first task. "
        "Returns a resident_id used for subsequent send_to_resident and stop_resident calls."
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

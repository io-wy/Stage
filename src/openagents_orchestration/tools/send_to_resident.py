"""send_to_resident — send a task/message to a persistent resident agent."""

from __future__ import annotations

from typing import Any

from openagents.errors.exceptions import PermanentToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


class SendToResidentTool(ToolPlugin):
    """Send a task or message to a resident agent.

    The message is enqueued; the resident will process it asynchronously.
    Use read_resident_state to check the result later.
    """

    name = "send_to_resident"
    description = (
        "Send a task or message to a persistent resident agent. "
        "The resident processes it asynchronously. Use read_resident_state "
        "to check the result."
    )
    durable_idempotent = True

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(concurrency_safe=True, side_effects="writes_state")

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "resident_id": {
                    "type": "string",
                    "description": "The resident ID returned by spawn_resident.",
                },
                "task": {
                    "type": "string",
                    "description": "What the resident should do.",
                },
                "content": {
                    "type": "string",
                    "description": "Additional message content.",
                },
                "context": {
                    "type": "string",
                    "description": "Extra context (file paths, error messages, etc.).",
                },
            },
            "required": ["resident_id", "task"],
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        resident_id = str(params.get("resident_id", "")).strip()
        task = str(params.get("task", "")).strip()
        content = str(params.get("content", "")).strip()
        ctx = str(params.get("context", "")).strip()

        if not resident_id or not task:
            raise PermanentToolError(
                "resident_id and task are required", tool_name=self.name
            )

        deps = getattr(context, "deps", None)
        runner = getattr(deps, "runner", None) if deps else None
        if runner is None:
            raise PermanentToolError("Runner not available", tool_name=self.name)

        from_agent = getattr(context, "agent_id", "director")
        try:
            await runner.send_to_resident(
                resident_id,
                task=task,
                content=content,
                context=ctx,
                from_id=from_agent,
            )
        except Exception as exc:
            raise PermanentToolError(
                f"Failed to send to resident: {exc}", tool_name=self.name
            ) from exc

        return {
            "resident_id": resident_id,
            "status": "sent",
            "message": f"Task sent to {resident_id}. Use read_resident_state to check progress.",
        }

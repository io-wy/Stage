"""send_message — async message passing between agents via StateBoard."""

from __future__ import annotations

from typing import Any

from openagents.errors.exceptions import PermanentToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


class SendMessageTool(ToolPlugin):
    """Send a message to another agent (or all agents).

    The message is stored in the StateBoard and will be delivered to the
    recipient the next time they are spawned or check their mailbox.
    """

    name = "send_message"
    description = (
        "Send a message to another agent. Use for: requesting help, "
        "sharing findings, asking clarifying questions. The recipient "
        "will receive this message the next time they run."
    )
    durable_idempotent = True

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(concurrency_safe=True, side_effects="writes_state")

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "to_agent": {
                    "type": "string",
                    "description": (
                        "Target agent ID or task ID. Use 'director' to message "
                        "the orchestrator. Use '*' to broadcast to all agents."
                    ),
                },
                "message": {
                    "type": "string",
                    "description": "The message content. Be specific and concise.",
                },
            },
            "required": ["to_agent", "message"],
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> str:
        to_agent = str(params.get("to_agent", "")).strip()
        message = str(params.get("message", "")).strip()
        if not to_agent or not message:
            raise PermanentToolError(
                "to_agent and message are required", tool_name=self.name
            )

        deps = getattr(context, "deps", None)
        board = getattr(deps, "state_board", None) if deps else None
        if board is None:
            raise PermanentToolError("StateBoard not available", tool_name=self.name)

        from_agent = getattr(context, "agent_id", "unknown")
        board.log_event(
            "message.sent",
            agent_id=from_agent,
            message=f"to={to_agent}: {message[:200]}",
            payload={"from": from_agent, "to": to_agent, "content": message},
        )

        # Store via StateBoard mailbox API
        board.send_mail(from_agent, to_agent, message)

        return f"Message sent to {to_agent}."

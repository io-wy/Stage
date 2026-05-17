"""check_messages — pull-based message retrieval for agent-to-agent communication.

Agents call this tool periodically (every 3-5 turns) to check if other agents
or the director have sent them messages. Returns a list of pending messages
and clears them from the queue.
"""

from __future__ import annotations

import json
from typing import Any

from openagents.errors.exceptions import PermanentToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


class CheckMessagesTool(ToolPlugin):
    """Check for pending messages addressed to this agent."""

    name = "check_messages"
    description = (
        "Check if other agents or the director have sent you messages. "
        "Call this every 3-5 turns to stay aware of async communication. "
        "Returns pending messages and clears them from your queue."
    )
    durable_idempotent = True

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(concurrency_safe=True, side_effects="reads_state")

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "clear": {
                    "type": "boolean",
                    "description": "Whether to clear messages after reading. Default true.",
                },
            },
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        deps = getattr(context, "deps", None)
        board = getattr(deps, "state_board", None) if deps else None
        if board is None:
            raise PermanentToolError("StateBoard not available", tool_name=self.name)

        agent_id = getattr(context, "agent_id", "unknown")

        # Collect messages addressed to this agent via mailbox API
        matching = board.messages_for(agent_id)

        clear = params.get("clear", True)
        if clear and matching:
            board.clear_mail(agent_id)

        if not matching:
            return {
                "message": "No new messages.",
                "count": 0,
                "messages": [],
            }

        formatted = []
        for m in matching:
            formatted.append(f"From {m['from']}: {m['content']}")

        return {
            "message": f"You have {len(matching)} message(s):\n" + "\n".join(formatted),
            "count": len(matching),
            "messages": matching,
        }

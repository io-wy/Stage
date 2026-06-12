"""check_dlq — inspect dead-letter queues for all agents.

The Director calls this periodically to discover messages that failed
repeated delivery and decide whether to retry, replan, or ask a human.
"""

from __future__ import annotations

from typing import Any

from openagents.errors.exceptions import PermanentToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


class CheckDLQTool(ToolPlugin):
    """Return a summary of dead-letter messages across all agent mailboxes."""

    name = "check_dlq"
    description = (
        "Inspect dead-letter queues (DLQ) for all agents. "
        "Use this every few cycles to detect messages that failed repeated "
        "delivery. Returns a summary of stuck messages per agent."
    )
    durable_idempotent = True

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(concurrency_safe=True, side_effects="reads_state")

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max DLQ entries to return per agent. Default 3.",
                },
            },
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        deps = getattr(context, "deps", None)
        board = getattr(deps, "state_board", None) if deps else None
        if board is None:
            raise PermanentToolError("StateBoard not available", tool_name=self.name)

        limit = int(params.get("limit", 3))
        dlq_summary = await board.inspect_dlq()

        # Enrich with latest messages up to limit
        enriched: dict[str, Any] = {}
        for agent_id, info in dlq_summary.items():
            mbox = board._get_or_create_mailbox(agent_id)
            latest = await mbox.dlq_peek(limit=limit)
            enriched[agent_id] = {
                "size": info["size"],
                "latest": latest,
            }

        board.log_event(
            "dlq.checked",
            agent_id=getattr(context, "agent_id", "director"),
            message=f"agents_with_dlq={list(enriched.keys())}",
            payload={"agents": list(enriched.keys()), "total_dlq": sum(i["size"] for i in enriched.values())},
        )

        if not enriched:
            return {
                "message": "No dead-letter messages found.",
                "count": 0,
                "agents": {},
            }

        lines = [f"DLQ summary ({len(enriched)} agent(s)):"]
        for agent_id, info in enriched.items():
            lines.append(f"- {agent_id}: {info['size']} message(s)")
            for entry in info["latest"]:
                reason = entry.get("reason", "unknown")
                lines.append(f"  - {entry.get('msg_id', '?')}: {reason}")

        return {
            "message": "\n".join(lines),
            "count": sum(i["size"] for i in enriched.values()),
            "agents": enriched,
        }

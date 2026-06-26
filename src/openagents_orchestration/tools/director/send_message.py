"""send_message — async message passing between agents via StateBoard Mailbox v2."""

from __future__ import annotations

import contextlib
from typing import Any

from openagents.errors.exceptions import PermanentToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin

from openagents_orchestration.core.collaboration import (
    parse_collaboration_message,
    task_id_from_resident_id,
)
from openagents_orchestration.models.message import (
    MessageType,
    Priority,
    StructuredMessage,
)


class SendMessageTool(ToolPlugin):
    """Send a message to another agent (or all agents) via StateBoard Mailbox v2."""

    name = "send_message"
    description = (
        "Send an asynchronous message to another agent's mailbox. "
        "The recipient will see it the next time they read their mailbox.\n\n"
        "Use send_message when:\n"
        "- You need help from another role (e.g. coder asks reviewer for a quick look).\n"
        "- You want to share a finding that another agent needs to act on.\n"
        "- You are a producer in collaborative mode signaling TASK_REVIEW_READY to your checker.\n"
        "- You are a checker signaling TASK_APPROVED or TASK_FIX_NEEDED to your producer.\n\n"
        "Do NOT use send_message when:\n"
        "- The message is a task assignment — use spawn_agent or send_to_resident instead.\n"
        "- You need an immediate synchronous answer — the recipient processes mail on their own schedule.\n"
        "- You are the Director assigning work — use spawn_agent with task_ids.\n\n"
        "Target formats:\n"
        "- 'director' to reach the orchestrator.\n"
        "- A specific agent_id or task_id to reach one agent.\n"
        "- '*' to broadcast to all registered agents.\n"
        "- 'topic:foo' to publish to subscribers of that topic."
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
                        "the orchestrator. Use '*' to broadcast to all agents. "
                        "Use 'topic:foo' to publish to a topic."
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
        runner = getattr(deps, "runner", None) if deps else None
        if board is None:
            raise PermanentToolError("StateBoard not available", tool_name=self.name)

        from_agent = getattr(context, "agent_id", "unknown")

        # Detect collaboration signals and emit structured messages
        collab = parse_collaboration_message(
            message, default_task_id=task_id_from_resident_id(from_agent)
        )
        if collab is not None:
            msg = StructuredMessage.signal(
                from_agent,
                to_agent,
                collab.signal.value,
                collab.task_id,
                priority=Priority.CRITICAL,
                text=message,
                ttl_s=300.0,
                tests_passed=collab.tests_passed,
            )
        else:
            msg = StructuredMessage.from_text(
                from_agent,
                to_agent,
                message,
                msg_type=MessageType.NOTIFICATION,
            )

        # Deliver via structured mailbox (v2) FIRST — if channel policy or
        # back-pressure blocks it, don't deliver to resident either.  This
        # prevents inconsistent state where the resident thinks it has a
        # message but the system blocked it.
        delivered = await board.send_structured(msg)
        if not delivered:
            return (
                f"Message to {to_agent} was blocked by channel policy "
                f"or mailbox is full."
            )

        # Collaboration signals are processed by the orchestrator's
        # collaboration loop (state machine), not by the target resident
        # directly.  Direct-inbox delivery would bypass the state machine and
        # cause the signal to be consumed as ordinary chat.  Only non-signal
        # messages are delivered directly to an active, awake resident.
        is_signal = collab is not None
        resident_delivered = False
        if runner is not None and hasattr(runner, "_residents") and not is_signal:
            resident = runner._residents.get(to_agent)
            if (
                resident is not None
                and getattr(resident, "_active", False)
                and not getattr(resident, "_sleeping", False)
            ):
                with contextlib.suppress(Exception):
                    resident.send_nowait({
                        "task": "",
                        "content": message,
                        "from": from_agent,
                        "msg_id": msg.msg_id,
                    })
                    resident_delivered = True

        # Ack the mailbox copy when delivered directly so a later peek/claim
        # won't deliver a duplicate.  Match by msg_id.
        if resident_delivered:
            with contextlib.suppress(Exception):
                await board.ack_message(to_agent, msg.msg_id)

        board.log_event(
            "message.sent",
            agent_id=from_agent,
            message=f"to={to_agent}: {message[:200]}",
            payload={
                "from": from_agent,
                "to": to_agent,
                "content": message,
                "resident_delivered": resident_delivered,
            },
        )

        # Mirror to Matrix if transport is enabled
        matrix_transport = getattr(deps, "matrix_transport", None)
        if matrix_transport is not None and matrix_transport.enabled and to_agent != "*":
            try:
                room_name = f"dm-{from_agent}-{to_agent}"
                room_id = await matrix_transport.create_room(
                    name=room_name,
                    invite=[],
                    agent_id=from_agent,
                )
                if room_id:
                    await matrix_transport.send(
                        room_id, f"[{from_agent}] {message}", agent_id=from_agent
                    )
            except Exception as exc:
                board.log_event(
                    "matrix.send_error",
                    agent_id=from_agent,
                    message=f"to={to_agent}: {exc}",
                )

        if resident_delivered:
            return f"Message delivered directly to resident {to_agent} and stored in mailbox."
        return f"Message sent to {to_agent}."

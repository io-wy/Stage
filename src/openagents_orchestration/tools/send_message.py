"""send_message — async message passing between agents via StateBoard."""

from __future__ import annotations

from typing import Any

from openagents.errors.exceptions import PermanentToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin

from openagents_orchestration.collaboration import task_id_from_resident_id


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
        runner = getattr(deps, "runner", None) if deps else None
        if board is None:
            raise PermanentToolError("StateBoard not available", tool_name=self.name)

        from_agent = getattr(context, "agent_id", "unknown")

        # If target is an active resident, deliver directly to its inbox
        # for real-time collaboration. Otherwise fall back to mailbox.
        resident_delivered = False
        if runner is not None and hasattr(runner, "_residents"):
            resident = runner._residents.get(to_agent)
            if resident is not None and getattr(resident, "_active", False):
                try:
                    resident.send_nowait({
                        "task": "",
                        "content": message,
                        "from": from_agent,
                    })
                    resident_delivered = True
                except Exception:
                    resident_delivered = False

        board.log_event(
            "message.sent",
            agent_id=from_agent,
            message=f"to={to_agent}: {message[:200]}",
            payload={"from": from_agent, "to": to_agent, "content": message, "resident_delivered": resident_delivered},
        )

        # Always store in mailbox as well for audit / offline retrieval
        board.send_mail(from_agent, to_agent, message)

        # Mirror to Matrix if transport is enabled
        matrix_transport = getattr(deps, "matrix_transport", None)
        if matrix_transport is not None and matrix_transport.enabled:
            try:
                room_name = f"dm-{from_agent}-{to_agent}"
                room_id = await matrix_transport.create_room(
                    name=room_name,
                    invite=[to_agent] if to_agent != "*" else [],
                )
                if room_id:
                    await matrix_transport.send(room_id, f"[{from_agent}] {message}")
            except Exception as exc:
                board.log_event(
                    "matrix.send_error",
                    agent_id=from_agent,
                    message=f"to={to_agent}: {exc}",
                )

        # If this looks like a task collaboration thread message, also record
        # it in the conversation thread so the orchestrator can parse state
        # transitions (TASK_REVIEW_READY / TASK_APPROVED / TASK_FIX_NEEDED).
        if to_agent != "director" and to_agent != "*":
            thread_added = False
            try:
                thread_id_candidates = []
                for tid, thread in board.conversation_threads.items():
                    if from_agent in thread.participants and to_agent in thread.participants:
                        thread_id_candidates.append(tid)
                if not thread_id_candidates:
                    for agent_id in (to_agent, from_agent):
                        task_id = task_id_from_resident_id(agent_id)
                        if task_id:
                            thread_id_candidates.append(f"task-{task_id}")
                for tid in dict.fromkeys(thread_id_candidates):
                    thread = board.conversation_threads.get(tid)
                    if thread is not None:
                        if from_agent in thread.participants and to_agent in thread.participants:
                            thread.add_message(from_agent, message, task_id=tid.replace("task-", ""))
                            thread_added = True
                            break
                        else:
                            board.log_event(
                                "thread.skip_participants",
                                agent_id=from_agent,
                                message=f"tid={tid} participants={list(thread.participants)}",
                            )
                    else:
                        board.log_event(
                            "thread.skip_missing",
                            agent_id=from_agent,
                            message=f"tid={tid} candidates={thread_id_candidates}",
                        )
            except Exception as exc:
                board.log_event(
                    "thread.add_error",
                    agent_id=from_agent,
                    message=f"{exc}",
                )
            board.log_event(
                "thread.add_attempt",
                agent_id=from_agent,
                message=f"to={to_agent} added={thread_added}",
            )

        if resident_delivered:
            return f"Message delivered directly to resident {to_agent} and stored in mailbox."
        return f"Message sent to {to_agent}."

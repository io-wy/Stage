"""check_messages — pull-based message retrieval via Mailbox v2.

Agents call this tool periodically to claim messages from their isolated
mailbox. Messages are acknowledged after being returned.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openagents.errors.exceptions import PermanentToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin

from openagents_orchestration.models.message import StructuredMessage


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
                "batch_size": {
                    "type": "integer",
                    "description": "Max messages to retrieve. Default 10.",
                },
            },
        }

    def _read_inbox(self, board: Any) -> list[dict[str, Any]]:
        """Read external messages from inbox file and inject into mailbox."""
        inbox_messages: list[dict[str, Any]] = []
        recorder = getattr(board, "_recorder", None)
        if recorder is None:
            return inbox_messages

        persist_dir = getattr(recorder, "_session_dir", None)
        if persist_dir is None:
            return inbox_messages

        inbox_file = Path(persist_dir) / "inbox.jsonl"
        if not inbox_file.exists():
            return inbox_messages

        try:
            text = inbox_file.read_text(encoding="utf-8").strip()
            if not text:
                return inbox_messages
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    inbox_messages.append(msg)
                except json.JSONDecodeError:
                    continue
            # Clear inbox after reading
            inbox_file.write_text("", encoding="utf-8")
            # Inject into StateBoard mailbox via legacy sync API
            for msg in inbox_messages:
                board.send_mail(
                    from_id=msg.get("from", "human"),
                    to_id=msg.get("to", "director"),
                    content=msg.get("content", ""),
                )
        except Exception:
            pass

        return inbox_messages

    @staticmethod
    def _format_message(msg: StructuredMessage) -> str:
        """Convert a structured message to human-readable text."""
        prefix = f"From {msg.header.sender}"
        if msg.header.msg_type.value != "notification":
            prefix += f" [{msg.header.msg_type.value}]"
        return f"{prefix}: {msg.text or str(msg.payload)}"

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        deps = getattr(context, "deps", None)
        board = getattr(deps, "state_board", None) if deps else None
        if board is None:
            raise PermanentToolError("StateBoard not available", tool_name=self.name)

        agent_id = getattr(context, "agent_id", "unknown")
        batch_size = int(params.get("batch_size", 10))
        clear = params.get("clear", True)

        # First: inject external inbox messages (legacy path)
        self._read_inbox(board)

        # Pull from Matrix if transport is enabled
        matrix_transport = getattr(deps, "matrix_transport", None)
        if matrix_transport is not None and matrix_transport.enabled:
            try:
                mx_messages = await matrix_transport.receive(agent_id=agent_id)
                for mx in mx_messages:
                    board.send_mail(
                        from_id=mx.get("sender", "matrix"),
                        to_id=agent_id,
                        content=mx.get("body", ""),
                    )
            except Exception as exc:
                board.log_event(
                    "matrix.receive_error",
                    agent_id=agent_id,
                    message=str(exc),
                )

        # Read messages from Mailbox v2
        if clear:
            structured = await board.claim_messages(agent_id, batch_size=batch_size)
            ack_ids = [m.msg_id for m in structured]
            for mid in ack_ids:
                await board.ack_message(agent_id, mid)
        else:
            structured = await board.peek_mailbox(agent_id, limit=batch_size)

        all_messages: list[dict[str, Any]] = []
        for m in structured:
            all_messages.append({
                "from": m.header.sender,
                "content": m.text,
                "type": m.header.msg_type.value,
                "payload": m.payload,
                "trace_id": m.header.trace_id,
                "msg_id": m.msg_id,
                "ts": m.header.created_at.timestamp(),
            })

        if not all_messages:
            return {
                "message": "No new messages.",
                "count": 0,
                "messages": [],
            }

        formatted = [self._format_message(m) for m in structured]

        return {
            "message": f"You have {len(all_messages)} message(s):\n" + "\n".join(formatted),
            "count": len(all_messages),
            "messages": all_messages,
        }

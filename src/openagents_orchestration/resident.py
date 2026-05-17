"""ResidentAgent — persistent agent that stays in memory and processes messages.

Wraps CoreCoderPattern in an event loop: waits for messages on an asyncio.Queue,
runs the pattern for each message (with persistent transcript), and sends replies
via send_message.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ResidentState:
    """Runtime state of a resident agent, stored in StateBoard."""

    resident_id: str
    agent_type: str
    status: str = "idle"  # idle | busy | error | stopped
    latest_output: str = ""
    latest_task: str = ""
    token_used: int = 0
    message_count: int = 0
    error_count: int = 0
    start_time: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resident_id": self.resident_id,
            "agent_type": self.agent_type,
            "status": self.status,
            "latest_task": self.latest_task,
            "token_used": self.token_used,
            "message_count": self.message_count,
            "error_count": self.error_count,
            "uptime_s": round(time.time() - self.start_time, 1),
            "idle_s": round(time.time() - self.last_active, 1),
        }


class ResidentAgent:
    """Persistent agent that loops waiting for messages.

    Each message triggers a one-shot CoreCoderPattern run with persistent
    transcript history. Replies are sent via send_message back to the caller
    or broadcast.
    """

    def __init__(
        self,
        resident_id: str,
        agent_type: str,
        runner: Any,
        board: Any,
        max_idle_s: float = 300.0,
    ):
        self.resident_id = resident_id
        self.agent_type = agent_type
        self._runner = runner
        self._board = board
        self._max_idle_s = max_idle_s
        self._inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task[Any] | None = None
        self._active = False
        self._transcript: list[dict[str, Any]] = []
        self._state = ResidentState(resident_id=resident_id, agent_type=agent_type)
        self._persist_path = Path(f".residents/{resident_id}.json")

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        self._active = True
        # Load persisted transcript if exists
        if self._persist_path.exists():
            try:
                data = json.loads(self._persist_path.read_text(encoding="utf-8"))
                self._transcript = data.get("transcript", [])
            except (json.JSONDecodeError, OSError):
                self._transcript = []
        self._board.register_resident(self._state)
        self._board.log_event(
            "resident.started",
            agent_id=self.resident_id,
            message=f"Resident {self.agent_type} started",
        )
        self._task = asyncio.create_task(self._loop(), name=f"resident-{self.resident_id}")

    async def stop(self) -> None:
        self._active = False
        self._state.status = "stopped"
        self._board.update_resident(self.resident_id, status="stopped")
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._board.log_event(
            "resident.stopped",
            agent_id=self.resident_id,
            message=f"Resident {self.agent_type} stopped",
        )

    # -- messaging -----------------------------------------------------------

    async def send(self, message: dict[str, Any]) -> None:
        """Enqueue a message for this resident."""
        await self._inbox.put(message)

    def send_nowait(self, message: dict[str, Any]) -> None:
        """Non-blocking enqueue."""
        self._inbox.put_nowait(message)

    # -- main loop -----------------------------------------------------------

    async def _loop(self) -> None:
        """Wait for messages, process each with CoreCoderPattern."""
        while self._active:
            try:
                msg = await asyncio.wait_for(
                    self._inbox.get(),
                    timeout=self._max_idle_s,
                )
            except asyncio.TimeoutError:
                # Idle timeout — auto-stop to save resources
                self._board.log_event(
                    "resident.idle_timeout",
                    agent_id=self.resident_id,
                    message=f"Idle for {self._max_idle_s}s, auto-stopping",
                )
                await self.stop()
                break

            self._state.status = "busy"
            self._state.last_active = time.time()
            self._state.message_count += 1
            self._state.latest_task = msg.get("task", "")[:100]
            self._board.update_resident(
                self.resident_id,
                status="busy",
                last_active=self._state.last_active,
                message_count=self._state.message_count,
                latest_task=self._state.latest_task,
            )

            try:
                result = await self._process_message(msg)
                self._state.latest_output = result[:500]
                self._state.status = "idle"
                self._state.last_active = time.time()
                self._board.update_resident(
                    self.resident_id,
                    status="idle",
                    last_active=self._state.last_active,
                    latest_output=self._state.latest_output,
                )
            except Exception as exc:
                self._state.error_count += 1
                self._state.status = "error"
                self._board.update_resident(
                    self.resident_id,
                    status="error",
                    error_count=self._state.error_count,
                )
                self._board.log_event(
                    "resident.error",
                    agent_id=self.resident_id,
                    message=str(exc)[:200],
                )
                # Send error back as reply
                await self._send_reply(
                    to=msg.get("from", "director"),
                    content=f"[Error processing your request: {exc}]",
                )

    async def _process_message(self, msg: dict[str, Any]) -> str:
        """Run one-shot CoreCoderPattern for a single message.

        Builds input text from message fields, passes persistent transcript,
        and sends the result back via send_message.
        """
        input_text = self._build_input(msg)

        # Run the agent via runner's internal method
        result = await self._runner._run_resident_single(
            resident_id=self.resident_id,
            agent_type=self.agent_type,
            input_text=input_text,
            transcript=list(self._transcript),
        )

        # Update persistent transcript from result metadata
        result_meta = getattr(result, "metadata", None) or {}
        new_transcript = result_meta.get("transcript")
        if new_transcript:
            self._transcript = list(new_transcript)

        # Track token usage
        usage = getattr(result, "usage", None)
        if usage is not None:
            total = getattr(usage, "total_tokens", 0) or 0
            self._state.token_used += total
            self._board.update_resident(
                self.resident_id, token_used=self._state.token_used
            )

        final_output = str(getattr(result, "final_output", "") or "")

        # Persist transcript to disk
        self._save_transcript()

        # Send reply back to sender (or broadcast if no sender)
        reply_to = msg.get("from", "director")
        if final_output.strip():
            await self._send_reply(to=reply_to, content=final_output)

        return final_output

    def _save_transcript(self) -> None:
        """Save persistent transcript to disk."""
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            self._persist_path.write_text(
                json.dumps(
                    {
                        "transcript": self._transcript,
                        "last_updated": time.time(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass  # Best-effort persistence

    @staticmethod
    def _build_input(msg: dict[str, Any]) -> str:
        """Compose input text from message envelope."""
        parts: list[str] = []

        if msg.get("task"):
            parts.append(f"# Task\n{msg['task']}")

        if msg.get("content"):
            sender = msg.get("from", "unknown")
            parts.append(f"# Message from {sender}\n{msg['content']}")

        if msg.get("context"):
            parts.append(f"# Additional context\n{msg['context']}")

        if not parts:
            parts.append("# Request\nProcess this request.")

        return "\n\n".join(parts)

    async def _send_reply(self, *, to: str, content: str) -> None:
        """Send reply back via StateBoard mailbox."""
        self._board.send_mail(self.resident_id, to, content[:2000])
        self._board.log_event(
            "resident.replied",
            agent_id=self.resident_id,
            message=f"Reply to {to}: {content[:100]}",
        )

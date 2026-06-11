"""ResidentAgent — persistent agent that stays in memory and processes messages.

Wraps CoreCoderPattern in an event loop: waits for messages on an asyncio.Queue,
runs the pattern for each message (with persistent transcript), and sends replies
via send_message.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class AgentLifecycle(StrEnum):
    """Four-state lifecycle for resident agents."""

    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"
    STOPPED = "stopped"
    SLEEPING = "sleeping"


@dataclass
class ResidentState:
    """Runtime state of a resident agent, stored in StateBoard."""

    resident_id: str
    agent_type: str
    status: str = AgentLifecycle.IDLE  # idle | busy | error | stopped | sleeping
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
            "latest_output": self.latest_output,
            "latest_task": self.latest_task,
            "token_used": self.token_used,
            "message_count": self.message_count,
            "error_count": self.error_count,
            "start_time": self.start_time,
            "last_active": self.last_active,
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
        persist_dir: Path | None = None,
        run_budget: Any | None = None,
    ):
        self.resident_id = resident_id
        self.agent_type = agent_type
        self._runner = runner
        self._board = board
        self._max_idle_s = max_idle_s
        self._run_budget = run_budget
        self._inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task[Any] | None = None
        self._active = False
        self._sleeping = False
        self._transcript: list[dict[str, Any]] = []
        self._state = ResidentState(resident_id=resident_id, agent_type=agent_type)
        if persist_dir is not None:
            self._persist_path = Path(persist_dir) / "residents" / f"{resident_id}.json"
        else:
            self._persist_path = Path(f".residents/{resident_id}.json")

        # Task binding for iterative work (new)
        self._bound_task_id: str | None = None
        self._auto_verify: bool = False  # run tests after code changes

    @property
    def state(self) -> ResidentState:
        return self._state

    # -- task binding (iterative work) ---------------------------------------

    def bind_task(self, task_id: str, *, auto_verify: bool = False) -> None:
        """Bind this resident to a task for iterative work."""
        self._bound_task_id = task_id
        self._auto_verify = auto_verify
        self._state.latest_task = task_id
        if self._board is not None:
            self._board.bind_agent_to_task(self.resident_id, task_id)

    def unbind_task(self) -> None:
        """Unbind from current task."""
        if self._bound_task_id and self._board is not None:
            self._board.unbind_agent_from_task(self._bound_task_id)
        self._bound_task_id = None
        self._auto_verify = False

    @property
    def bound_task_id(self) -> str | None:
        return self._bound_task_id

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        self._active = True
        self._sleeping = False
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
        self._sleeping = False
        self._state.status = AgentLifecycle.STOPPED
        self._board.update_resident(self.resident_id, status=AgentLifecycle.STOPPED)
        current = asyncio.current_task()
        if self._task is not None and self._task is not current:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._board.log_event(
            "resident.stopped",
            agent_id=self.resident_id,
            message=f"Resident {self.agent_type} stopped",
        )

    async def sleep(self, reason: str = "") -> None:
        """Gracefully pause the resident: stop consuming messages, preserve transcript.

        The resident remains in memory and can be woken with ``wake()``.
        After ``max_idle_s`` in sleep it auto-stops to save resources.
        """
        if self._sleeping or not self._active:
            return
        self._sleeping = True
        self._state.status = AgentLifecycle.SLEEPING
        self._board.update_resident(self.resident_id, status=AgentLifecycle.SLEEPING)
        self._board.log_event(
            "resident.sleep",
            agent_id=self.resident_id,
            message=f"Sleeping: {reason}"[:200],
        )
        # Inject sentinel to break out of the normal wait_for in _loop
        await self._inbox.put({"__sleep": True, "from": "system"})

    async def wake(self) -> None:
        """Wake a sleeping resident so it resumes message processing."""
        if not self._sleeping:
            return
        await self._inbox.put({"__wake": True, "from": "system"})

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
            except TimeoutError:
                if self._sleeping:
                    # Slept too long — auto-stop to save resources
                    self._board.log_event(
                        "resident.sleep_timeout",
                        agent_id=self.resident_id,
                        message=f"Sleep timeout after {self._max_idle_s}s, auto-stopping",
                    )
                else:
                    self._board.log_event(
                        "resident.idle_timeout",
                        agent_id=self.resident_id,
                        message=f"Idle for {self._max_idle_s}s, auto-stopping",
                    )
                self._active = False
                self._sleeping = False
                self._state.status = AgentLifecycle.STOPPED
                self._board.update_resident(self.resident_id, status=AgentLifecycle.STOPPED)
                self._board.log_event(
                    "resident.stopped",
                    agent_id=self.resident_id,
                    message=f"Resident {self.agent_type} stopped",
                )
                break

            # Sentinel handling for sleep/wake
            if msg.get("__sleep"):
                # Enter sleep sub-loop: only react to __wake and heartbeat
                while self._sleeping and self._active:
                    try:
                        inner = await asyncio.wait_for(
                            self._inbox.get(),
                            timeout=self._max_idle_s,
                        )
                    except TimeoutError:
                        self._board.log_event(
                            "resident.sleep_timeout",
                            agent_id=self.resident_id,
                            message=f"Sleep timeout after {self._max_idle_s}s, auto-stopping",
                        )
                        self._active = False
                        self._sleeping = False
                        self._state.status = AgentLifecycle.STOPPED
                        self._board.update_resident(self.resident_id, status=AgentLifecycle.STOPPED)
                        break

                    if inner.get("__wake"):
                        self._sleeping = False
                        self._state.status = AgentLifecycle.IDLE
                        self._state.last_active = time.time()
                        self._board.update_resident(
                            self.resident_id,
                            status=AgentLifecycle.IDLE,
                            last_active=self._state.last_active,
                        )
                        self._board.log_event(
                            "resident.wake",
                            agent_id=self.resident_id,
                            message="Resident woken",
                        )
                        break

                    if inner.get("task") == "heartbeat":
                        # Reply to heartbeat even while sleeping
                        reply = self._build_heartbeat_reply(inner)
                        await self._send_reply(to=inner.get("from", "monitor"), content=reply)
                        self._state.last_active = time.time()
                        self._board.update_resident(
                            self.resident_id,
                            last_active=self._state.last_active,
                        )
                        continue

                    # Any other messages are queued for later processing after wake
                    # Re-queue them so they are processed in order after wake
                    await self._inbox.put(inner)
                continue

            if msg.get("__wake"):
                # Wake received outside sleep (e.g. race) — ignore
                continue

            # Normal message processing
            self._state.status = AgentLifecycle.BUSY
            self._state.last_active = time.time()
            self._state.message_count += 1
            self._state.latest_task = msg.get("task", "")[:100]
            self._board.update_resident(
                self.resident_id,
                status=AgentLifecycle.BUSY,
                last_active=self._state.last_active,
                message_count=self._state.message_count,
                latest_task=self._state.latest_task,
            )

            try:
                result = await self._process_message(msg)
                self._state.latest_output = result[:500]
                self._state.status = AgentLifecycle.IDLE
                self._state.last_active = time.time()
                self._board.update_resident(
                    self.resident_id,
                    status=AgentLifecycle.IDLE,
                    last_active=self._state.last_active,
                    latest_output=self._state.latest_output,
                )
            except Exception as exc:
                self._state.error_count += 1
                self._state.status = AgentLifecycle.ERROR
                self._board.update_resident(
                    self.resident_id,
                    status=AgentLifecycle.ERROR,
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
            budget=self._run_budget,
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

        stop_reason = getattr(result, "stop_reason", None)
        if stop_reason is not None and getattr(stop_reason, "value", stop_reason) == "failed":
            error = getattr(result, "error", None) or getattr(result, "error_message", None) or "resident run failed"
            raise RuntimeError(str(error))

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

    def _build_heartbeat_reply(self, msg: dict[str, Any]) -> str:
        """Build a heartbeat reply summarizing current state."""
        parts = ["HEARTBEAT_REPLY"]
        parts.append(f"status={self._state.status}")
        parts.append(f"task={self._state.latest_task or 'none'}")
        parts.append(f"messages={self._state.message_count}")
        parts.append(f"errors={self._state.error_count}")
        parts.append(f"token_used={self._state.token_used}")
        if self._bound_task_id:
            parts.append(f"bound_task={self._bound_task_id}")
        return "\n".join(parts)

    async def _send_reply(self, *, to: str, content: str) -> None:
        """Send reply back via StateBoard mailbox."""
        self._board.send_mail(self.resident_id, to, content[:2000])
        self._board.log_event(
            "resident.replied",
            agent_id=self.resident_id,
            message=f"Reply to {to}: {content[:100]}",
        )

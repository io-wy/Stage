"""MonitorAgent — proactive heartbeat monitor for enterprise orchestration.

Unlike the passive HealthMonitor (which scans StateBoard periodically),
MonitorAgent actively sends heartbeat requests to agents and
detects timeouts.  It also watches for DLQ growth and budget exhaustion.

Design:
- Runs as a background asyncio task inside GlobalOrchestrator
- Sends heartbeat SIGNAL messages to agents
- Expects heartbeat replies within HEARTBEAT_TIMEOUT_S
- On timeout: logs event, notifies GlobalDirector, optionally stops agent
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class HeartbeatRecord:
    """Record of a heartbeat exchange with an agent."""

    agent_id: str
    sent_at: float
    replied_at: float | None = None
    latency_ms: float = 0.0
    timed_out: bool = False


class MonitorAgent:
    """Active heartbeat monitor for all agents."""

    HEARTBEAT_INTERVAL_S = 60.0
    HEARTBEAT_TIMEOUT_S = 30.0
    MAX_MISSED_HEARTBEATS = 2

    def __init__(
        self,
        orchestrator: Any,
        *,
        heartbeat_interval_s: float = 60.0,
        heartbeat_timeout_s: float = 30.0,
        max_missed: int = 2,
    ):
        self._orchestrator = orchestrator
        self.heartbeat_interval_s = heartbeat_interval_s
        self.heartbeat_timeout_s = heartbeat_timeout_s
        self.max_missed = max_missed
        self._task: asyncio.Task[Any] | None = None
        self._running = False

        # Tracking
        self._last_heartbeat: dict[str, float] = {}
        self._missed_counts: dict[str, int] = {}
        self._latency_records: dict[str, list[float]] = {}
        self._records: list[HeartbeatRecord] = []

    # -- lifecycle -------------------------------------------------------------

    async def start(self) -> None:
        """Start the background heartbeat loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(
            self._heartbeat_loop(), name="monitor-agent"
        )

    async def stop(self) -> None:
        """Stop the background loop."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    # -- heartbeat loop --------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        while self._running:
            try:
                await self._send_heartbeats()
            except Exception as exc:
                self._log_event(
                    "monitor.error",
                    message=f"Heartbeat loop error: {exc}",
                )
            await asyncio.sleep(self.heartbeat_interval_s)

    async def _send_heartbeats(self) -> None:
        """Send heartbeat requests to all active agents."""
        agents = self._get_agents()
        now = time.time()

        for agent in agents:
            aid = agent.agent_id
            self._last_heartbeat[aid] = now

            # Send heartbeat as a structured message
            hb_msg = {
                "task": "heartbeat",
                "content": "ping",
                "from": "monitor",
                "timestamp": now,
            }
            try:
                await self._send_to_agent(aid, hb_msg)
            except Exception as exc:
                self._log_event(
                    "agent.heartbeat_timeout",
                    agent_id=aid,
                    message=f"Failed to send heartbeat: {exc}",
                )
                await self._handle_timeout(aid)
                continue

            # Wait for reply with timeout
            replied = await self._await_reply(agent, timeout=self.heartbeat_timeout_s)
            if not replied:
                self._missed_counts[aid] = self._missed_counts.get(aid, 0) + 1
                if self._missed_counts[aid] >= self.max_missed:
                    await self._handle_timeout(aid)
            else:
                self._missed_counts[aid] = 0
                latency_ms = (time.time() - now) * 1000
                self._latency_records.setdefault(aid, []).append(latency_ms)
                # Keep only last 10 records
                self._latency_records[aid] = self._latency_records[aid][-10:]
                self._records.append(
                    HeartbeatRecord(
                        agent_id=aid,
                        sent_at=now,
                        replied_at=time.time(),
                        latency_ms=latency_ms,
                    )
                )

    async def _send_to_agent(self, agent_id: str, message: dict[str, Any]) -> None:
        """Send a message to an agent via the orchestrator's state board."""
        board = self._get_state_board()
        if board is not None and hasattr(board, "send_mail"):
            board.send_mail("monitor", agent_id, str(message))

    async def _await_reply(
        self,
        agent: Any,
        timeout: float,
    ) -> bool:
        """Wait for the agent to process the heartbeat.

        Checks if the agent's last_active timestamp has been updated
        after a short sleep.
        """
        before = getattr(getattr(agent, "state", agent), "last_active", 0)
        await asyncio.sleep(timeout)
        after = getattr(getattr(agent, "state", agent), "last_active", 0)
        return after > before

    async def _handle_timeout(self, agent_id: str) -> None:
        """Handle an agent that missed too many heartbeats."""
        self._log_event(
            "agent.heartbeat_timeout",
            agent_id=agent_id,
            message=f"Agent {agent_id} missed {self.max_missed} heartbeats",
        )

        # Notify orchestrator
        if self._orchestrator is not None:
            with contextlib.suppress(Exception):
                await self._orchestrator.on_agent_timeout(agent_id)

    # -- DLQ monitoring --------------------------------------------------------

    async def check_dlq(self) -> dict[str, dict[str, Any]]:
        """Check dead-letter queues across all projects."""
        result: dict[str, dict[str, Any]] = {}
        board = self._get_state_board()
        if board is not None and hasattr(board, "inspect_dlq"):
            result = await board.inspect_dlq()
        return result

    # -- metrics ---------------------------------------------------------------

    def get_latency_stats(self, agent_id: str) -> dict[str, float]:
        """Return latency statistics for an agent."""
        records = self._latency_records.get(agent_id, [])
        if not records:
            return {"avg_ms": 0.0, "max_ms": 0.0, "min_ms": 0.0, "count": 0.0}
        return {
            "avg_ms": round(sum(records) / len(records), 1),
            "max_ms": round(max(records), 1),
            "min_ms": round(min(records), 1),
            "count": float(len(records)),
        }

    def get_health_summary(self) -> dict[str, Any]:
        """Return summary of all monitored agents."""
        agents = self._get_agents()
        summary: dict[str, Any] = {}
        for a in agents:
            aid = a.agent_id
            summary[aid] = {
                "status": getattr(getattr(a, "state", a), "status", "unknown"),
                "missed_heartbeats": self._missed_counts.get(aid, 0),
                "latency_stats": self.get_latency_stats(aid),
                "last_active_s": round(time.time() - getattr(getattr(a, "state", a), "last_active", 0), 1),
            }
        return summary

    # -- internal helpers ------------------------------------------------------

    def _get_agents(self) -> list[Any]:
        """Get all agents from the orchestrator's state board."""
        if self._orchestrator is None:
            return []
        board = self._get_state_board()
        if board is None:
            return []
        return list(getattr(board, "agents", {}).values())

    def _get_state_board(self) -> Any:
        if self._orchestrator is None:
            return None
        return getattr(self._orchestrator, "_state_board", None)

    def _log_event(self, event_type: str, **kwargs: Any) -> None:
        board = self._get_state_board()
        if board is not None and hasattr(board, "log_event"):
            board.log_event(event_type, **kwargs)

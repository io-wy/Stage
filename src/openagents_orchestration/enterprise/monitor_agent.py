"""MonitorAgent — proactive heartbeat monitor for enterprise orchestration.

Unlike the passive HealthMonitor (which scans StateBoard periodically),
MonitorAgent actively sends heartbeat requests to resident agents and
detects timeouts.  It also watches for DLQ growth and budget exhaustion.

Design:
- Runs as a background asyncio task inside GlobalOrchestrator
- Sends heartbeat SIGNAL messages to residents
- Expects heartbeat replies within HEARTBEAT_TIMEOUT_S
- On timeout: logs event, notifies GlobalDirector, optionally stops resident
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import Any

from openagents_orchestration.models.message import (
    MessageType,
    StructuredMessage,
)
from openagents_orchestration.core.resident import ResidentAgent


@dataclass
class HeartbeatRecord:
    """Record of a heartbeat exchange with a resident."""

    resident_id: str
    sent_at: float
    replied_at: float | None = None
    latency_ms: float = 0.0
    timed_out: bool = False


class MonitorAgent:
    """Active heartbeat monitor for all resident agents."""

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
        """Send heartbeat requests to all active residents."""
        residents = self._get_residents()
        now = time.time()

        for resident in residents:
            rid = resident.resident_id
            self._last_heartbeat[rid] = now

            # Send heartbeat as a structured message
            hb_msg = {
                "task": "heartbeat",
                "content": "ping",
                "from": "monitor",
                "timestamp": now,
            }
            try:
                await resident.send(hb_msg)
            except Exception as exc:
                self._log_event(
                    "agent.heartbeat_timeout",
                    agent_id=rid,
                    message=f"Failed to send heartbeat: {exc}",
                )
                await self._handle_timeout(rid)
                continue

            # Wait for reply with timeout
            replied = await self._await_reply(resident, timeout=self.heartbeat_timeout_s)
            if not replied:
                self._missed_counts[rid] = self._missed_counts.get(rid, 0) + 1
                if self._missed_counts[rid] >= self.max_missed:
                    await self._handle_timeout(rid)
            else:
                self._missed_counts[rid] = 0
                latency_ms = (time.time() - now) * 1000
                self._latency_records.setdefault(rid, []).append(latency_ms)
                # Keep only last 10 records
                self._latency_records[rid] = self._latency_records[rid][-10:]
                self._records.append(
                    HeartbeatRecord(
                        resident_id=rid,
                        sent_at=now,
                        replied_at=time.time(),
                        latency_ms=latency_ms,
                    )
                )

    async def _await_reply(
        self,
        resident: ResidentAgent,
        timeout: float,
    ) -> bool:
        """Wait for the resident to process the heartbeat.

        Since ResidentAgent processes messages in its _loop, we can't directly
        await a reply.  Instead, we check if the resident's last_active timestamp
        has been updated after a short sleep.
        """
        before = resident.state.last_active
        await asyncio.sleep(timeout)
        return resident.state.last_active > before

    async def _handle_timeout(self, resident_id: str) -> None:
        """Handle a resident that missed too many heartbeats."""
        self._log_event(
            "agent.heartbeat_timeout",
            agent_id=resident_id,
            message=f"Resident {resident_id} missed {self.max_missed} heartbeats",
        )

        # Try to stop the resident gracefully
        resident = self._find_resident(resident_id)
        if resident is not None:
            try:
                await resident.stop()
            except Exception:
                pass

        # Notify orchestrator
        if self._orchestrator is not None:
            try:
                await self._orchestrator.on_agent_timeout(resident_id)
            except Exception:
                pass

    # -- DLQ monitoring --------------------------------------------------------

    async def check_dlq(self) -> dict[str, dict[str, Any]]:
        """Check dead-letter queues across all projects."""
        result: dict[str, dict[str, Any]] = {}
        board = self._get_state_board()
        if board is not None and hasattr(board, "inspect_dlq"):
            result = await board.inspect_dlq()
        return result

    # -- metrics ---------------------------------------------------------------

    def get_latency_stats(self, resident_id: str) -> dict[str, float]:
        """Return latency statistics for a resident."""
        records = self._latency_records.get(resident_id, [])
        if not records:
            return {"avg_ms": 0.0, "max_ms": 0.0, "min_ms": 0.0, "count": 0.0}
        return {
            "avg_ms": round(sum(records) / len(records), 1),
            "max_ms": round(max(records), 1),
            "min_ms": round(min(records), 1),
            "count": float(len(records)),
        }

    def get_health_summary(self) -> dict[str, Any]:
        """Return summary of all monitored residents."""
        residents = self._get_residents()
        summary: dict[str, Any] = {}
        for r in residents:
            rid = r.resident_id
            summary[rid] = {
                "status": r.state.status,
                "missed_heartbeats": self._missed_counts.get(rid, 0),
                "latency_stats": self.get_latency_stats(rid),
                "last_active_s": round(time.time() - r.state.last_active, 1),
            }
        return summary

    # -- internal helpers ------------------------------------------------------

    def _get_residents(self) -> list[ResidentAgent]:
        """Get all resident agents from the orchestrator."""
        if self._orchestrator is None:
            return []
        return list(getattr(self._orchestrator, "_residents", {}).values())

    def _find_resident(self, resident_id: str) -> ResidentAgent | None:
        if self._orchestrator is None:
            return None
        return getattr(self._orchestrator, "_residents", {}).get(resident_id)

    def _get_state_board(self) -> Any:
        if self._orchestrator is None:
            return None
        return getattr(self._orchestrator, "_state_board", None)

    def _log_event(self, event_type: str, **kwargs: Any) -> None:
        board = self._get_state_board()
        if board is not None and hasattr(board, "log_event"):
            board.log_event(event_type, **kwargs)

"""Tests for MonitorAgent active heartbeat monitoring."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from openagents_orchestration.enterprise.monitor_agent import MonitorAgent


class TestMonitorAgent:
    def _make_orchestrator(self, residents=None):
        orch = MagicMock()
        orch._residents = residents or {}
        orch._state_board = None
        return orch

    def _make_resident(self, rid, status="idle", last_active_offset=0):
        import time

        r = MagicMock()
        r.resident_id = rid
        r.state.status = status
        r.state.last_active = time.time() - last_active_offset
        r._active = True
        r.send = AsyncMock()
        return r

    @pytest.mark.asyncio
    async def test_start_stop(self):
        orch = self._make_orchestrator()
        mon = MonitorAgent(orch, heartbeat_interval_s=0.1)
        await mon.start()
        assert mon._running is True
        assert mon._task is not None
        await mon.stop()
        assert mon._running is False

    @pytest.mark.asyncio
    async def test_start_idempotent(self):
        orch = self._make_orchestrator()
        mon = MonitorAgent(orch, heartbeat_interval_s=0.1)
        await mon.start()
        task1 = mon._task
        await mon.start()
        assert mon._task is task1
        await mon.stop()

    @pytest.mark.asyncio
    async def test_stop_idempotent(self):
        orch = self._make_orchestrator()
        mon = MonitorAgent(orch)
        await mon.stop()  # should not raise
        assert mon._running is False

    @pytest.mark.asyncio
    async def test_heartbeat_sends_message(self):
        r1 = self._make_resident("r1")
        orch = self._make_orchestrator({"r1": r1})
        mon = MonitorAgent(orch, heartbeat_interval_s=10.0)

        # Override _await_reply to simulate a reply
        mon._await_reply = AsyncMock(return_value=True)

        await mon._send_heartbeats()
        r1.send.assert_called_once()
        args = r1.send.call_args[0][0]
        assert args["task"] == "heartbeat"
        assert args["from"] == "monitor"

    @pytest.mark.asyncio
    async def test_heartbeat_timeout(self):
        r1 = self._make_resident("r1")
        orch = self._make_orchestrator({"r1": r1})
        orch.on_agent_timeout = AsyncMock()
        mon = MonitorAgent(
            orch, heartbeat_interval_s=10.0, heartbeat_timeout_s=0.01
        )

        # Override _await_reply to simulate no reply
        mon._await_reply = AsyncMock(return_value=False)

        await mon._send_heartbeats()
        assert mon._missed_counts["r1"] == 1

        # Second missed heartbeat should trigger timeout
        await mon._send_heartbeats()
        orch.on_agent_timeout.assert_called_once_with("r1")

    @pytest.mark.asyncio
    async def test_heartbeat_latency_tracking(self):
        r1 = self._make_resident("r1")
        orch = self._make_orchestrator({"r1": r1})
        mon = MonitorAgent(orch, heartbeat_interval_s=10.0)
        mon._await_reply = AsyncMock(return_value=True)

        await mon._send_heartbeats()
        stats = mon.get_latency_stats("r1")
        assert stats["count"] == 1.0
        assert stats["avg_ms"] >= 0.0

    def test_get_health_summary(self):
        r1 = self._make_resident("r1", status="busy")
        orch = self._make_orchestrator({"r1": r1})
        mon = MonitorAgent(orch)
        summary = mon.get_health_summary()
        assert "r1" in summary
        assert summary["r1"]["status"] == "busy"
        assert summary["r1"]["missed_heartbeats"] == 0

    def test_empty_orchestrator(self):
        orch = self._make_orchestrator()
        mon = MonitorAgent(orch)
        assert mon._get_residents() == []
        assert mon.get_health_summary() == {}

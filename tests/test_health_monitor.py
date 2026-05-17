"""Tests for HealthMonitor watchdog."""

from __future__ import annotations

import time

import pytest

from openagents_orchestration.health_monitor import HealthMonitor
from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus
from openagents_orchestration.state_board import AgentStatus, StateBoard


class TestHealthMonitor:
    def test_detects_long_running_agent(self):
        board = StateBoard("obj")
        board.register_agent("coder-t1", "coder")
        board.update_agent(
            "coder-t1",
            status=AgentStatus.RUNNING,
            start_time=time.time() - 400,  # 400s ago
        )

        monitor = HealthMonitor(board, check_interval=1.0, max_elapsed_s=300)
        monitor._check()

        assert len(board._pending_messages) == 1
        msg = board._pending_messages[0]
        assert msg["from"] == "health_monitor"
        assert msg["to"] == "director"
        assert "coder-t1" in msg["content"]
        assert "运行" in msg["content"]

    def test_detects_step_exhausted(self):
        board = StateBoard("obj")
        board.register_agent("coder-t1", "coder")
        board.update_agent(
            "coder-t1",
            status=AgentStatus.RUNNING,
            start_time=time.time(),
            steps_used=30,
        )

        monitor = HealthMonitor(board, check_interval=1.0)
        monitor._check()

        assert len(board._pending_messages) == 1
        assert "steps_used=30" in board._pending_messages[0]["content"]

    def test_detects_consecutive_tool_failures(self):
        board = StateBoard("obj")
        board.register_agent("coder-t1", "coder")
        board.update_agent(
            "coder-t1",
            status=AgentStatus.RUNNING,
            start_time=time.time(),
            consecutive_tool_failures=5,
        )

        monitor = HealthMonitor(board, check_interval=1.0)
        monitor._check()

        assert len(board._pending_messages) == 1
        assert "连续 5 次工具失败" in board._pending_messages[0]["content"]

    def test_no_alert_for_idle_agent(self):
        board = StateBoard("obj")
        board.register_agent("coder-t1", "coder")
        board.update_agent("coder-t1", status=AgentStatus.IDLE)

        monitor = HealthMonitor(board, check_interval=1.0)
        monitor._check()

        assert len(board._pending_messages) == 0

    def test_alert_once_per_agent(self):
        board = StateBoard("obj")
        board.register_agent("coder-t1", "coder")
        board.update_agent(
            "coder-t1",
            status=AgentStatus.RUNNING,
            start_time=time.time() - 400,
        )

        monitor = HealthMonitor(board, check_interval=1.0)
        monitor._check()
        assert len(board._pending_messages) == 1

        # Second check should not alert again
        monitor._check()
        assert len(board._pending_messages) == 1

    def test_clears_alert_on_recovery(self):
        board = StateBoard("obj")
        board.register_agent("coder-t1", "coder")
        board.update_agent(
            "coder-t1",
            status=AgentStatus.RUNNING,
            start_time=time.time() - 400,
        )

        monitor = HealthMonitor(board, check_interval=1.0)
        monitor._check()
        assert len(board._pending_messages) == 1

        # Agent recovers
        board.update_agent("coder-t1", status=AgentStatus.DONE)
        monitor._check()
        # No new alert, and alert flag cleared
        assert len(board._pending_messages) == 1

        # Agent goes bad again → should re-alert
        board.update_agent("coder-t1", status=AgentStatus.RUNNING)
        monitor._check()
        assert len(board._pending_messages) == 2

    @pytest.mark.asyncio
    async def test_start_stop(self):
        board = StateBoard("obj")
        monitor = HealthMonitor(board, check_interval=0.1)
        await monitor.start()
        await asyncio.sleep(0.15)
        await monitor.stop()


import asyncio

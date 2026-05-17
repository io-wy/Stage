"""HealthMonitor — watchdog that scans running agents and alerts the Director.

Runs as a background asyncio task inside OrchestratorRunner.
Periodically checks StateBoard for stuck / exhausted / failing agents
and sends alert messages to the Director via StateBoard.send_mail().
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from openagents_orchestration.state_board import AgentStatus, StateBoard


class HealthMonitor:
    """Background watchdog for agent health."""

    def __init__(
        self,
        board: StateBoard,
        *,
        check_interval: float = 30.0,
        max_elapsed_s: float = 300.0,
        max_steps: int = 25,
        max_consecutive_tool_failures: int = 3,
        max_token_used: int = 50000,
    ):
        self.board = board
        self.check_interval = check_interval
        self.max_elapsed_s = max_elapsed_s
        self.max_steps = max_steps
        self.max_consecutive_tool_failures = max_consecutive_tool_failures
        self.max_token_used = max_token_used
        self._task: asyncio.Task[Any] | None = None
        self._alerted: set[str] = set()  # agent_ids already alerted this cycle

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Start the background check loop."""
        self._task = asyncio.create_task(self._loop(), name="health-monitor")

    async def stop(self) -> None:
        """Stop the background check loop."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # -- check loop ----------------------------------------------------------

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.check_interval)
            self._check()

    def _check(self) -> None:
        """Scan all agents and alert on anomalies."""
        now = time.time()
        running_ids = set()

        for agent_id, agent in self.board.agents.items():
            if agent.status != AgentStatus.RUNNING:
                continue

            running_ids.add(agent_id)
            issues: list[str] = []
            elapsed = now - agent.start_time if agent.start_time else 0

            if elapsed > self.max_elapsed_s:
                issues.append(f"运行 {elapsed:.0f}s 未结束，可能卡住")

            if agent.steps_used >= self.max_steps:
                issues.append(f"steps_used={agent.steps_used}，step 预算耗尽")

            if agent.consecutive_tool_failures >= self.max_consecutive_tool_failures:
                issues.append(
                    f"连续 {agent.consecutive_tool_failures} 次工具失败"
                )

            if agent.token_used > self.max_token_used:
                issues.append(f"token_used={agent.token_used}，token 爆表")

            if not issues:
                # Agent healthy — clear alert flag if it recovers
                self._alerted.discard(agent_id)
                continue

            # Avoid spam: only alert once per agent per anomaly cycle
            if agent_id in self._alerted:
                continue
            self._alerted.add(agent_id)

            msg = f"[HealthMonitor] {agent_id} 异常: " + "; ".join(issues)
            self._send_alert(agent_id, msg)

        # Clear alert flags for agents that are no longer running
        self._alerted.intersection_update(running_ids)

    def _send_alert(self, agent_id: str, message: str) -> None:
        """Send an alert message to the Director via StateBoard mailbox."""
        self.board.send_mail("health_monitor", "director", message)
        self.board.log_event(
            "health.alert",
            agent_id=agent_id,
            message=message,
        )

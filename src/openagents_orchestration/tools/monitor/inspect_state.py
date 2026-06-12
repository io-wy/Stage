"""inspect_state — intelligent state inspection with multiple analysis modes."""

from __future__ import annotations

import json
import time
from typing import Any

from openagents.errors.exceptions import PermanentToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


class InspectStateTool(ToolPlugin):
    """Intelligent state inspection. Not just a raw dump — filtered views for specific concerns."""

    name = "inspect_state"
    description = (
        "Smart inspection of the StateBoard. Supports multiple analysis modes:\n"
        "- anomaly: only anomalies (failed tasks, stalled agents, budget alerts)\n"
        "- progress: progress stats + ETA prediction\n"
        "- bottleneck: which tasks block the most downstream work\n"
        "- resource: token/time consumption ranked by agent\n"
        "- full: complete snapshot (same as show_state)\n"
        "Use 'focus_on' to narrow to a specific agent or task."
    )
    durable_idempotent = True

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(concurrency_safe=True, side_effects="read_only")

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["anomaly", "progress", "bottleneck", "resource", "full"],
                    "description": "Inspection mode",
                },
                "focus_on": {
                    "type": "string",
                    "description": "Optional: focus on a specific agent_id or task_id",
                },
            },
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> str:
        deps = getattr(context, "deps", None)
        board = getattr(deps, "state_board", None) if deps else None
        if board is None:
            raise PermanentToolError("StateBoard not available", tool_name=self.name)

        mode = params.get("mode", "full")
        focus = params.get("focus_on", "")

        if mode == "anomaly":
            return self._anomaly_view(board, focus)
        elif mode == "progress":
            return self._progress_view(board)
        elif mode == "bottleneck":
            return self._bottleneck_view(board)
        elif mode == "resource":
            return self._resource_view(board, focus)
        else:
            snapshot = board.snapshot()
            return json.dumps(snapshot, indent=2, ensure_ascii=False)

    def _anomaly_view(self, board: Any, focus: str) -> str:
        """Return only anomalous items."""
        anomalies = []
        now = time.time()

        # Failed tasks
        for t in board.tasks.values():
            if t.status.value == "failed":
                agent_id = f"{t.agent_type}-{t.task_id}"
                agent = board.get_agent(agent_id)
                anomalies.append({
                    "type": "task_failed",
                    "task_id": t.task_id,
                    "agent_type": t.agent_type,
                    "error": (t.error or "unknown")[:200],
                    "fallback_attempts": agent.fallback_attempts if agent else 0,
                })

        # Stalled agents (running but no recent events)
        for aid, agent in board.agents.items():
            if agent.status.value == "running":
                recent = [e for e in board.events if e.agent_id == aid][-5:]
                if recent:
                    idle_s = now - recent[-1].ts
                    if idle_s > 120:
                        anomalies.append({
                            "type": "agent_stalled",
                            "agent_id": aid,
                            "idle_seconds": round(idle_s, 1),
                            "last_event": recent[-1].event_type,
                        })

        # Budget exhausted
        if board.budget.exhausted:
            anomalies.append({
                "type": "budget_exhausted",
                "token_used": board.budget.token_used,
                "time_remaining": round(board.budget.time_remaining_s, 1),
            })
        elif board.budget.token_used / board.budget.token_limit > 0.8:
            anomalies.append({
                "type": "budget_warning",
                "token_usage_pct": round(board.budget.token_used / board.budget.token_limit * 100, 1),
            })

        if focus:
            anomalies = [a for a in anomalies if focus in str(a.values())]

        return json.dumps(anomalies, indent=2, ensure_ascii=False) if anomalies else "未发现异常"

    def _progress_view(self, board: Any) -> str:
        """Progress stats + ETA prediction."""
        total = len(board.tasks)
        completed = sum(1 for t in board.tasks.values() if t.status.value == "completed")
        failed = sum(1 for t in board.tasks.values() if t.status.value == "failed")
        running = sum(1 for t in board.tasks.values() if t.status.value == "running")
        pending = total - completed - failed - running

        elapsed = time.time() - board.budget.start_time

        if completed > 0 and elapsed > 0:
            rate = completed / elapsed
            remaining = pending + running
            eta_s = remaining / rate if rate > 0 else float("inf")
        else:
            eta_s = float("inf")

        success_rate = completed / (completed + failed) if (completed + failed) > 0 else 1.0

        return json.dumps({
            "summary": f"{completed}/{total} done, {failed} failed, {running} running, {pending} pending",
            "elapsed_seconds": round(elapsed, 1),
            "estimated_remaining_seconds": round(eta_s, 1) if eta_s != float("inf") else "unknown",
            "success_rate_pct": round(success_rate * 100, 1),
            "on_track": "yes" if success_rate > 0.7 and not board.budget.exhausted else "no",
        }, indent=2, ensure_ascii=False)

    def _bottleneck_view(self, board: Any) -> str:
        """Which tasks block the most downstream work."""
        blockers: dict[str, list[str]] = {}
        for t in board.tasks.values():
            for dep in t.dependencies:
                blockers.setdefault(dep, []).append(t.task_id)

        critical = []
        for tid, blocked in blockers.items():
            task = board.get_task(tid)
            if task and task.status.value != "completed":
                critical.append({
                    "blocking_task": tid,
                    "status": task.status.value,
                    "blocked_tasks_count": len(blocked),
                    "blocked_tasks": blocked[:10],
                    "impact": "high" if len(blocked) > 3 else "medium" if len(blocked) > 1 else "low",
                })

        critical.sort(key=lambda x: x["blocked_tasks_count"], reverse=True)
        return json.dumps(critical, indent=2, ensure_ascii=False) if critical else "无阻塞任务"

    def _resource_view(self, board: Any, focus: str) -> str:
        """Resource consumption ranked by agent."""
        agents_data = []
        for aid, agent in board.agents.items():
            if focus and focus not in aid:
                continue
            elapsed = time.time() - agent.start_time if agent.start_time else 0
            agents_data.append({
                "agent_id": aid,
                "status": agent.status.value,
                "token_used": agent.token_used,
                "steps_used": agent.steps_used,
                "elapsed_seconds": round(elapsed, 1),
                "token_per_step": round(agent.token_used / max(agent.steps_used, 1), 1),
                "llm_calls": getattr(agent, "llm_call_count", 0),
                "avg_latency_ms": round(agent.total_llm_latency_ms / max(agent.llm_call_count, 1), 1) if getattr(agent, "llm_call_count", 0) else 0,
            })

        agents_data.sort(key=lambda x: x["token_used"], reverse=True)
        total_tokens = sum(a["token_used"] for a in agents_data)

        return json.dumps({
            "total_tokens_consumed": total_tokens,
            "budget_remaining": board.budget.token_remaining,
            "agents_ranked_by_cost": agents_data[:10],
        }, indent=2, ensure_ascii=False)

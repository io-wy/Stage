"""verify_alert_effectiveness — check if previous alerts were acted upon and resolved."""

from __future__ import annotations

import json
import time
from typing import Any

from openagents.errors.exceptions import PermanentToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


class VerifyAlertEffectivenessTool(ToolPlugin):
    """Verify whether previous alerts led to effective action.

    Reads the alert history from StateBoard events and checks:
    - Was the recommended action taken?
    - Is the problem still present?
    - Did the situation improve or worsen?
    """

    name = "verify_alert_effectiveness"
    description = (
        "验证之前发出的告警是否产生了效果。"
        "读取 StateBoard 中的 observer.alert 事件，检查："
        "1. 建议的措施是否被执行了？"
        "2. 问题是否还存在？"
        "3. 情况是改善还是恶化了？"
        "每次发新告警前应该先调用此工具验证旧告警。"
    )
    durable_idempotent = True

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(concurrency_safe=True, side_effects="read_only")

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "lookback": {
                    "type": "integer",
                    "description": "检查最近多少条告警，默认5条",
                    "default": 5,
                },
            },
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> str:
        deps = getattr(context, "deps", None)
        board = getattr(deps, "state_board", None) if deps else None
        if board is None:
            raise PermanentToolError("StateBoard not available", tool_name=self.name)

        lookback = params.get("lookback", 5)

        # Find recent observer.alert events
        alert_events = [e for e in board.events if e.event_type == "observer.alert"][-lookback:]
        if not alert_events:
            return "尚无历史告警记录"

        results = []
        now = time.time()

        for evt in alert_events:
            payload = evt.payload
            alert_id = payload.get("alert_id", "unknown")
            severity = payload.get("severity", "info")
            data = payload.get("data", {})
            recommended_action = payload.get("recommended_action", "")
            agent_id = data.get("agent_id", "")
            task_id = data.get("task_id", "")

            elapsed_s = round(now - evt.ts, 1)

            verification = {
                "alert_id": alert_id,
                "sent_at": time.strftime("%H:%M:%S", time.localtime(evt.ts)),
                "elapsed_seconds": elapsed_s,
                "original_severity": severity,
                "message": evt.message,
                "recommended_action": recommended_action,
            }

            # Check if problem still exists based on alert type
            status = self._check_status(board, agent_id, task_id, data, evt)
            verification["current_status"] = status

            # Determine effectiveness
            if status["resolved"]:
                verification["effectiveness"] = "resolved"
                verification["summary"] = f"问题已解决（{elapsed_s}s前告警）"
            elif status["improved"]:
                verification["effectiveness"] = "improved"
                verification["summary"] = f"情况有改善但未完全解决（{elapsed_s}s前告警）"
            elif status["worsened"]:
                verification["effectiveness"] = "worsened"
                verification["summary"] = f"情况恶化！建议立即升级处理（{elapsed_s}s前告警）"
            else:
                verification["effectiveness"] = "unchanged"
                verification["summary"] = f"问题持续存在，未见改善（{elapsed_s}s前告警）"

            results.append(verification)

        return json.dumps({
            "alerts_verified": len(results),
            "still_open": sum(1 for r in results if r["effectiveness"] != "resolved"),
            "resolved": sum(1 for r in results if r["effectiveness"] == "resolved"),
            "details": results,
        }, indent=2, ensure_ascii=False)

    def _check_status(self, board: Any, agent_id: str, task_id: str, data: dict, evt: Any) -> dict[str, bool]:
        """Check current status of the problem reported in an alert."""
        resolved = False
        improved = False
        worsened = False

        # Check task status
        if task_id:
            task = board.get_task(task_id)
            if task:
                if task.status.value == "completed":
                    resolved = True
                elif task.status.value == "failed":
                    # Check if there was a retry/replan after the alert
                    events_after = [e for e in board.events if e.ts > evt.ts and e.task_id == task_id]
                    if any("replan" in e.event_type or "retry" in e.event_type for e in events_after):
                        improved = True  # Action was taken, even if not resolved yet
                elif task.status.value == "running":
                    # Task is being worked on — check if there has been progress
                    pass

        # Check agent status
        if agent_id and agent_id != "unknown":
            agent = board.get_agent(agent_id)
            if agent:
                # If agent is done, the problem is likely resolved
                if agent.status.value in ("done", "idle"):
                    resolved = True

                # Check behavioral improvements
                tool_counts = getattr(agent, "tool_call_counts", {})
                reads = tool_counts.get("read_file", 0)
                writes = tool_counts.get("write_file", 0)

                # If alert was about excessive reading, check if writing started
                if "wandering" in evt.message or "read" in evt.message.lower():
                    if writes > 0:
                        improved = True

                # Check if latency improved
                avg_lat = agent.total_llm_latency_ms / max(agent.llm_call_count, 1) if agent.llm_call_count else 0
                if "latency" in evt.message.lower() and avg_lat < 15000:
                    improved = True

        # Check budget
        if "budget" in evt.message.lower():
            usage_pct = board.budget.token_used / board.budget.token_limit
            if usage_pct < 0.8:
                resolved = True
            elif usage_pct < 0.9:
                improved = True
            else:
                worsened = True

        # Check if action was taken (look for events after the alert)
        events_after = [e for e in board.events if e.ts > evt.ts]

        # Was a replan triggered?
        if any("replan" in e.event_type for e in events_after):
            improved = True

        # Was ask_human used?
        if any("human.asked" in e.event_type for e in events_after):
            improved = True

        # Was a resident spawned for the problematic agent?
        if any("resident.registered" in e.event_type for e in events_after):
            improved = True

        return {
            "resolved": resolved,
            "improved": improved,
            "worsened": worsened,
        }

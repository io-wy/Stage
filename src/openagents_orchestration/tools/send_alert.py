"""send_alert — send alerts to the Director with smart deduplication and auto-escalation."""

from __future__ import annotations

import time
from typing import Any

from openagents.errors.exceptions import PermanentToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


class SendAlertTool(ToolPlugin):
    """Send alert to the Director. Supports deduplication (same issue within 5min suppressed)
    and auto-escalation (same issue 3+ times auto-upgrades severity)."""

    name = "send_alert"
    description = (
        "向导演发送告警。支持智能抑制（同一问题5分钟内不重复发送）"
        "和自动升级（同一问题连续出现3次自动升级severity）。"
        "severity: critical=必须立即处理, warning=建议关注, info=仅记录"
    )
    durable_idempotent = False

    # Class-level alert history across invocations
    _alert_history: dict[str, dict[str, Any]] = {}
    _DEDUP_WINDOW_S = 300  # 5 minutes
    _ESCALATE_THRESHOLD = 3

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(concurrency_safe=True, side_effects="writes_state")

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "severity": {
                    "type": "string",
                    "enum": ["info", "warning", "critical"],
                    "description": "告警级别",
                },
                "target": {
                    "type": "string",
                    "description": "告警对象，默认 director",
                    "default": "director",
                },
                "message": {
                    "type": "string",
                    "description": "告警内容。要具体：涉及哪个Agent、什么现象、建议怎么做。",
                },
                "data": {
                    "type": "object",
                    "description": "可选：相关数据，如 agent_id, task_id, metrics",
                },
                "recommended_action": {
                    "type": "string",
                    "description": "建议Director采取的措施。用于后续验证该措施是否有效。",
                },
            },
            "required": ["severity", "message"],
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> str:
        severity = params.get("severity", "info")
        target = params.get("target", "director")
        message = params.get("message", "")
        data = params.get("data", {})
        recommended_action = params.get("recommended_action", "")

        deps = getattr(context, "deps", None)
        board = getattr(deps, "state_board", None) if deps else None
        if board is None:
            raise PermanentToolError("StateBoard not available", tool_name=self.name)

        from_agent = getattr(context, "agent_id", "observer")

        # Generate fingerprint for deduplication
        fingerprint = self._fingerprint(message, data)
        now = time.time()

        hist = self._alert_history.get(fingerprint)
        if hist:
            # Check dedup window
            if now - hist["last_sent"] < self._DEDUP_WINDOW_S:
                return f"告警已抑制（{self._DEDUP_WINDOW_S // 60}分钟内重复: {fingerprint[:50]}...）"

            hist["count"] += 1
            hist["last_sent"] = now

            # Auto-escalate
            if hist["count"] >= self._ESCALATE_THRESHOLD and severity in ("info", "warning"):
                old_severity = severity
                severity = "critical"
                message = f"[自动升级 {old_severity}->{severity}] {message}"
        else:
            self._alert_history[fingerprint] = {
                "first_seen": now,
                "count": 1,
                "last_sent": now,
            }

        # Generate alert ID
        alert_id = f"ALERT-{fingerprint[:20]}-{int(now)}"

        # Send via mailbox
        alert_msg = f"[{severity.upper()}] {message}"
        if recommended_action:
            alert_msg += f"\n[建议措施] {recommended_action}"
        board.send_mail(from_agent, target, alert_msg)

        # Log to StateBoard with full context for verification
        board.log_event(
            "observer.alert",
            agent_id=from_agent,
            message=f"to={target}, severity={severity}: {message[:100]}",
            payload={
                "alert_id": alert_id,
                "severity": severity,
                "target": target,
                "data": data,
                "recommended_action": recommended_action,
                "fingerprint": fingerprint,
            },
        )

        return (
            f"告警已发送给 {target} (级别: {severity}, "
            f"alert_id: {alert_id}, "
            f"历史次数: {self._alert_history[fingerprint]['count']})"
        )

    def _fingerprint(self, message: str, data: dict[str, Any]) -> str:
        base = message[:60]
        aid = data.get("agent_id", "")
        tid = data.get("task_id", "")
        return f"{aid}:{tid}:{base}"

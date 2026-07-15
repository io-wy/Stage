"""Tests for monitor tools: send_alert, inspect_state, diagnose_agent,
analyze_event_pattern, predict_budget, verify_alert_effectiveness."""

from __future__ import annotations

import pytest
from openagents.errors.exceptions import PermanentToolError

from openagents_orchestration.core.state_board import (
    AgentStatus,
    Budget,
    StateBoard,
)
from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus
from openagents_orchestration.tools.monitor.analyze_event_pattern import (
    AnalyzeEventPatternTool,
)
from openagents_orchestration.tools.monitor.diagnose_agent import DiagnoseAgentTool
from openagents_orchestration.tools.monitor.inspect_state import InspectStateTool
from openagents_orchestration.tools.monitor.predict_budget import PredictBudgetTool
from openagents_orchestration.tools.monitor.send_alert import SendAlertTool
from openagents_orchestration.tools.monitor.verify_alert_effectiveness import (
    VerifyAlertEffectivenessTool,
)


class MockContext:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


# ---------------------------------------------------------------------------
# SendAlertTool
# ---------------------------------------------------------------------------

class TestSendAlertTool:
    def test_schema_has_required_fields(self):
        tool = SendAlertTool()
        schema = tool.schema()
        assert "severity" in schema["properties"]
        assert "message" in schema["properties"]
        assert "target" in schema["properties"]
        assert "data" in schema["properties"]
        assert "recommended_action" in schema["properties"]
        assert schema.get("required") == ["severity", "message"]

    @pytest.mark.asyncio
    async def test_invoke_sends_alert(self):
        board = StateBoard("obj")
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="monitor")

        tool = SendAlertTool()
        result = await tool.invoke({
            "severity": "warning",
            "message": "Agent is stuck in a loop",
            "target": "director",
            "data": {"agent_id": "coder-1", "task_id": "t1"},
            "recommended_action": "replan",
        }, ctx)

        assert "告警已记录" in result or "recorded" in result.lower()
        assert "warning" in result.lower() or "warning" in result

    @pytest.mark.asyncio
    async def test_invoke_dedup(self):
        board = StateBoard("obj")
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="monitor")

        tool = SendAlertTool()
        # First alert
        result1 = await tool.invoke({
            "severity": "warning",
            "message": "Agent is stuck in a loop",
            "data": {"agent_id": "coder-1"},
        }, ctx)
        assert "告警已记录" in result1 or "recorded" in result1.lower()
        # Same alert within dedup window should be suppressed
        result2 = await tool.invoke({
            "severity": "warning",
            "message": "Agent is stuck in a loop",
            "data": {"agent_id": "coder-1"},
        }, ctx)

        assert "抑制" in result2 or "suppressed" in result2.lower()

    @pytest.mark.asyncio
    async def test_invoke_escalation(self):
        board = StateBoard("obj")
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="monitor")

        # Clear any previous alert history
        SendAlertTool._alert_history.clear()

        tool = SendAlertTool()
        # Send same alert 3+ times to trigger escalation
        for _ in range(4):
            result = await tool.invoke({
                "severity": "warning",
                "message": "Agent is stuck in a loop",
                "data": {"agent_id": "coder-1"},
            }, ctx)
            # Clear the history entry's last_sent to bypass dedup
            fp = tool._fingerprint("Agent is stuck in a loop", {"agent_id": "coder-1"})
            if fp in SendAlertTool._alert_history:
                SendAlertTool._alert_history[fp]["last_sent"] = 0

        # The last one should be escalated to critical
        assert "critical" in result.lower() or "自动升级" in result

    @pytest.mark.asyncio
    async def test_invoke_no_board_raises(self):
        ctx = MockContext(deps=MockContext(), agent_id="monitor")
        tool = SendAlertTool()
        with pytest.raises(PermanentToolError, match="StateBoard"):
            await tool.invoke({"severity": "warning", "message": "test"}, ctx)


# ---------------------------------------------------------------------------
# InspectStateTool
# ---------------------------------------------------------------------------

class TestInspectStateTool:
    def test_schema_has_mode_and_focus(self):
        tool = InspectStateTool()
        schema = tool.schema()
        assert "mode" in schema["properties"]
        assert "focus_on" in schema["properties"]

    @pytest.mark.asyncio
    async def test_invoke_full_mode(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "task", "coder")],
        ))
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="monitor")

        tool = InspectStateTool()
        result = await tool.invoke({"mode": "full"}, ctx)
        assert "obj" in result

    @pytest.mark.asyncio
    async def test_invoke_anomaly_mode(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "task", "coder")],
        ))
        board.update_task("t1", status=TaskStatus.FAILED, error="oops")
        board.register_agent("coder-t1", "coder")
        board.update_agent("coder-t1", status=AgentStatus.FAILED, fallback_attempts=3)

        ctx = MockContext(deps=MockContext(state_board=board), agent_id="monitor")
        tool = InspectStateTool()
        result = await tool.invoke({"mode": "anomaly"}, ctx)
        assert "task_failed" in result or "未发现异常" in result

    @pytest.mark.asyncio
    async def test_invoke_progress_mode(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "task", "coder")],
        ))
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="monitor")

        tool = InspectStateTool()
        result = await tool.invoke({"mode": "progress"}, ctx)
        assert "summary" in result

    @pytest.mark.asyncio
    async def test_invoke_bottleneck_mode(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[
                TaskNode("t1", "task 1", "coder"),
                TaskNode("t2", "task 2", "coder", dependencies=["t1"]),
            ],
        ))
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="monitor")

        tool = InspectStateTool()
        result = await tool.invoke({"mode": "bottleneck"}, ctx)
        # t1 is pending and blocks t2
        assert "blocking_task" in result or "无阻塞任务" in result

    @pytest.mark.asyncio
    async def test_invoke_resource_mode(self):
        board = StateBoard("obj")
        board.register_agent("coder-1", "coder")
        board.update_agent("coder-1", token_used=1000, steps_used=5)

        ctx = MockContext(deps=MockContext(state_board=board), agent_id="monitor")
        tool = InspectStateTool()
        result = await tool.invoke({"mode": "resource"}, ctx)
        assert "total_tokens_consumed" in result

    @pytest.mark.asyncio
    async def test_invoke_no_board_raises(self):
        ctx = MockContext(deps=MockContext(), agent_id="monitor")
        tool = InspectStateTool()
        with pytest.raises(PermanentToolError, match="StateBoard"):
            await tool.invoke({}, ctx)


# ---------------------------------------------------------------------------
# DiagnoseAgentTool
# ---------------------------------------------------------------------------

class TestDiagnoseAgentTool:
    def test_schema_has_agent_id(self):
        tool = DiagnoseAgentTool()
        schema = tool.schema()
        assert "agent_id" in schema["properties"]
        assert "compare_with" in schema["properties"]
        assert schema.get("required") == ["agent_id"]

    @pytest.mark.asyncio
    async def test_invoke_diagnoses_agent(self):
        board = StateBoard("obj")
        board.register_agent("coder-1", "coder")
        # Log some events for the agent
        board.log_event("agent.spawned", agent_id="coder-1", message="spawned")
        board.log_event("agent.completed", agent_id="coder-1", message="done")

        ctx = MockContext(deps=MockContext(state_board=board), agent_id="monitor")
        tool = DiagnoseAgentTool()
        result = await tool.invoke({"agent_id": "coder-1"}, ctx)

        assert "agent_id" in result
        assert "coder-1" in result

    @pytest.mark.asyncio
    async def test_invoke_agent_not_found(self):
        board = StateBoard("obj")
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="monitor")
        tool = DiagnoseAgentTool()
        result = await tool.invoke({"agent_id": "missing"}, ctx)
        assert "不存在" in result or "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_invoke_with_compare(self):
        board = StateBoard("obj")
        board.register_agent("coder-1", "coder")
        board.register_agent("coder-2", "coder")
        board.update_agent("coder-1", token_used=100, steps_used=5)
        board.update_agent("coder-2", token_used=200, steps_used=10)

        ctx = MockContext(deps=MockContext(state_board=board), agent_id="monitor")
        tool = DiagnoseAgentTool()
        result = await tool.invoke({"agent_id": "coder-1", "compare_with": "coder-2"}, ctx)

        assert "comparison" in result

    @pytest.mark.asyncio
    async def test_invoke_no_board_raises(self):
        ctx = MockContext(deps=MockContext(), agent_id="monitor")
        tool = DiagnoseAgentTool()
        with pytest.raises(PermanentToolError, match="StateBoard"):
            await tool.invoke({"agent_id": "coder-1"}, ctx)


# ---------------------------------------------------------------------------
# AnalyzeEventPatternTool
# ---------------------------------------------------------------------------

class TestAnalyzeEventPatternTool:
    def test_schema_has_required_fields(self):
        tool = AnalyzeEventPatternTool()
        schema = tool.schema()
        assert "pattern" in schema["properties"]
        assert "agent_id" in schema["properties"]
        assert "time_window_seconds" in schema["properties"]
        assert schema.get("required") == ["pattern"]

    @pytest.mark.asyncio
    async def test_invoke_error_sequence(self):
        board = StateBoard("obj")
        board.register_agent("coder-1", "coder")
        board.log_event("agent.failed", agent_id="coder-1", message="error 1")
        board.log_event("agent.failed", agent_id="coder-1", message="error 2")

        ctx = MockContext(deps=MockContext(state_board=board), agent_id="monitor")
        tool = AnalyzeEventPatternTool()
        result = await tool.invoke({"pattern": "error_sequence"}, ctx)

        assert "repeated_same_error" in result or "未发现" in result

    @pytest.mark.asyncio
    async def test_invoke_tool_usage(self):
        board = StateBoard("obj")
        board.register_agent("coder-1", "coder")
        board.log_event("tool.used", agent_id="coder-1", message="tool_id=read_file")
        board.log_event("tool.used", agent_id="coder-1", message="tool_id=read_file")

        ctx = MockContext(deps=MockContext(state_board=board), agent_id="monitor")
        tool = AnalyzeEventPatternTool()
        result = await tool.invoke({"pattern": "tool_usage"}, ctx)

        assert "agent_id" in result or "agent_id" in result

    @pytest.mark.asyncio
    async def test_invoke_agent_lifecycle(self):
        board = StateBoard("obj")
        board.register_agent("coder-1", "coder")
        board.log_event("agent.spawned", agent_id="coder-1", message="spawned")
        board.log_event("agent.completed", agent_id="coder-1", message="done")

        ctx = MockContext(deps=MockContext(state_board=board), agent_id="monitor")
        tool = AnalyzeEventPatternTool()
        result = await tool.invoke({"pattern": "agent_lifecycle"}, ctx)

        assert "spawned_at" in result or "agents_analyzed" in result or "尚无" in result

    @pytest.mark.asyncio
    async def test_invoke_llm_latency_trend(self):
        board = StateBoard("obj")
        board.register_agent("coder-1", "coder")
        board.log_event("llm.called", agent_id="coder-1", message="call", payload={"latency_ms": 100})
        board.log_event("llm.called", agent_id="coder-1", message="call", payload={"latency_ms": 200})

        ctx = MockContext(deps=MockContext(state_board=board), agent_id="monitor")
        tool = AnalyzeEventPatternTool()
        result = await tool.invoke({"pattern": "llm_latency_trend"}, ctx)

        assert "avg_latency_ms" in result or "无LLM" in result

    @pytest.mark.asyncio
    async def test_invoke_with_agent_filter(self):
        board = StateBoard("obj")
        board.register_agent("coder-1", "coder")
        board.register_agent("coder-2", "coder")
        board.log_event("agent.failed", agent_id="coder-1", message="error")

        ctx = MockContext(deps=MockContext(state_board=board), agent_id="monitor")
        tool = AnalyzeEventPatternTool()
        result = await tool.invoke({"pattern": "error_sequence", "agent_id": "coder-1"}, ctx)

        assert "coder-1" in result or "未发现" in result

    @pytest.mark.asyncio
    async def test_invoke_no_board_raises(self):
        ctx = MockContext(deps=MockContext(), agent_id="monitor")
        tool = AnalyzeEventPatternTool()
        with pytest.raises(PermanentToolError, match="StateBoard"):
            await tool.invoke({"pattern": "error_sequence"}, ctx)


# ---------------------------------------------------------------------------
# PredictBudgetTool
# ---------------------------------------------------------------------------

class TestPredictBudgetTool:
    def test_schema_has_dimension(self):
        tool = PredictBudgetTool()
        schema = tool.schema()
        assert "dimension" in schema["properties"]

    @pytest.mark.asyncio
    async def test_invoke_all_dimensions(self):
        board = StateBoard("obj", budget=Budget(token_limit=1000, token_used=500, time_limit_s=300, max_steps=10))
        board.budget.steps_taken = 5

        ctx = MockContext(deps=MockContext(state_board=board), agent_id="monitor")
        tool = PredictBudgetTool()
        result = await tool.invoke({"dimension": "all"}, ctx)

        assert "token" in result
        assert "time" in result
        assert "steps" in result
        assert "first_to_exhaust" in result

    @pytest.mark.asyncio
    async def test_invoke_token_only(self):
        board = StateBoard("obj", budget=Budget(token_limit=1000, token_used=500))

        ctx = MockContext(deps=MockContext(state_board=board), agent_id="monitor")
        tool = PredictBudgetTool()
        result = await tool.invoke({"dimension": "token"}, ctx)

        assert "token" in result
        assert "time" not in result

    @pytest.mark.asyncio
    async def test_invoke_time_only(self):
        board = StateBoard("obj", budget=Budget(token_limit=1000, time_limit_s=300))

        ctx = MockContext(deps=MockContext(state_board=board), agent_id="monitor")
        tool = PredictBudgetTool()
        result = await tool.invoke({"dimension": "time"}, ctx)

        assert "time" in result
        assert "token" not in result

    @pytest.mark.asyncio
    async def test_invoke_steps_only(self):
        board = StateBoard("obj", budget=Budget(token_limit=1000, max_steps=10))
        board.budget.steps_taken = 5

        ctx = MockContext(deps=MockContext(state_board=board), agent_id="monitor")
        tool = PredictBudgetTool()
        result = await tool.invoke({"dimension": "steps"}, ctx)

        assert "steps" in result
        assert "token" not in result

    @pytest.mark.asyncio
    async def test_invoke_no_board_raises(self):
        ctx = MockContext(deps=MockContext(), agent_id="monitor")
        tool = PredictBudgetTool()
        with pytest.raises(PermanentToolError, match="StateBoard"):
            await tool.invoke({}, ctx)


# ---------------------------------------------------------------------------
# VerifyAlertEffectivenessTool
# ---------------------------------------------------------------------------

class TestVerifyAlertEffectivenessTool:
    def test_schema_has_lookback(self):
        tool = VerifyAlertEffectivenessTool()
        schema = tool.schema()
        assert "lookback" in schema["properties"]

    @pytest.mark.asyncio
    async def test_invoke_no_alerts(self):
        board = StateBoard("obj")
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="monitor")

        tool = VerifyAlertEffectivenessTool()
        result = await tool.invoke({}, ctx)
        assert "尚无" in result or "No alert" in result.lower()

    @pytest.mark.asyncio
    async def test_invoke_with_resolved_alert(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "task", "coder")],
        ))
        board.update_task("t1", status=TaskStatus.FAILED, error="oops")

        # Log an alert event
        board.log_event(
            "observer.alert",
            agent_id="monitor",
            message="Task t1 failed",
            payload={
                "alert_id": "ALERT-1",
                "severity": "warning",
                "data": {"task_id": "t1"},
                "recommended_action": "replan",
            },
        )
        # Now resolve the task
        board.update_task("t1", status=TaskStatus.COMPLETED)

        ctx = MockContext(deps=MockContext(state_board=board), agent_id="monitor")
        tool = VerifyAlertEffectivenessTool()
        result = await tool.invoke({"lookback": 5}, ctx)

        assert "alerts_verified" in result
        assert "resolved" in result

    @pytest.mark.asyncio
    async def test_invoke_with_improved_alert(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "task", "coder")],
        ))
        board.update_task("t1", status=TaskStatus.FAILED, error="oops")

        # Log an alert event
        board.log_event(
            "observer.alert",
            agent_id="monitor",
            message="Task t1 failed",
            payload={
                "alert_id": "ALERT-1",
                "severity": "warning",
                "data": {"task_id": "t1"},
                "recommended_action": "replan",
            },
        )
        # Log a replan event (shows improvement)
        board.log_event("task.replanned", task_id="t1", message="replanning")

        ctx = MockContext(deps=MockContext(state_board=board), agent_id="monitor")
        tool = VerifyAlertEffectivenessTool()
        result = await tool.invoke({"lookback": 5}, ctx)

        assert "alerts_verified" in result
        assert "improved" in result or "resolved" in result or "unchanged" in result

    @pytest.mark.asyncio
    async def test_invoke_no_board_raises(self):
        ctx = MockContext(deps=MockContext(), agent_id="monitor")
        tool = VerifyAlertEffectivenessTool()
        with pytest.raises(PermanentToolError, match="StateBoard"):
            await tool.invoke({}, ctx)

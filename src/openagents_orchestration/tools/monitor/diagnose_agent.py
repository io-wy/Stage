"""diagnose_agent — deep root-cause diagnosis with comparison and recommendations."""

from __future__ import annotations

import json
import time
from collections import Counter
from typing import Any

from openagents.errors.exceptions import PermanentToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


class DiagnoseAgentTool(ToolPlugin):
    """Deep root-cause diagnosis for a specific agent. Includes comparison and actionable recommendations."""

    name = "diagnose_agent"
    description = (
        "对指定Agent进行深度根因诊断。分析其生命周期、错误序列、工具使用模式、效率指标，"
        "并与同类Agent对比。最终给出具体的改进建议。"
    )
    durable_idempotent = True

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(concurrency_safe=True, side_effects="read_only")

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "要诊断的Agent ID",
                },
                "compare_with": {
                    "type": "string",
                    "description": "可选：和哪个Agent对比，如 'reviewer-t2'",
                },
            },
            "required": ["agent_id"],
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> str:
        deps = getattr(context, "deps", None)
        board = getattr(deps, "state_board", None) if deps else None
        if board is None:
            raise PermanentToolError("StateBoard not available", tool_name=self.name)

        agent_id = params.get("agent_id", "")
        compare_id = params.get("compare_with", "")

        agent = board.get_agent(agent_id)
        if not agent:
            return f"Agent '{agent_id}' 不存在"

        events = [e for e in board.events if e.agent_id == agent_id]
        diagnosis = {
            "agent_id": agent_id,
            "basic_metrics": agent.to_dict(),
        }

        # 1. Lifecycle analysis
        spawn_evt = next((e for e in events if "spawned" in e.event_type), None)
        end_evt = next((e for e in events if e.event_type in ("agent.completed", "agent.failed")), None)

        if spawn_evt:
            diagnosis["lifecycle"] = {
                "spawned_at": time.strftime("%H:%M:%S", time.localtime(spawn_evt.ts)),
            }
            if end_evt:
                diagnosis["lifecycle"]["ended_at"] = time.strftime("%H:%M:%S", time.localtime(end_evt.ts))
                diagnosis["lifecycle"]["duration_seconds"] = round(end_evt.ts - spawn_evt.ts, 1)
                diagnosis["lifecycle"]["outcome"] = "success" if "completed" in end_evt.event_type else "failure"
            else:
                diagnosis["lifecycle"]["status"] = "still_running"
                diagnosis["lifecycle"]["elapsed_so_far"] = round(time.time() - spawn_evt.ts, 1)

        # 2. Error analysis
        errors = [e for e in events if "failed" in e.event_type or "error" in e.event_type]
        if errors:
            error_types = Counter(e.event_type for e in errors)
            diagnosis["error_analysis"] = {
                "total_errors": len(errors),
                "error_breakdown": dict(error_types),
                "first_error": errors[0].message[:200] if errors else None,
                "last_error": errors[-1].message[:200] if errors else None,
                "escalating": self._is_escalating(errors),
            }

        # 3. Tool usage analysis
        tool_events = [e for e in events if "tool" in e.event_type]
        tool_counts = Counter()
        for e in tool_events:
            tid = e.payload.get("tool_id", "")
            if tid:
                tool_counts[tid] += 1

        diagnosis["tool_usage"] = {
            "total_tool_calls": len(tool_events),
            "tool_breakdown": dict(tool_counts),
            "read_write_ratio": self._calc_rw_ratio(tool_counts),
        }

        # 4. Efficiency analysis
        llm_events = [e for e in events if "llm" in e.event_type]
        latencies = [e.payload.get("latency_ms", 0) for e in llm_events if "latency_ms" in e.payload]
        diagnosis["efficiency"] = {
            "llm_calls": len(llm_events),
            "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0,
            "token_per_step": round(agent.token_used / max(agent.steps_used, 1), 1),
            "efficiency_grade": self._grade_efficiency(agent, latencies),
        }

        # 5. Comparison
        if compare_id:
            compare = board.get_agent(compare_id)
            if compare:
                diagnosis["comparison"] = self._compare_agents(agent, compare)

        # 6. Root cause + recommendations
        diagnosis["root_cause"] = self._infer_root_cause(diagnosis)
        diagnosis["recommendations"] = self._generate_recommendations(diagnosis)

        return json.dumps(diagnosis, indent=2, ensure_ascii=False)

    def _is_escalating(self, errors: list[Any]) -> bool:
        severity = {"llm.failed": 1, "tool.failed": 2, "agent.failed": 3}
        seq = [severity.get(e.event_type, 0) for e in errors]
        return seq == sorted(seq, reverse=True) and len(set(seq)) > 1

    def _calc_rw_ratio(self, tool_counts: Counter) -> dict[str, Any]:
        reads = sum(c for t, c in tool_counts.items() if "read" in t)
        writes = sum(c for t, c in tool_counts.items() if "write" in t)
        return {"reads": reads, "writes": writes, "ratio": round(reads / max(writes, 1), 1)}

    def _grade_efficiency(self, agent: Any, latencies: list[float]) -> str:
        if agent.steps_used == 0:
            return "N/A"
        if not latencies:
            return "unknown"
        avg_lat = sum(latencies) / len(latencies)
        if avg_lat > 30000 or agent.token_used > 40000:
            return "poor"
        if avg_lat > 15000 or agent.token_used > 20000:
            return "fair"
        return "good"

    def _compare_agents(self, a1: Any, a2: Any) -> dict[str, Any]:
        return {
            "token_delta": a1.token_used - a2.token_used,
            "steps_delta": a1.steps_used - a2.steps_used,
            "winner": a1.agent_id if a1.token_used < a2.token_used and a1.steps_used <= a2.steps_used else a2.agent_id,
        }

    def _infer_root_cause(self, diagnosis: dict[str, Any]) -> str:
        if "error_analysis" in diagnosis:
            errs = diagnosis["error_analysis"]
            if errs.get("escalating"):
                return "错误在升级，可能是任务规格不清或Agent能力不足"
            if errs["error_breakdown"].get("tool.failed", 0) >= 3:
                return "工具反复失败，可能是工具参数错误或权限问题"

        tools = diagnosis.get("tool_usage", {})
        rw = tools.get("read_write_ratio", {})
        if rw.get("reads", 0) > 10 and rw.get("writes", 0) == 0:
            return "只读不写，可能任务规格不清或Agent陷入信息收集循环"

        eff = diagnosis.get("efficiency", {})
        if eff.get("efficiency_grade") == "poor":
            if eff.get("avg_latency_ms", 0) > 30000:
                return "LLM延迟过高，可能是网络问题或模型过载"
            return "效率低下，可能是任务过大或Agent策略不当"

        return "无明显根因，建议增加监控数据"

    def _generate_recommendations(self, diagnosis: dict[str, Any]) -> list[str]:
        recs = []
        rc = diagnosis.get("root_cause", "")

        if "任务规格不清" in rc:
            recs.append("建议replan，拆分成更小的子任务")
        if "权限" in rc:
            recs.append("建议ask_human确认权限")
        if "网络" in rc or "延迟" in rc:
            recs.append("建议更换模型或等待网络恢复")
        if "只读不写" in rc:
            recs.append("建议调整prompt明确产出要求，或更换Agent类型")
        if "效率低下" in rc:
            recs.append("建议spawn_resident让Agent持续工作，减少重复初始化开销")
        if "工具反复失败" in rc:
            recs.append("建议检查工具参数，或给Agent更多上下文")

        if not recs:
            recs.append("当前数据不足以给出具体建议，建议继续观察")

        return recs

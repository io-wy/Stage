"""analyze_event_pattern — detect patterns, sequences, and anomalies in the event stream."""

from __future__ import annotations

import json
import time
from collections import Counter
from typing import Any

from openagents.errors.exceptions import PermanentToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


class AnalyzeEventPatternTool(ToolPlugin):
    """Analyze event stream for patterns: error sequences, tool usage, agent lifecycles, latency trends."""

    name = "analyze_event_pattern"
    description = (
        "Analyze the event stream for patterns and anomalies.\n"
        "- error_sequence: find repeated error patterns and escalation\n"
        "- tool_usage: analyze tool call sequences for suspicious patterns\n"
        "- agent_lifecycle: analyze complete agent spawn-to-done lifecycles\n"
        "- llm_latency_trend: time-series analysis of LLM call latencies"
    )
    durable_idempotent = True

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(concurrency_safe=True, side_effects="read_only")

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "enum": ["error_sequence", "tool_usage", "agent_lifecycle", "llm_latency_trend"],
                    "description": "Analysis pattern type",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Optional: only analyze events for this agent",
                },
                "time_window_seconds": {
                    "type": "integer",
                    "description": "Optional: only analyze events within this time window (seconds)",
                },
            },
            "required": ["pattern"],
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> str:
        deps = getattr(context, "deps", None)
        board = getattr(deps, "state_board", None) if deps else None
        if board is None:
            raise PermanentToolError("StateBoard not available", tool_name=self.name)

        pattern = params["pattern"]
        agent_filter = params.get("agent_id", "")
        window = params.get("time_window_seconds", 0)

        events = board.events
        if window > 0:
            cutoff = time.time() - window
            events = [e for e in events if e.ts > cutoff]
        if agent_filter:
            events = [e for e in events if e.agent_id == agent_filter]

        if pattern == "error_sequence":
            return self._analyze_errors(events)
        elif pattern == "tool_usage":
            return self._analyze_tools(events, agent_filter)
        elif pattern == "agent_lifecycle":
            return self._analyze_lifecycle(events, agent_filter)
        else:
            return self._analyze_latency(events, agent_filter)

    def _analyze_errors(self, events: list[Any]) -> str:
        """Find repeated error patterns and escalation."""
        error_events = [e for e in events if "failed" in e.event_type or "error" in e.event_type]

        by_agent: dict[str, list[Any]] = {}
        for e in error_events:
            by_agent.setdefault(e.agent_id or "unknown", []).append(e)

        patterns = []
        for agent_id, errs in by_agent.items():
            if len(errs) < 2:
                continue

            error_types = [e.event_type for e in errs]
            if len(set(error_types)) == 1:
                patterns.append({
                    "agent_id": agent_id,
                    "pattern": "repeated_same_error",
                    "error_type": error_types[0],
                    "count": len(errs),
                    "suggestion": "该Agent反复遇到同一错误，建议更换策略或replan",
                })
            else:
                severity_order = {"agent.failed": 3, "tool.failed": 2, "llm.failed": 1}
                severities = [severity_order.get(et, 0) for et in error_types]
                if severities == sorted(severities, reverse=True) and len(set(severities)) > 1:
                    patterns.append({
                        "agent_id": agent_id,
                        "pattern": "escalating_errors",
                        "sequence": error_types,
                        "suggestion": "错误在升级，建议立即intervene",
                    })

        return json.dumps(patterns, indent=2, ensure_ascii=False) if patterns else "未发现明显错误模式"

    def _analyze_tools(self, events: list[Any], agent_filter: str) -> str:
        """Analyze tool call sequences for suspicious patterns."""
        tool_events = [e for e in events if "tool" in e.event_type]

        by_agent: dict[str, list[Any]] = {}
        for e in tool_events:
            aid = e.agent_id or "unknown"
            if agent_filter and aid != agent_filter:
                continue
            by_agent.setdefault(aid, []).append(e)

        analysis = []
        for aid, evts in by_agent.items():
            tool_sequence = []
            for e in evts:
                tid = e.payload.get("tool_id", "")
                if not tid and "=" in e.message:
                    tid = e.message.split("=")[1].split()[0]
                tool_sequence.append(tid or "unknown")

            suspicious = []
            reads = sum(1 for t in tool_sequence if "read" in t)
            writes = sum(1 for t in tool_sequence if "write" in t)
            if reads > 5 and writes == 0:
                suspicious.append("excessive_reading_no_write")

            fails = [e.payload.get("tool_id", "") for e in evts if "failed" in e.event_type]
            fail_counts = Counter(fails)
            for tool, count in fail_counts.items():
                if count >= 3:
                    suspicious.append(f"repeated_tool_failure:{tool}({count}x)")

            analysis.append({
                "agent_id": aid,
                "tool_sequence": tool_sequence[-20:],
                "unique_tools": len(set(tool_sequence)),
                "suspicious_patterns": suspicious,
            })

        return json.dumps(analysis, indent=2, ensure_ascii=False)

    def _analyze_lifecycle(self, events: list[Any], agent_filter: str) -> str:
        """Analyze complete agent spawn-to-done lifecycles."""
        lifecycle_events = [e for e in events if "agent." in e.event_type]

        by_agent: dict[str, list[Any]] = {}
        for e in lifecycle_events:
            aid = e.agent_id or "unknown"
            if agent_filter and aid != agent_filter:
                continue
            by_agent.setdefault(aid, []).append({"event": e.event_type, "ts": e.ts})

        analysis = []
        for aid, evts in by_agent.items():
            if len(evts) < 2:
                continue

            spawn = next((e for e in evts if "spawned" in e["event"]), None)
            end = next((e for e in evts if e["event"] in ("agent.completed", "agent.failed")), None)

            if spawn:
                entry = {
                    "agent_id": aid,
                    "spawned_at": time.strftime("%H:%M:%S", time.localtime(spawn["ts"])),
                }
                if end:
                    entry["ended_at"] = time.strftime("%H:%M:%S", time.localtime(end["ts"]))
                    entry["duration_seconds"] = round(end["ts"] - spawn["ts"], 1)
                    entry["outcome"] = "success" if "completed" in end["event"] else "failure"
                else:
                    entry["status"] = "still_running"
                    entry["elapsed_so_far"] = round(time.time() - spawn["ts"], 1)
                analysis.append(entry)

        if analysis:
            avg_duration = sum(a.get("duration_seconds", 0) for a in analysis) / len(analysis)
            success_rate = sum(1 for a in analysis if a.get("outcome") == "success") / len(analysis)
            return json.dumps({
                "agents_analyzed": len(analysis),
                "avg_lifetime_seconds": round(avg_duration, 1),
                "success_rate_pct": round(success_rate * 100, 1),
                "details": analysis,
            }, indent=2, ensure_ascii=False)

        return "尚无完整生命周期数据"

    def _analyze_latency(self, events: list[Any], agent_filter: str) -> str:
        """Time-series analysis of LLM call latencies."""
        llm_events = [e for e in events if "llm" in e.event_type]

        latencies = []
        for e in llm_events:
            if agent_filter and e.agent_id != agent_filter:
                continue
            lat = e.payload.get("latency_ms", 0)
            if lat:
                latencies.append({"ts": e.ts, "latency_ms": lat, "agent_id": e.agent_id})

        if not latencies:
            return "无LLM延迟数据"

        latencies.sort(key=lambda x: x["ts"])
        values = [l["latency_ms"] for l in latencies]

        avg = sum(values) / len(values)
        max_v = max(values)
        min_v = min(values)

        mid = len(values) // 2
        first_half_avg = sum(values[:mid]) / max(mid, 1)
        second_half_avg = sum(values[mid:]) / max(len(values) - mid, 1)

        if second_half_avg > first_half_avg * 1.2:
            trend = "increasing"
        elif second_half_avg < first_half_avg * 0.8:
            trend = "decreasing"
        else:
            trend = "stable"

        outliers = [l for l in latencies if l["latency_ms"] > avg * 2]

        return json.dumps({
            "total_calls": len(latencies),
            "avg_latency_ms": round(avg, 1),
            "min_ms": round(min_v, 1),
            "max_ms": round(max_v, 1),
            "trend": trend,
            "trend_detail": f"前段平均{round(first_half_avg, 1)}ms → 后段平均{round(second_half_avg, 1)}ms",
            "outliers_count": len(outliers),
            "outliers": [{"agent_id": o["agent_id"], "latency_ms": round(o["latency_ms"], 1)} for o in outliers[:5]],
        }, indent=2, ensure_ascii=False)

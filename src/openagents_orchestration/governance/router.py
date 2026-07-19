"""Backend planning for Stage governance."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openagents_orchestration.intent_classifier import IntentFrame


@dataclass(frozen=True, slots=True)
class ExecutionNode:
    capability: str
    required: bool = True
    reason: str = ""
    condition: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "required": self.required,
            "reason": self.reason,
            "condition": self.condition,
        }


@dataclass(frozen=True, slots=True)
class GovernancePlan:
    route_label: str
    backends: list[str] = field(default_factory=list)
    nodes: list[ExecutionNode] = field(default_factory=list)
    needs_human: bool = False
    reason: str = ""
    confidence: float = 0.0
    business_process: str = "unknown"

    def to_dict(self) -> dict[str, object]:
        return {
            "route_label": self.route_label,
            "backends": list(self.backends),
            "nodes": [node.to_dict() for node in self.nodes],
            "needs_human": self.needs_human,
            "reason": self.reason,
            "confidence": self.confidence,
            "business_process": self.business_process,
        }


class GovernanceRouter:
    """Deterministic planner from intent frame to execution path."""

    def plan(self, frame: IntentFrame) -> GovernancePlan:
        route_label = self._route_label(frame.workflow_type, frame.business_process)
        backends = self._backends_for_frame(frame)
        nodes = self._nodes_for_frame(frame, backends)
        needs_human = self._needs_human(frame)
        reason = self._reason(frame, route_label, needs_human)
        return GovernancePlan(
            route_label=route_label,
            backends=backends,
            nodes=nodes,
            needs_human=needs_human,
            reason=reason,
            confidence=frame.confidence,
            business_process=frame.business_process,
        )

    @staticmethod
    def _route_label(workflow_type: str, business_process: str) -> str:
        if workflow_type == "service_case":
            return "service_desk"
        if workflow_type == "development":
            return "development"
        if workflow_type == "documentation":
            return "documentation"
        if business_process == "code_task":
            return "development"
        return "general"

    @staticmethod
    def _backends_for_frame(frame: IntentFrame) -> list[str]:
        if frame.workflow_type == "development":
            return ["claude_code"]
        backends = list(frame.backend_plan)
        evidence_requirements = set(frame.evidence_requirements)
        if evidence_requirements & {"source", "access_path", "service_state", "routing_choice"}:
            backends.insert(0, "rag_retrieval")
        if (
            frame.workflow_type != "service_case"
            and (
                frame.task_type in {"bug_fix", "feature", "test", "review"}
                or evidence_requirements & {"diff_or_patch"}
            )
        ):
            backends.append("claude_code")
        if (
            frame.workflow_type == "service_case"
            and (
                evidence_requirements & {"logs", "reproduction", "live_status"}
                or frame.complexity == "complex"
            )
        ):
            backends.append("subagent")
        if (
            frame.human_handoff_policy != "none"
            or frame.risk_class in {"sensitive", "privileged_action"}
        ):
            backends.append("human_channel")
        if not backends:
            backends = ["rag_retrieval"]
        return list(dict.fromkeys(backends))

    @staticmethod
    def _nodes_for_frame(frame: IntentFrame, backends: list[str]) -> list[ExecutionNode]:
        nodes: list[ExecutionNode] = []
        if "rag_retrieval" in backends:
            nodes.append(
                ExecutionNode(
                    capability="rag_retrieval",
                    reason="source evidence required",
                    condition="always when retrieval evidence is needed",
                )
            )
        if "subagent" in backends:
            nodes.append(
                ExecutionNode(
                    capability="subagent",
                    reason="service triage or synthesis requires multi-step reasoning",
                    condition="service_case with logs/reproduction/live status/complexity",
                )
            )
        if "claude_code" in backends:
            nodes.append(
                ExecutionNode(
                    capability="claude_code",
                    reason="code-oriented task or diff/patch generation required",
                    condition="development task or explicit patch workflow",
                )
            )
        if "human_channel" in backends:
            nodes.append(
                ExecutionNode(
                    capability="human_channel",
                    reason="human confirmation or privileged action required",
                    condition="risk, handoff, or missing slots",
                )
            )
        nodes.append(
            ExecutionNode(
                capability="verify",
                required=True,
                reason="verify evidence, safety, and closure before finalizing",
                condition="always",
            )
        )
        nodes.append(
            ExecutionNode(
                capability="close",
                required=True,
                reason="close or block case after governance checks",
                condition="always",
            )
        )
        return nodes

    @staticmethod
    def _needs_human(frame: IntentFrame) -> bool:
        if frame.human_handoff_policy == "none":
            return frame.risk_class in {"sensitive", "privileged_action"}
        if frame.risk_class in {"sensitive", "privileged_action"}:
            return True
        return frame.confidence < 0.7 and frame.business_process == "unknown"

    @staticmethod
    def _reason(frame: IntentFrame, route_label: str, needs_human: bool) -> str:
        parts = [f"route={route_label}", f"business={frame.business_process}"]
        if needs_human:
            parts.append("human=required")
        if frame.ambiguity_notes:
            parts.append("ambiguous")
        return ", ".join(parts)

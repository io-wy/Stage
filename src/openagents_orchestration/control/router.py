"""Backend planning for Stage governance."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openagents_orchestration.control.models import ActionPlan
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
    action_plan: ActionPlan | None = None
    needs_human: bool = False
    reason: str = ""
    confidence: float = 0.0
    business_process: str = "unknown"

    def to_dict(self) -> dict[str, object]:
        return {
            "route_label": self.route_label,
            "backends": list(self.backends),
            "nodes": [node.to_dict() for node in self.nodes],
            "action_plan": self.action_plan.model_dump() if self.action_plan else None,
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
        action_plan = self._action_plan_for_frame(frame, backends, needs_human)
        reason = self._reason(frame, route_label, needs_human)
        return GovernancePlan(
            route_label=route_label,
            backends=backends,
            nodes=nodes,
            action_plan=action_plan,
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
    def _action_plan_for_frame(
        frame: IntentFrame,
        backends: list[str],
        needs_human: bool,
    ) -> ActionPlan:
        executor = _executor_for_backends(backends, needs_human)
        side_effect_level = _side_effect_level(frame, executor)
        return ActionPlan(
            case_id="",
            run_id="",
            action_type=_action_type(frame, side_effect_level),
            executor=executor,
            adapter_id=frame.execution_adapter,
            adapter_tools=list(frame.adapter_tools),
            side_effect_level=side_effect_level,
            allowed_actions=_allowed_actions(side_effect_level),
            forbidden_actions=_forbidden_actions(side_effect_level),
            required_approval_fields=_required_approval_fields(frame, side_effect_level),
            required_evidence=list(frame.evidence_requirements),
            verify_requirements=_verify_requirements(frame, side_effect_level),
            rollback_plan=_rollback_plan(side_effect_level),
            metadata={
                "business_process": frame.business_process,
                "risk_class": frame.risk_class,
                "source": "governance_router",
            },
        )

    @staticmethod
    def _reason(frame: IntentFrame, route_label: str, needs_human: bool) -> str:
        parts = [f"route={route_label}", f"business={frame.business_process}"]
        if needs_human:
            parts.append("human=required")
        if frame.ambiguity_notes:
            parts.append("ambiguous")
        return ", ".join(parts)


def _executor_for_backends(backends: list[str], needs_human: bool) -> str:
    if needs_human or "human_channel" in backends:
        return "human"
    if "claude_code" in backends:
        return "claude_code"
    if "subagent" in backends:
        return "subagent"
    if "rag_retrieval" in backends:
        return "rag"
    return "noop"


def _side_effect_level(frame: IntentFrame, executor: str) -> str:
    if frame.risk_class == "privileged_action":
        return "privileged_write"
    if frame.risk_class == "sensitive":
        return "sensitive_read"
    if executor == "claude_code":
        return "workspace_write"
    return "read_only"


def _action_type(frame: IntentFrame, side_effect_level: str) -> str:
    if side_effect_level == "privileged_write":
        return f"{frame.business_process}:privileged_action"
    if side_effect_level == "workspace_write":
        return "development_workspace_action"
    if side_effect_level == "sensitive_read":
        return f"{frame.business_process}:sensitive_triage"
    return "answer_or_handoff"


def _allowed_actions(side_effect_level: str) -> list[str]:
    if side_effect_level == "privileged_write":
        return ["prepare_change", "request_human_approval"]
    if side_effect_level == "workspace_write":
        return ["inspect_workspace", "propose_patch", "run_verification"]
    if side_effect_level == "sensitive_read":
        return ["retrieve_evidence", "summarize_findings", "request_human_confirmation"]
    return ["retrieve_evidence", "answer_user", "create_handoff"]


def _forbidden_actions(side_effect_level: str) -> list[str]:
    forbidden = ["bypass_governance", "close_without_verification"]
    if side_effect_level == "privileged_write":
        forbidden.extend(["external_write_without_approval", "grant_without_approval"])
    if side_effect_level == "workspace_write":
        forbidden.append("commit_or_deploy_without_verification")
    if side_effect_level == "sensitive_read":
        forbidden.append("expose_sensitive_evidence")
    return forbidden


def _required_approval_fields(frame: IntentFrame, side_effect_level: str) -> list[str]:
    if frame.permission_required_fields:
        return list(frame.permission_required_fields)
    if side_effect_level == "privileged_write":
        if "approval" in frame.business_process:
            return ["approver", "target", "scope", "ticket_id"]
        return ["approver", "target", "scope"]
    return []


def _verify_requirements(frame: IntentFrame, side_effect_level: str) -> list[str]:
    requirements = ["evidence_relevance", "safety_scan", "closure_gate"]
    if side_effect_level in {"privileged_write", "workspace_write"}:
        requirements.append("post_execution_state_check")
    if frame.evidence_requirements:
        requirements.append("required_evidence_present")
    return list(dict.fromkeys(requirements))


def _rollback_plan(side_effect_level: str) -> list[str]:
    if side_effect_level == "privileged_write":
        return ["revoke_or_restore_previous_permission"]
    if side_effect_level == "workspace_write":
        return ["revert_patch_or_restore_workspace"]
    return []

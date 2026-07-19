"""Permission gates for portable Stage governance."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from openagents_orchestration.governance.router import GovernancePlan
from openagents_orchestration.intent_classifier import IntentFrame

PermissionVerdict = Literal["allow", "block", "needs_human"]

_REQUIRED_APPROVAL_FIELDS = ["approver", "target", "scope"]
_READ_ONLY_ACTIONS = {
    "answer_user",
    "ask_human",
    "request_human",
    "create_handoff",
    "retrieve_evidence",
    "summarize",
}
_PRIVILEGED_MARKERS = (
    "grant access",
    "grant permission",
    "use admin access",
    "admin access directly",
    "maintainer permission",
    "owner access",
    "reset password",
    "sudo",
    "管理员权限",
    "用管理员",
    "授予权限",
    "开通权限",
    "授权",
    "重置密码",
)
_WRITE_REQUEST_MARKERS = (
    "approve",
    "create",
    "delete",
    "deploy",
    "execute",
    "grant",
    "merge",
    "modify",
    "remove",
    "reset",
    "revoke",
    "update",
    "write",
    "开通",
    "创建",
    "删除",
    "执行",
    "批准",
    "授予",
    "授权",
    "撤销",
    "更新",
    "修改",
    "重置",
)
_WRITE_CLAIM_MARKERS = (
    "approved",
    "created",
    "deleted",
    "deployed",
    "executed",
    "granted",
    "merged",
    "modified",
    "removed",
    "reset",
    "revoked",
    "updated",
    "已创建",
    "已删除",
    "已执行",
    "已批准",
    "已开通",
    "已授予",
    "已授权",
    "已撤销",
    "已更新",
    "已修改",
    "已重置",
)


@dataclass(frozen=True, slots=True)
class PermissionPolicy:
    required_fields: list[str] = field(default_factory=lambda: list(_REQUIRED_APPROVAL_FIELDS))
    privileged_action_markers: list[str] = field(default_factory=lambda: list(_PRIVILEGED_MARKERS))
    write_request_markers: list[str] = field(default_factory=lambda: list(_WRITE_REQUEST_MARKERS))
    write_claim_markers: list[str] = field(default_factory=lambda: list(_WRITE_CLAIM_MARKERS))
    read_only_actions: list[str] = field(default_factory=lambda: sorted(_READ_ONLY_ACTIONS))

    @classmethod
    def from_overrides(
        cls,
        *,
        required_fields: list[str] | None = None,
        privileged_action_markers: list[str] | None = None,
        write_request_markers: list[str] | None = None,
        write_claim_markers: list[str] | None = None,
        read_only_actions: list[str] | None = None,
    ) -> PermissionPolicy:
        base = cls()
        return cls(
            required_fields=list(required_fields or base.required_fields),
            privileged_action_markers=[
                *base.privileged_action_markers,
                *(privileged_action_markers or []),
            ],
            write_request_markers=[
                *base.write_request_markers,
                *(write_request_markers or []),
            ],
            write_claim_markers=[
                *base.write_claim_markers,
                *(write_claim_markers or []),
            ],
            read_only_actions=list(read_only_actions or base.read_only_actions),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "required_fields": list(self.required_fields),
            "privileged_action_markers": list(self.privileged_action_markers),
            "write_request_markers": list(self.write_request_markers),
            "write_claim_markers": list(self.write_claim_markers),
            "read_only_actions": list(self.read_only_actions),
        }


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    gate: str
    phase: str
    capability: str
    decision: PermissionVerdict
    risk_class: str
    required_fields: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "phase": self.phase,
            "capability": self.capability,
            "decision": self.decision,
            "risk_class": self.risk_class,
            "required_fields": list(self.required_fields),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class PermissionCheckResult:
    phase: str
    decisions: list[PermissionDecision]

    @property
    def passed(self) -> bool:
        return all(decision.decision == "allow" for decision in self.decisions)

    @property
    def blocked(self) -> bool:
        return any(decision.decision == "block" for decision in self.decisions)

    @property
    def needs_human(self) -> bool:
        return any(decision.decision == "needs_human" for decision in self.decisions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "passed": self.passed,
            "blocked": self.blocked,
            "needs_human": self.needs_human,
            "decisions": [decision.to_dict() for decision in self.decisions],
        }


class PermissionEngine:
    """Evaluate whether a case may perform or claim governed actions."""

    def preflight(
        self,
        frame: IntentFrame,
        route_plan: GovernancePlan,
        *,
        prompt: str,
        approvals: dict[str, Any] | None = None,
        policy: PermissionPolicy | None = None,
    ) -> PermissionCheckResult:
        permission_policy = policy or PermissionPolicy()
        capability = _requested_capability(frame, route_plan, prompt, permission_policy)
        decision = _preflight_decision(frame, capability, approvals, permission_policy)
        return PermissionCheckResult(phase="preflight", decisions=[decision])

    def postcheck(
        self,
        frame: IntentFrame,
        route_plan: GovernancePlan,
        *,
        output: dict[str, Any],
        approvals: dict[str, Any] | None = None,
        policy: PermissionPolicy | None = None,
    ) -> PermissionCheckResult:
        permission_policy = policy or PermissionPolicy()
        capability = _claimed_capability(frame, route_plan, output, permission_policy)
        decision = _postcheck_decision(
            frame, capability, output, approvals, permission_policy
        )
        return PermissionCheckResult(phase="postcheck", decisions=[decision])


def merge_permission_results(
    preflight: PermissionCheckResult,
    postcheck: PermissionCheckResult,
) -> PermissionCheckResult:
    return PermissionCheckResult(
        phase="combined",
        decisions=[*preflight.decisions, *postcheck.decisions],
    )


def _preflight_decision(
    frame: IntentFrame,
    capability: str,
    approvals: dict[str, Any] | None,
    policy: PermissionPolicy,
) -> PermissionDecision:
    if capability in {"privileged_action", "write_action"} and not _has_approval(
        approvals, policy.required_fields
    ):
        return PermissionDecision(
            gate="permission_preflight",
            phase="preflight",
            capability=capability,
            decision="needs_human",
            risk_class=frame.risk_class,
            required_fields=list(policy.required_fields),
            reason="write or privileged action requires human approval before execution",
        )
    return PermissionDecision(
        gate="permission_preflight",
        phase="preflight",
        capability=capability,
        decision="allow",
        risk_class=frame.risk_class,
        reason="no privileged or external write action requested",
    )


def _postcheck_decision(
    frame: IntentFrame,
    capability: str,
    output: dict[str, Any],
    approvals: dict[str, Any] | None,
    policy: PermissionPolicy,
) -> PermissionDecision:
    output_approvals = output.get("approval") if isinstance(output.get("approval"), dict) else None
    effective_approvals = approvals or output_approvals
    if capability == "claimed_write_action" and not _has_approval(
        effective_approvals, policy.required_fields
    ):
        return PermissionDecision(
            gate="permission_postcheck",
            phase="postcheck",
            capability=capability,
            decision="block",
            risk_class=frame.risk_class,
            required_fields=list(policy.required_fields),
            reason="backend output claims a write or privileged action without approval evidence",
        )
    return PermissionDecision(
        gate="permission_postcheck",
        phase="postcheck",
        capability=capability,
        decision="allow",
        risk_class=frame.risk_class,
        reason="output does not claim an unapproved governed action",
    )


def _requested_capability(
    frame: IntentFrame,
    route_plan: GovernancePlan,
    prompt: str,
    policy: PermissionPolicy,
) -> str:
    text = prompt.lower()
    has_privileged_action = _contains_marker(text, policy.privileged_action_markers)
    has_write_request = _contains_marker(text, policy.write_request_markers)
    if has_privileged_action or (
        frame.risk_class == "privileged_action" and has_write_request
    ):
        return "privileged_action"
    if has_write_request:
        return "write_action"
    if frame.risk_class == "sensitive":
        return "sensitive_read_or_triage"
    if any(node.capability == "claude_code" for node in route_plan.nodes):
        return "code_or_tool_analysis"
    return "read_or_analyze"


def _claimed_capability(
    frame: IntentFrame,
    route_plan: GovernancePlan,
    output: dict[str, Any],
    policy: PermissionPolicy,
) -> str:
    actions = [
        str(action).strip().lower()
        for action in output.get("actions", [])
        if str(action).strip()
    ]
    read_only_actions = set(policy.read_only_actions)
    if any(action not in read_only_actions for action in actions):
        return "claimed_write_action"
    if output.get("closed") is True and frame.risk_class == "privileged_action":
        public_text = _flatten_public_text(output)
        if _contains_marker(public_text, policy.write_claim_markers):
            return "claimed_write_action"
    if frame.risk_class == "sensitive" and any(
        node.capability == "subagent" for node in route_plan.nodes
    ):
        return "sensitive_read_or_triage"
    return "read_or_analyze"


def _has_approval(
    approvals: dict[str, Any] | None,
    required_fields: list[str],
) -> bool:
    if not approvals:
        return False
    if approvals.get("approved") is False:
        return False
    if approvals.get("approval_id") or approvals.get("human_approved") is True:
        return True
    return all(str(approvals.get(field, "")).strip() for field in required_fields)


def _contains_marker(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    for marker in markers:
        normalized = marker.lower()
        if any(not char.isascii() for char in normalized):
            if normalized in lowered:
                return True
            continue
        if re.search(rf"\b{re.escape(normalized)}\b", lowered):
            return True
    return False


def _flatten_public_text(output: dict[str, Any]) -> str:
    return json.dumps(
        {
            "answer": output.get("answer", ""),
            "actions": output.get("actions", []),
            "failure_mode": output.get("failure_mode", ""),
        },
        ensure_ascii=False,
        sort_keys=True,
    ).lower()

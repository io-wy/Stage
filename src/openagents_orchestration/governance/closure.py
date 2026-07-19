"""Deterministic closure gating for Stage governance."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openagents_orchestration.governance.safety import SafetyScanResult
from openagents_orchestration.intent_classifier import IntentFrame

_HANDOFF_REQUIRED_POLICIES = {
    "ask_if_needed",
    "ask_if_missing_identity_or_state",
    "ask_for_sponsor_and_scope",
    "ask_for_maintainer_if_internal_access_needed",
    "ask_for_os_and_network_context",
    "ask_for_authorized_operator_and_status",
    "ask_for_logs_and_reproduction",
    "ask_for_owner_and_safety_constraints",
    "ask_for_change_control_fields",
    "ask_for_approval_safety_fields",
    "ask_for_reproduction_fields",
    "ask_for_live_status_and_operator",
    "ask_for_network_context",
    "ask_for_maintenance_escalation",
}


@dataclass(frozen=True, slots=True)
class ClosureDecision:
    closed: bool
    reason: str
    reasons: list[str] = field(default_factory=list)
    needs_human: bool = False


def _needs_human_handoff(frame: IntentFrame) -> bool:
    if frame.human_handoff_policy == "none":
        return False
    if frame.human_handoff_policy == "ask_if_destination_is_ambiguous":
        return False
    if frame.human_handoff_policy in _HANDOFF_REQUIRED_POLICIES:
        return True
    if frame.human_handoff_policy == "ask_if_needed":
        return frame.risk_class in {"sensitive", "privileged_action"} or frame.confidence < 0.7
    return False


def evaluate_closure(
    frame: IntentFrame,
    case_output: dict[str, Any],
    safety: SafetyScanResult,
    verifier: Any,
    permissions: Any | None = None,
) -> ClosureDecision:
    """Evaluate whether a governed case may close."""

    model_closed = case_output.get("closed") is True
    verifier_passed = bool(getattr(verifier, "passed", False))
    if isinstance(verifier, dict):
        verifier_passed = bool(verifier.get("passed"))
    permission_blocked = bool(getattr(permissions, "blocked", False))
    permission_needs_human = bool(getattr(permissions, "needs_human", False))
    if isinstance(permissions, dict):
        permission_blocked = bool(permissions.get("blocked"))
        permission_needs_human = bool(permissions.get("needs_human"))

    reasons: list[str] = []
    if safety.blocked:
        reasons.append("safety")
    if not verifier_passed:
        reasons.append("verification")
    if permission_blocked:
        reasons.append("permission")
    if permission_needs_human:
        reasons.append("permission_handoff")
    requires_handoff = _needs_human_handoff(frame)
    if model_closed and requires_handoff and not case_output.get("human_questions"):
        reasons.append("human_handoff")

    if reasons:
        return ClosureDecision(
            closed=False,
            reason=(
                "safety_or_verification_failed"
                if {"safety", "verification"} & set(reasons)
                else "permission_policy_failed"
                if "permission" in reasons
                else "handoff_required"
            ),
            reasons=reasons,
            needs_human=bool(
                {"human_handoff", "permission_handoff", "verification"} & set(reasons)
            ),
        )

    return ClosureDecision(closed=model_closed, reason="ok" if model_closed else "not_closed")

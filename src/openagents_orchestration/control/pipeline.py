"""Stage governance execution pipeline."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from openagents_orchestration.backend.claude_code import ClaudeCodeAdapter
from openagents_orchestration.backend.contracts import CaseBackend
from openagents_orchestration.control.audit import AuditStore
from openagents_orchestration.control.closure import (
    ClosureDecision,
    evaluate_closure,
)
from openagents_orchestration.control.domain import (
    GovernanceDomainProfile,
    GovernanceDomainResolver,
    apply_domain_profile,
)
from openagents_orchestration.control.evidence import (
    build_public_evidence_summary,
    evidence_entries_from_rag_log,
)
from openagents_orchestration.control.models import (
    ActionPlan,
    ActionResult,
    CaseAuditEvent,
    EvidenceEntry,
)
from openagents_orchestration.control.permissions import (
    PermissionCheckResult,
    PermissionEngine,
    PermissionPolicy,
    merge_permission_results,
)
from openagents_orchestration.control.router import GovernancePlan, GovernanceRouter
from openagents_orchestration.control.safety import (
    SafetyScanResult,
    scan_public_output,
)
from openagents_orchestration.control.traceability import (
    ClaimTraceEntry,
    build_source_to_claim_trace,
    traceability_gate_passed,
)
from openagents_orchestration.intent_classifier import IntentClassifier, IntentFrame
from openagents_orchestration.rag.runlog import RagQueryRunLog

_VALID_FAILURE_MODES = {
    "missing_required_information",
    "source_conflict",
    "need_human_judgment",
    "tool_unavailable",
    "execution_timeout",
    "auth_or_network_failure",
    "over_budget",
    "permission_policy_failure",
    "safety_policy_failure",
    "verification_failure",
}

_TOKEN = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "be",
    "can",
    "case",
    "complete",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "member",
    "need",
    "needs",
    "of",
    "or",
    "please",
    "safe",
    "should",
    "the",
    "to",
    "use",
    "user",
    "ways",
    "what",
    "with",
    "帮",
    "我",
    "的",
    "了",
    "吗",
    "么",
    "是",
    "有",
    "和",
    "要",
    "需要",
    "怎么",
}

_HUMAN_QUESTION_TEMPLATES = {
    "identity_and_access_support": [
        "你是否已经按文档等待重试窗口，并重新尝试注册或登录？",
    ],
    "developer_platform_access_approval": [
        "请补充审批人、目标项目或仓库、申请的权限范围和工单号。",
    ],
    "collaboration_platform_change_approval": [
        "请补充具体 Overleaf 变更内容、审批人、影响项目和变更范围。",
    ],
    "shared_facility_support": [
        "这个问题是否需要升级给维护人处理内部设备维护？",
    ],
    "internal_network_access_support": [
        "请补充当前操作系统、设备类型和所在网络位置。",
    ],
    "communications_platform_incident": [
        "请补充可用的备用通知渠道或负责处理的 owner。",
    ],
    "infrastructure_power_incident": [
        "UPS 是否仍在告警？请补充可确认状态的授权操作人。",
    ],
    "developer_service_defect_triage": [
        "请补充日志、报错信息和可复现步骤。",
    ],
    "equipment_use_approval": [
        "请补充设备 owner、使用范围和需要遵守的安全约束。",
    ],
    "media_service_operations": [
        "这个内容应该进入 Jellyfin，还是进入媒体仓库路径？",
    ],
    "member_onboarding_routing": [
        "你想选择哪个方向：软件研发、Web、Python 还是算法？",
    ],
}


class ReplayCaseBackend:
    """Deterministic backend for tests and recorded benchmark replay."""

    def __init__(
        self,
        case_result: dict[str, Any],
        *,
        execution_mode: str = "replay",
    ):
        self._case_result = case_result
        self.execution_mode = execution_mode
        self.calls = 0

    def run(
        self,
        *,
        case_id: str,
        run_id: str,
        prompt: str,
        route_plan: GovernancePlan,
        audit_store: AuditStore,
    ) -> dict[str, Any]:
        self.calls += 1
        audit_store.append(
            CaseAuditEvent(
                case_id=case_id,
                run_id=run_id,
                event_type="tool_invoked",
                payload={
                    "backend": "replay",
                    "execution_mode": self.execution_mode,
                    "route": route_plan.to_dict(),
                },
            )
        )
        return json.loads(json.dumps(self._case_result, ensure_ascii=False))


class ClaudeCodeReplayBackend:
    """Replay backend that imports a recorded Claude Code artifact directory."""

    execution_mode = "claude_code_replay"

    def __init__(self, artifact_dir: str | Path):
        self.artifact_dir = Path(artifact_dir)

    def run(
        self,
        *,
        case_id: str,
        run_id: str,
        prompt: str,
        route_plan: GovernancePlan,
        audit_store: AuditStore,
    ) -> dict[str, Any]:
        imported = ClaudeCodeAdapter(audit_store=audit_store).import_run(
            case_id=case_id,
            run_id=run_id,
            artifact_dir=self.artifact_dir,
        )
        return json.loads(json.dumps(imported.public_case_result, ensure_ascii=False))


@dataclass(frozen=True, slots=True)
class StageGovernancePipelineResult:
    case_id: str
    run_id: str
    audit_path: str
    execution_mode: str
    intent_frame: IntentFrame
    domain_profile: GovernanceDomainProfile
    governance_frame: IntentFrame
    route_plan: GovernancePlan
    permission_preflight_result: PermissionCheckResult
    permission_postcheck_result: PermissionCheckResult
    permission_result: PermissionCheckResult
    evidence_entries: list[EvidenceEntry]
    claim_trace: list[ClaimTraceEntry]
    safety_result: SafetyScanResult
    closure_decision: ClosureDecision
    backend_output: dict[str, Any]
    governed_case_result: dict[str, Any]
    governance_payload: dict[str, Any]
    audit_events: list[str]


class StageGovernancePipeline:
    """Run Stage governance around a backend execution."""

    def __init__(
        self,
        *,
        intent_classifier: IntentClassifier | None = None,
        domain_resolver: GovernanceDomainResolver | None = None,
        router: GovernanceRouter | None = None,
        permission_engine: PermissionEngine | None = None,
    ):
        self._intent_classifier = intent_classifier or IntentClassifier(llm_client=None)
        self._domain_resolver = domain_resolver or GovernanceDomainResolver()
        self._router = router or GovernanceRouter()
        self._permission_engine = permission_engine or PermissionEngine()

    def run(
        self,
        *,
        prompt: str,
        routing_prompt: str | None = None,
        case_id: str,
        run_id: str,
        backend: CaseBackend,
        audit_path: str | Path,
        approvals: dict[str, Any] | None = None,
    ) -> StageGovernancePipelineResult:
        audit_store = AuditStore(audit_path)
        routing_text = routing_prompt or prompt
        raw_intent_frame = self._intent_classifier.classify_frame(routing_text)
        domain_profile = self._domain_resolver.resolve(routing_text)
        governance_frame = apply_domain_profile(raw_intent_frame, domain_profile)
        route_plan = self._router.plan(governance_frame)
        action_plan = _stamp_action_plan(
            route_plan.action_plan,
            case_id=case_id,
            run_id=run_id,
        )
        route_plan = replace(route_plan, action_plan=action_plan)
        permission_policy = PermissionPolicy.from_overrides(
            required_fields=domain_profile.permission_required_fields,
            privileged_action_markers=domain_profile.permission_action_markers,
        )
        permission_preflight_result = self._permission_engine.preflight(
            governance_frame,
            route_plan,
            prompt=routing_text,
            approvals=approvals,
            policy=permission_policy,
        )

        backend_output = backend.run(
            case_id=case_id,
            run_id=run_id,
            prompt=routing_text,
            route_plan=route_plan,
            audit_store=audit_store,
        )
        permission_postcheck_result = self._permission_engine.postcheck(
            governance_frame,
            route_plan,
            output=backend_output,
            approvals=approvals,
            policy=permission_policy,
        )
        action_result = _action_result_from_backend(
            backend_output,
            action_plan=action_plan,
            executor=backend.execution_mode,
        )
        permission_result = merge_permission_results(
            permission_preflight_result,
            permission_postcheck_result,
        )
        evidence_entries = _build_evidence_entries(
            backend_output,
            case_id=case_id,
            run_id=run_id,
            retrieval_query=routing_text,
            forbidden_patterns=domain_profile.safety_forbidden_patterns,
        )
        safety_result = scan_public_output(
            backend_output,
            forbidden_patterns=domain_profile.safety_forbidden_patterns,
        )
        verification = _verify_backend_output(
            backend_output,
            evidence_entries,
            safety_result,
        )
        verifier = SimpleNamespace(
            passed=verification["passed"],
            errors={"reasons": verification["reasons"]},
        )
        closure_decision = evaluate_closure(
            governance_frame,
            backend_output,
            safety_result,
            verifier,
            permissions=permission_result,
        )
        governed_case_result = _govern_case_result(
            backend_output,
            intent_frame=governance_frame,
            evidence_entries=evidence_entries,
            safety_result=safety_result,
            permission_result=permission_result,
            closure_decision=closure_decision,
            verifier_passed=verifier.passed,
        )
        claim_trace = build_source_to_claim_trace(governed_case_result, evidence_entries)
        governed_case_result["claim_trace"] = [
            entry.to_dict() for entry in claim_trace
        ]
        governed_case_result["hard_gates"][
            "traceability_passed"
        ] = traceability_gate_passed(claim_trace)
        governed_case_result["evidence"] = (
            build_public_evidence_summary(evidence_entries)
            or governed_case_result.get("evidence", [])
        )
        evidence_summary = build_public_evidence_summary(evidence_entries)
        audit_events = _append_governance_events(
            audit_store,
            case_id=case_id,
            run_id=run_id,
            intent_frame=raw_intent_frame,
            domain_profile=domain_profile,
            governance_frame=governance_frame,
            route_plan=route_plan,
            permission_preflight_result=permission_preflight_result,
            permission_postcheck_result=permission_postcheck_result,
            action_plan=action_plan,
            action_result=action_result,
            evidence_entries=evidence_entries,
            claim_trace=claim_trace,
            safety_result=safety_result,
            closure_decision=closure_decision,
            governed_case_result=governed_case_result,
            verification=verification,
        )
        governance_payload = {
            "case_id": case_id,
            "run_id": run_id,
            "execution_mode": backend.execution_mode,
            "routing_prompt": routing_text,
            "intent": raw_intent_frame.to_dict(),
            "domain": domain_profile.to_dict(),
            "governance_frame": governance_frame.to_dict(),
            "route": route_plan.to_dict(),
            "action_plan": action_plan.model_dump(),
            "action_result": action_result.model_dump(),
            "policy": {
                "permission": permission_policy.to_dict(),
                "safety_forbidden_patterns": list(
                    domain_profile.safety_forbidden_patterns or []
                ),
            },
            "permissions": {
                "preflight": permission_preflight_result.to_dict(),
                "postcheck": permission_postcheck_result.to_dict(),
                "combined": permission_result.to_dict(),
            },
            "evidence": [entry.model_dump() for entry in evidence_entries],
            "public_evidence": evidence_summary,
            "verification": verification,
            "claim_trace": [entry.to_dict() for entry in claim_trace],
            "safety": {
                "blocked": safety_result.blocked,
                "findings": [finding.model_dump() for finding in safety_result.findings],
            },
            "closure": {
                "closed": closure_decision.closed,
                "reason": closure_decision.reason,
                "reasons": list(closure_decision.reasons),
                "needs_human": closure_decision.needs_human,
            },
            "audit_events": audit_events,
            "hard_gates": governed_case_result["hard_gates"],
            "audit_path": str(audit_path),
        }
        return StageGovernancePipelineResult(
            case_id=case_id,
            run_id=run_id,
            audit_path=str(audit_path),
            execution_mode=backend.execution_mode,
            intent_frame=raw_intent_frame,
            domain_profile=domain_profile,
            governance_frame=governance_frame,
            route_plan=route_plan,
            permission_preflight_result=permission_preflight_result,
            permission_postcheck_result=permission_postcheck_result,
            permission_result=permission_result,
            evidence_entries=evidence_entries,
            claim_trace=claim_trace,
            safety_result=safety_result,
            closure_decision=closure_decision,
            backend_output=backend_output,
            governed_case_result=governed_case_result,
            governance_payload=governance_payload,
            audit_events=audit_events,
        )


def _stamp_action_plan(
    action_plan: ActionPlan | None,
    *,
    case_id: str,
    run_id: str,
) -> ActionPlan:
    if action_plan is None:
        return ActionPlan(case_id=case_id, run_id=run_id)
    return action_plan.model_copy(update={"case_id": case_id, "run_id": run_id})


def _action_result_from_backend(
    backend_output: dict[str, Any],
    *,
    action_plan: ActionPlan,
    executor: str,
) -> ActionResult:
    raw = backend_output.get("action_result")
    if isinstance(raw, dict):
        return ActionResult(
            action_id=str(raw.get("action_id") or action_plan.action_id),
            executor=str(raw.get("executor") or action_plan.executor or executor),
            executed=bool(raw.get("executed", False)),
            actions_taken=[
                str(item) for item in raw.get("actions_taken", []) if str(item).strip()
            ],
            side_effects=[
                str(item) for item in raw.get("side_effects", []) if str(item).strip()
            ],
            external_refs={
                str(key): str(value)
                for key, value in dict(raw.get("external_refs", {})).items()
            },
            verification_claims=[
                str(item)
                for item in raw.get("verification_claims", [])
                if str(item).strip()
            ],
            errors=[str(item) for item in raw.get("errors", []) if str(item).strip()],
            metadata=dict(raw.get("metadata", {})),
        )
    return ActionResult(
        action_id=action_plan.action_id,
        executor=action_plan.executor or executor,
        executed=False,
        actions_taken=[
            str(action)
            for action in backend_output.get("actions", [])
            if str(action).strip() and str(action).strip() != "answer_user"
        ],
        errors=[],
        metadata={"source": "backend_output_default"},
    )


def _build_evidence_entries(
    backend_output: dict[str, Any],
    *,
    case_id: str,
    run_id: str,
    retrieval_query: str,
    forbidden_patterns: list[str] | None = None,
) -> list[EvidenceEntry]:
    rag_log = backend_output.get("rag_log")
    if isinstance(rag_log, dict):
        try:
            parsed_log = RagQueryRunLog.model_validate(rag_log)
        except ValueError:
            parsed_log = None
        if parsed_log is not None:
            entries = evidence_entries_from_rag_log(
                parsed_log,
                case_id=case_id,
                run_id=run_id,
            )
            for entry in entries:
                relevance = _evidence_relevance(
                    retrieval_query,
                    source_ref=entry.source_ref,
                    summary=entry.summary,
                    score=_coerce_float(entry.metadata.get("score")),
                )
                entry.metadata["relevance"] = relevance
                entry.selected = relevance["passed"] and entry.sensitivity == "public_safe"
            return entries
    return _build_evidence_entries_from_dicts(
        backend_output.get("evidence", []),
        case_id=case_id,
        run_id=run_id,
        retrieval_query=retrieval_query,
        forbidden_patterns=forbidden_patterns,
    )


def _build_evidence_entries_from_dicts(
    evidence: list[dict[str, Any]],
    *,
    case_id: str,
    run_id: str,
    retrieval_query: str,
    forbidden_patterns: list[str] | None = None,
) -> list[EvidenceEntry]:
    entries: list[EvidenceEntry] = []
    for item in evidence:
        source_ref = str(item.get("file") or item.get("source_ref") or "").strip()
        summary = str(item.get("summary", "")).strip()
        tags = [str(tag) for tag in item.get("tags", [])]
        relevance = _evidence_relevance(
            retrieval_query,
            source_ref=source_ref,
            summary=summary,
            score=_coerce_float(item.get("score")),
        )
        sensitivity = "unknown"
        if summary or source_ref:
            scan_result = scan_public_output(
                {"source_ref": source_ref, "summary": summary},
                forbidden_patterns=forbidden_patterns,
            )
            if scan_result.blocked:
                sensitivity = "secret_risk"
            elif "perm:sensitive" in tags:
                sensitivity = "restricted"
            else:
                sensitivity = "public_safe"
        entries.append(
            EvidenceEntry(
                case_id=case_id,
                run_id=run_id,
                source_ref=source_ref,
                retrieval_query=retrieval_query,
                summary=summary or source_ref,
                sensitivity=sensitivity,
                used_by=["stage_governance"],
                selected=relevance["passed"] and sensitivity == "public_safe",
                metadata={
                    "source": "backend_output",
                    "rank": item.get("rank"),
                    "score": item.get("score"),
                    "tags": tags,
                    "score_breakdown": dict(item.get("score_breakdown", {})),
                    "relevance": relevance,
                },
            )
        )
    return entries


def _verify_backend_output(
    backend_output: dict[str, Any],
    evidence_entries: list[EvidenceEntry],
    safety_result: SafetyScanResult,
) -> dict[str, Any]:
    reasons: list[str] = []
    actions = {str(action).strip() for action in backend_output.get("actions", [])}
    is_handoff = bool(actions & {"create_handoff", "ask_human", "request_human"})
    selected_public_evidence = [
        entry
        for entry in evidence_entries
        if entry.selected and entry.sensitivity == "public_safe"
    ]
    if not backend_output.get("answer") and not is_handoff:
        reasons.append("missing_answer")
    if not evidence_entries and not is_handoff:
        reasons.append("missing_evidence")
    elif evidence_entries and not selected_public_evidence:
        reasons.append("no_relevant_public_evidence")
    if safety_result.blocked:
        reasons.append("safety_blocked")
    return {
        "passed": not reasons,
        "reasons": reasons,
        "relevant_evidence_count": len(selected_public_evidence),
        "evidence_count": len(evidence_entries),
    }


def _verification_passed(
    backend_output: dict[str, Any],
    evidence_entries: list[EvidenceEntry],
    safety_result: SafetyScanResult,
) -> bool:
    return bool(_verify_backend_output(backend_output, evidence_entries, safety_result)["passed"])


def _evidence_relevance(
    query: str,
    *,
    source_ref: str,
    summary: str,
    score: float | None = None,
) -> dict[str, Any]:
    query_terms = _meaningful_terms(query)
    evidence_terms = _meaningful_terms(f"{source_ref} {summary}")
    overlap = sorted(query_terms & evidence_terms)
    query_anchor_terms = _anchor_terms(query_terms)
    matched_anchor_terms = sorted(query_anchor_terms & evidence_terms)
    overlap_ratio = len(overlap) / max(len(query_terms), 1)
    anchor_passed = bool(matched_anchor_terms)
    lexical_passed = (
        (len(overlap) >= 4 and overlap_ratio >= 0.3)
        if query_anchor_terms
        else (len(overlap) >= 2 or overlap_ratio >= 0.18)
    )
    passed = bool(query_terms) and (anchor_passed or lexical_passed)
    return {
        "passed": passed,
        "reason": "matched_query_terms" if passed else "no_query_evidence_overlap",
        "overlap_terms": overlap[:12],
        "matched_anchor_terms": matched_anchor_terms[:8],
        "overlap_ratio": round(overlap_ratio, 3),
        "score": score,
    }


def _meaningful_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for match in _TOKEN.finditer(text.lower()):
        raw = match.group(0).strip()
        if not raw or raw in _STOPWORDS:
            continue
        if _is_cjk(raw):
            chars = [ch for ch in raw if "\u4e00" <= ch <= "\u9fff"]
            if len(chars) >= 3:
                terms.add("".join(chars))
            terms.update(ch for ch in chars if ch not in _STOPWORDS)
            terms.update(
                pair
                for pair in (left + right for left, right in zip(chars, chars[1:], strict=False))
                if pair not in _STOPWORDS
            )
        elif len(raw) > 1:
            terms.add(raw)
    return terms


def _anchor_terms(terms: set[str]) -> set[str]:
    return {
        term
        for term in terms
        if (
            any(ch.isdigit() for ch in term)
            or any("a" <= ch <= "z" for ch in term)
            or len(term) >= 3
        )
    }


def _is_cjk(token: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in token)


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _govern_case_result(
    backend_output: dict[str, Any],
    *,
    intent_frame: IntentFrame,
    evidence_entries: list[EvidenceEntry],
    safety_result: SafetyScanResult,
    permission_result: PermissionCheckResult,
    closure_decision: ClosureDecision,
    verifier_passed: bool,
) -> dict[str, Any]:
    result = json.loads(json.dumps(backend_output, ensure_ascii=False))
    result["confidence"] = max(
        float(result.get("confidence", 0.0) or 0.0), intent_frame.confidence
    )
    result["evidence"] = build_public_evidence_summary(evidence_entries) or result.get(
        "evidence", []
    )

    human_questions = list(result.get("human_questions", []))
    if not closure_decision.closed and not human_questions:
        human_questions = _permission_questions(permission_result) or _human_questions_for_frame(
            intent_frame
        )
    result["human_questions"] = human_questions

    if closure_decision.closed:
        result["closed"] = True
        result["failure_mode"] = None
    else:
        result["closed"] = False
        result["failure_mode"] = _choose_failure_mode(
            intent_frame=intent_frame,
            backend_output=backend_output,
            safety_result=safety_result,
            permission_result=permission_result,
            verifier_passed=verifier_passed,
            closure_decision=closure_decision,
        )

    result["hard_gates"] = {
        "no_misuse": not safety_result.blocked and not permission_result.blocked,
        "permission_passed": permission_result.passed,
        "verification_passed": verifier_passed,
        "closure_gate_passed": closure_decision.closed == result["closed"],
    }
    return result


def _choose_failure_mode(
    *,
    intent_frame: IntentFrame,
    backend_output: dict[str, Any],
    safety_result: SafetyScanResult,
    permission_result: PermissionCheckResult,
    verifier_passed: bool,
    closure_decision: ClosureDecision,
) -> str:
    backend_failure_mode = backend_output.get("failure_mode")
    if safety_result.blocked:
        return "safety_policy_failure"
    if permission_result.blocked or permission_result.needs_human:
        return "permission_policy_failure"
    if not verifier_passed:
        return "verification_failure"
    if backend_failure_mode in _VALID_FAILURE_MODES:
        return str(backend_failure_mode)
    if closure_decision.needs_human:
        return _human_failure_mode(intent_frame)
    return "need_human_judgment"


def _human_failure_mode(frame: IntentFrame) -> str:
    if frame.business_process in {
        "identity_and_access_support",
        "developer_platform_access_approval",
        "collaboration_platform_change_approval",
        "shared_facility_support",
        "internal_network_access_support",
        "developer_service_defect_triage",
        "equipment_use_approval",
    }:
        return "missing_required_information"
    return "need_human_judgment"


def _human_questions_for_frame(frame: IntentFrame) -> list[str]:
    questions = list(_HUMAN_QUESTION_TEMPLATES.get(frame.business_process, []))
    if questions:
        return questions
    if frame.ambiguity_notes:
        return [f"What missing detail should resolve: {frame.ambiguity_notes[0]}?"]
    return ["What additional information is needed to close this case?"]


def _permission_questions(permission_result: PermissionCheckResult) -> list[str]:
    required_fields: list[str] = []
    for decision in permission_result.decisions:
        if decision.decision == "allow":
            continue
        for field_name in decision.required_fields:
            if field_name not in required_fields:
                required_fields.append(field_name)
    if not required_fields:
        return []
    return [
        "执行前请补充人工审批证据："
        + ", ".join(required_fields)
        + "。"
    ]


def _append_governance_events(
    audit_store: AuditStore,
    *,
    case_id: str,
    run_id: str,
    intent_frame: IntentFrame,
    domain_profile: GovernanceDomainProfile,
    governance_frame: IntentFrame,
    route_plan: GovernancePlan,
    permission_preflight_result: PermissionCheckResult,
    permission_postcheck_result: PermissionCheckResult,
    action_plan: ActionPlan,
    action_result: ActionResult,
    evidence_entries: list[EvidenceEntry],
    claim_trace: list[ClaimTraceEntry],
    safety_result: SafetyScanResult,
    closure_decision: ClosureDecision,
    governed_case_result: dict[str, Any],
    verification: dict[str, Any],
) -> list[str]:
    event_types: list[str] = []
    audit_store.append(
        CaseAuditEvent(
            case_id=case_id,
            run_id=run_id,
            event_type="intent_classified",
            payload=intent_frame.to_dict(),
        )
    )
    event_types.append("intent_classified")
    audit_store.append(
        CaseAuditEvent(
            case_id=case_id,
            run_id=run_id,
            event_type="domain_resolved",
            payload=domain_profile.to_dict(),
        )
    )
    event_types.append("domain_resolved")
    audit_store.append(
        CaseAuditEvent(
            case_id=case_id,
            run_id=run_id,
            event_type="governance_frame_built",
            payload=governance_frame.to_dict(),
        )
    )
    event_types.append("governance_frame_built")
    audit_store.append(
        CaseAuditEvent(
            case_id=case_id,
            run_id=run_id,
            event_type="backend_planned",
            payload=route_plan.to_dict(),
        )
    )
    event_types.append("backend_planned")
    audit_store.append(
        CaseAuditEvent(
            case_id=case_id,
            run_id=run_id,
            event_type="action_planned",
            payload=action_plan.model_dump(),
        )
    )
    event_types.append("action_planned")
    audit_store.append(
        CaseAuditEvent(
            case_id=case_id,
            run_id=run_id,
            event_type="permission_preflight_checked",
            payload=permission_preflight_result.to_dict(),
        )
    )
    event_types.append("permission_preflight_checked")
    audit_store.append(
        CaseAuditEvent(
            case_id=case_id,
            run_id=run_id,
            event_type="permission_postcheck_checked",
            payload=permission_postcheck_result.to_dict(),
        )
    )
    event_types.append("permission_postcheck_checked")
    audit_store.append(
        CaseAuditEvent(
            case_id=case_id,
            run_id=run_id,
            event_type="action_result_recorded",
            payload=action_result.model_dump(),
        )
    )
    event_types.append("action_result_recorded")
    for entry in evidence_entries:
        audit_store.append(
            CaseAuditEvent(
                case_id=case_id,
                run_id=run_id,
                event_type="evidence_added",
                payload={
                    "source_ref": entry.source_ref,
                    "sensitivity": entry.sensitivity,
                    "selected": entry.selected,
                    "relevance": dict(entry.metadata.get("relevance", {})),
                },
            )
        )
    if evidence_entries:
        event_types.append("evidence_added")
    audit_store.append(
        CaseAuditEvent(
            case_id=case_id,
            run_id=run_id,
            event_type="claim_trace_built",
            payload={
                "claim_count": len(claim_trace),
                "unsupported_claims": [
                    entry.to_dict()
                    for entry in claim_trace
                    if entry.status == "unsupported"
                ],
            },
        )
    )
    event_types.append("claim_trace_built")
    audit_store.append(
        CaseAuditEvent(
            case_id=case_id,
            run_id=run_id,
            event_type="safety_checked",
            payload={
                "blocked": safety_result.blocked,
                "findings": [finding.model_dump() for finding in safety_result.findings],
            },
        )
    )
    event_types.append("safety_checked")
    audit_store.append(
        CaseAuditEvent(
            case_id=case_id,
            run_id=run_id,
            event_type="verification_checked",
            payload=verification,
        )
    )
    event_types.append("verification_checked")
    audit_store.append(
        CaseAuditEvent(
            case_id=case_id,
            run_id=run_id,
            event_type="closure_checked",
            payload={
                "closed": closure_decision.closed,
                "reason": closure_decision.reason,
                "reasons": list(closure_decision.reasons),
                "needs_human": closure_decision.needs_human,
            },
        )
    )
    event_types.append("closure_checked")
    event_type = "case_closed" if governed_case_result.get("closed") else "case_blocked"
    audit_store.append(
        CaseAuditEvent(
            case_id=case_id,
            run_id=run_id,
            event_type=event_type,
            payload={
                "failure_mode": governed_case_result.get("failure_mode"),
                "human_questions": list(governed_case_result.get("human_questions", [])),
            },
        )
    )
    event_types.append(event_type)
    if governed_case_result.get("human_questions"):
        audit_store.append(
            CaseAuditEvent(
                case_id=case_id,
                run_id=run_id,
                event_type="human_handoff_created",
                payload={"questions": list(governed_case_result.get("human_questions", []))},
            )
        )
        event_types.append("human_handoff_created")
    return event_types

"""Stage governance primitives."""

from openagents_orchestration.control.audit import AuditStore, replay_case_state
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
from openagents_orchestration.control.feedback import (
    CaseFeedbackRecord,
    build_regression_case,
    suggest_governance_patch,
    write_feedback_artifacts,
)
from openagents_orchestration.control.models import (
    ActionPlan,
    ActionResult,
    CaseAuditEvent,
    CaseRecord,
    CaseRunRecord,
    EvidenceEntry,
    HumanHandoffRecord,
    SafetyFinding,
    ToolInvocationRecord,
    VerificationFinding,
)
from openagents_orchestration.control.permissions import (
    PermissionCheckResult,
    PermissionDecision,
    PermissionEngine,
    PermissionPolicy,
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

__all__ = [
    "AuditStore",
    "ActionPlan",
    "ActionResult",
    "CaseAuditEvent",
    "CaseFeedbackRecord",
    "CaseRecord",
    "CaseRunRecord",
    "ClaimTraceEntry",
    "ClosureDecision",
    "GovernanceDomainProfile",
    "GovernanceDomainResolver",
    "GovernancePlan",
    "GovernanceRouter",
    "EvidenceEntry",
    "HumanHandoffRecord",
    "PermissionCheckResult",
    "PermissionDecision",
    "PermissionEngine",
    "PermissionPolicy",
    "SafetyFinding",
    "SafetyScanResult",
    "build_public_evidence_summary",
    "build_regression_case",
    "build_source_to_claim_trace",
    "apply_domain_profile",
    "evaluate_closure",
    "evidence_entries_from_rag_log",
    "replay_case_state",
    "scan_public_output",
    "suggest_governance_patch",
    "traceability_gate_passed",
    "ToolInvocationRecord",
    "VerificationFinding",
    "write_feedback_artifacts",
]


def __getattr__(name: str):
    if name == "ClaudeCodeAdapter":
        from openagents_orchestration.control.claude_code import ClaudeCodeAdapter

        return ClaudeCodeAdapter
    if name == "ReplayCaseBackend":
        from openagents_orchestration.control.pipeline import ReplayCaseBackend

        return ReplayCaseBackend
    if name == "ClaudeCodeReplayBackend":
        from openagents_orchestration.control.pipeline import ClaudeCodeReplayBackend

        return ClaudeCodeReplayBackend
    if name == "StageGovernancePipeline":
        from openagents_orchestration.control.pipeline import StageGovernancePipeline

        return StageGovernancePipeline
    if name == "StageGovernancePipelineResult":
        from openagents_orchestration.control.pipeline import (
            StageGovernancePipelineResult,
        )

        return StageGovernancePipelineResult
    raise AttributeError(name)

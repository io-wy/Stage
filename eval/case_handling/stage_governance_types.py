"""Typed containers for the Stage governance hard-v2 benchmark."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class HardV2EvalSpec:
    eval_id: int
    eval_name: str
    family: str
    business_process: str
    expected_closed: bool
    prompt: str
    assertions: list[dict[str, Any]] = field(default_factory=list)
    files: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class StageGovernanceEvalResult:
    eval_id: int
    eval_name: str
    expected_family: str
    expected_closed: bool
    expected_business_process: str
    baseline_case_result_path: str
    case_result_path: str
    governance_path: str
    audit_path: str
    intent_frame: dict[str, Any]
    domain_profile: dict[str, Any]
    route_plan: dict[str, Any]
    governance: dict[str, Any]
    governed_case_result: dict[str, Any]
    grade: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "eval_id": self.eval_id,
            "eval_name": self.eval_name,
            "expected_family": self.expected_family,
            "expected_closed": self.expected_closed,
            "expected_business_process": self.expected_business_process,
            "baseline_case_result_path": self.baseline_case_result_path,
            "case_result_path": self.case_result_path,
            "governance_path": self.governance_path,
            "audit_path": self.audit_path,
            "intent_frame": self.intent_frame,
            "domain_profile": self.domain_profile,
            "route_plan": self.route_plan,
            "governance": self.governance,
            "governed_case_result": self.governed_case_result,
            "grade": self.grade,
        }


@dataclass(slots=True)
class StageGovernanceBenchmarkSummary:
    total_evals: int
    total_passed: int
    total_assertions: int
    overall_pass_rate: float
    closed_correct: int
    family_correct: int
    secret_safe: int
    intent_correct: int
    domain_correct: int
    route_correct: int
    safety_correct: int
    closure_correct: int
    evidence_correct: int
    traceability_correct: int
    human_handoff_correct: int
    audit_complete: int
    label_leakage_safe: int
    evals: list[StageGovernanceEvalResult] = field(default_factory=list)
    output_root: str = ""
    report_path: str | None = None
    summary_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_evals": self.total_evals,
            "total_passed": self.total_passed,
            "total_assertions": self.total_assertions,
            "overall_pass_rate": self.overall_pass_rate,
            "closed_correct": self.closed_correct,
            "family_correct": self.family_correct,
            "secret_safe": self.secret_safe,
            "intent_correct": self.intent_correct,
            "domain_correct": self.domain_correct,
            "route_correct": self.route_correct,
            "safety_correct": self.safety_correct,
            "closure_correct": self.closure_correct,
            "evidence_correct": self.evidence_correct,
            "traceability_correct": self.traceability_correct,
            "human_handoff_correct": self.human_handoff_correct,
            "audit_complete": self.audit_complete,
            "label_leakage_safe": self.label_leakage_safe,
            "output_root": self.output_root,
            "report_path": self.report_path,
            "summary_path": self.summary_path,
            "evals": [item.to_dict() for item in self.evals],
        }

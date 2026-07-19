"""Feedback capture and governance patch generation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class CaseFeedbackRecord:
    case_id: str
    labels: list[str] = field(default_factory=list)
    prompt: str = ""
    governance: dict[str, Any] = field(default_factory=dict)
    governed_case_result: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    @classmethod
    def from_governance(
        cls,
        *,
        case_id: str,
        prompt: str,
        governance: dict[str, Any],
        governed_case_result: dict[str, Any],
        labels: list[str],
        note: str = "",
    ) -> CaseFeedbackRecord:
        return cls(
            case_id=case_id,
            labels=[label.strip() for label in labels if label.strip()],
            prompt=prompt.strip(),
            governance=json.loads(json.dumps(governance, ensure_ascii=False)),
            governed_case_result=json.loads(
                json.dumps(governed_case_result, ensure_ascii=False)
            ),
            note=note.strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "labels": list(self.labels),
            "prompt": self.prompt,
            "governance": self.governance,
            "governed_case_result": self.governed_case_result,
            "note": self.note,
        }


def suggest_governance_patch(record: CaseFeedbackRecord) -> dict[str, Any]:
    domain = record.governance.get("domain", {})
    route = record.governance.get("route", {})
    prompt_keywords = _prompt_keywords(record.prompt)
    profile = {
        "workflow_type": domain.get("workflow_type", "service_case"),
        "business_process": _business_process_override(
            record.labels, domain.get("business_process", "unknown")
        ),
        "risk_class": _risk_class_override(record.labels, domain.get("risk_class", "normal")),
        "backend_plan": _backend_plan_override(record.labels, route.get("backends", [])),
        "evidence_requirements": _evidence_requirements_override(
            record.labels, domain.get("evidence_requirements", [])
        ),
        "closure_policy": _closure_policy_override(
            record.labels, domain.get("closure_policy", "verify_before_close")
        ),
        "human_handoff_policy": _human_handoff_policy_override(
            record.labels, domain.get("human_handoff_policy", "ask_if_needed")
        ),
        "verifier_profile": domain.get("verifier_profile", "service_desk"),
        "permission_required_fields": _permission_fields_override(
            record.labels, domain.get("permission_required_fields", [])
        ),
        "permission_action_markers": list(domain.get("permission_action_markers", [])),
        "safety_forbidden_patterns": _safety_patterns_override(
            record.labels, domain.get("safety_forbidden_patterns", [])
        ),
    }
    return {
        "pack_id": domain.get("pack_id", "feedback_generated"),
        "version": "feedback-proposal",
        "rules": [
            {
                "id": f"feedback_{record.case_id}",
                "keywords": prompt_keywords,
                "profile": profile,
                "source_case_id": record.case_id,
                "labels": list(record.labels),
            }
        ],
    }


def build_regression_case(record: CaseFeedbackRecord) -> dict[str, Any]:
    domain = record.governance.get("domain", {})
    route = record.governance.get("route", {})
    expected_closed = _expected_closed(record.labels, record.governed_case_result)
    assertions = _regression_assertions(record.labels, record.governed_case_result)
    return {
        "id": f"regression-{record.case_id}",
        "name": f"feedback-{record.case_id}",
        "family": record.governed_case_result.get("family", "ticket"),
        "business_process": domain.get("business_process", "unknown"),
        "expected_closed": expected_closed,
        "prompt": record.prompt,
        "files": [],
        "labels": list(record.labels),
        "expected_output": record.note or "Derived from on-call feedback.",
        "targeted_failure_modes": _targeted_failure_modes(record.labels),
        "route_backends": list(route.get("backends", [])),
        "assertions": assertions,
    }


def write_feedback_artifacts(
    record: CaseFeedbackRecord,
    *,
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir) / record.case_id
    root.mkdir(parents=True, exist_ok=True)
    feedback_path = root / "feedback.json"
    patch_path = root / "governance_patch.yaml"
    regression_path = root / "regression_case.json"
    markdown_path = root / "feedback.md"

    patch = suggest_governance_patch(record)
    regression_case = build_regression_case(record)

    feedback_path.write_text(
        json.dumps(record.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    patch_path.write_text(
        yaml.safe_dump(patch, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    regression_path.write_text(
        json.dumps(regression_case, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    markdown_path.write_text(_render_markdown(record, patch, regression_case), encoding="utf-8")

    return {
        "feedback_json": str(feedback_path),
        "governance_patch": str(patch_path),
        "regression_case": str(regression_path),
        "markdown": str(markdown_path),
    }


def _prompt_keywords(prompt: str) -> list[str]:
    stopwords = {
        "and",
        "any",
        "ask",
        "asks",
        "can",
        "for",
        "need",
        "needs",
        "the",
        "they",
        "this",
        "with",
    }
    words = [chunk.strip("，。！？:;,.()[]{}<>\"'") for chunk in prompt.split()]
    keywords: list[str] = []
    for word in words:
        if len(word) < 3 or word.lower() in stopwords:
            continue
        if word not in keywords:
            keywords.append(word)
    return keywords[:5]


def _business_process_override(labels: list[str], default: str) -> str:
    for label in labels:
        if label.startswith("business_process:"):
            return label.split(":", 1)[1].strip() or default
    return default


def _risk_class_override(labels: list[str], default: str) -> str:
    if "risk:privileged_action" in labels or "wrong_risk" in labels:
        return "privileged_action"
    if "risk:sensitive" in labels:
        return "sensitive"
    return default


def _backend_plan_override(labels: list[str], default: list[str]) -> list[str]:
    plan = list(default)
    if "should_not_use_claude_code" in labels:
        plan = [item for item in plan if item != "claude_code"]
    if "should_call_human" in labels and "human_channel" not in plan:
        plan.append("human_channel")
    if "should_use_rag" in labels and "rag_retrieval" not in plan:
        plan.insert(0, "rag_retrieval")
    return list(dict.fromkeys(plan)) or ["rag_retrieval"]


def _evidence_requirements_override(labels: list[str], default: list[str]) -> list[str]:
    requirements = list(default)
    if "should_cite_doc" in labels and "source" not in requirements:
        requirements.insert(0, "source")
    if "should_cite_doc" in labels and "evidence" not in requirements:
        requirements.append("evidence")
    return list(dict.fromkeys(requirements))


def _closure_policy_override(labels: list[str], default: str) -> str:
    if {"wrong_closure", "should_not_close", "should_human_handoff"} & set(labels):
        return "verify_before_close"
    return default


def _human_handoff_policy_override(labels: list[str], default: str) -> str:
    if "should_human_handoff" in labels or "should_call_human" in labels:
        return "ask_if_needed"
    return default


def _permission_fields_override(labels: list[str], default: list[str]) -> list[str]:
    fields = list(default)
    if {"should_require_approval", "should_human_handoff", "wrong_closure"} & set(labels):
        for field in ["approver", "target", "scope", "ticket_id"]:
            if field not in fields:
                fields.append(field)
    if "should_require_owner" in labels and "owner" not in fields:
        fields.append("owner")
    return fields


def _safety_patterns_override(labels: list[str], default: list[str]) -> list[str]:
    patterns = list(default)
    for label in labels:
        if label.startswith(("forbid:", "redact:")):
            pattern = label.split(":", 1)[1].strip()
            if pattern and pattern not in patterns:
                patterns.append(pattern)
    return patterns


def _expected_closed(labels: list[str], governed_case_result: dict[str, Any]) -> bool:
    if {"wrong_closure", "should_not_close", "should_human_handoff"} & set(labels):
        return False
    return bool(governed_case_result.get("closed"))


def _regression_assertions(
    labels: list[str],
    governed_case_result: dict[str, Any],
) -> list[dict[str, Any]]:
    assertions: list[dict[str, Any]] = []
    expected_closed = _expected_closed(labels, governed_case_result)
    assertions.append(
        {
            "name": "case_closed" if expected_closed else "case_not_closed",
            "description": "derived from feedback labels",
        }
    )
    if "should_human_handoff" in labels:
        assertions.append(
            {
                "name": "asks_concrete_human_question",
                "description": "case should ask for human follow-up instead of closing",
            }
        )
    if "should_cite_doc" in labels:
        assertions.append(
            {
                "name": "cites_relevant_sources",
                "description": "case should cite the governing documents",
            }
        )
    if "should_not_use_claude_code" in labels:
        assertions.append(
            {
                "name": "does_not_invoke_claude_code",
                "description": "case should not route to Claude Code for this request",
            }
        )
    if "should_redact_secret" in labels:
        assertions.append(
            {
                "name": "whole_output_secret_safe",
                "description": "case should not expose secret-bearing content",
            }
        )
    return assertions


def _targeted_failure_modes(labels: list[str]) -> list[str]:
    failures: list[str] = []
    if "wrong_closure" in labels:
        failures.append("verification_failure")
    if "should_human_handoff" in labels:
        failures.append("human_handoff_failure")
    if "should_not_use_claude_code" in labels:
        failures.append("planning_failure")
    if "should_cite_doc" in labels:
        failures.append("retrieval_failure")
    return failures


def _render_markdown(
    record: CaseFeedbackRecord,
    patch: dict[str, Any],
    regression_case: dict[str, Any],
) -> str:
    return "\n".join(
        [
            f"# Case Feedback {record.case_id}",
            "",
            f"- labels: `{', '.join(record.labels) or 'none'}`",
            f"- note: {record.note or 'n/a'}",
            "",
            "## Governance Patch",
            "",
            "```yaml",
            yaml.safe_dump(patch, sort_keys=False, allow_unicode=True).strip(),
            "```",
            "",
            "## Regression Case",
            "",
            "```json",
            json.dumps(regression_case, ensure_ascii=False, indent=2),
            "```",
        ]
    )

"""Service-domain policy resolution for Stage governance."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from openagents_orchestration.intent_classifier import IntentFrame


@dataclass(frozen=True, slots=True)
class GovernanceDomainProfile:
    pack_id: str = "unknown"
    pack_version: str = "unversioned"
    rule_id: str = "unknown"
    workflow_type: str = "general"
    business_process: str = "unknown"
    risk_class: str = "normal"
    backend_plan: list[str] | None = None
    evidence_requirements: list[str] | None = None
    closure_policy: str = "verify_before_close"
    human_handoff_policy: str = "ask_if_needed"
    verifier_profile: str = "service_desk"
    ambiguity_notes: list[str] | None = None
    permission_required_fields: list[str] | None = None
    permission_action_markers: list[str] | None = None
    safety_forbidden_patterns: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack_id": self.pack_id,
            "pack_version": self.pack_version,
            "rule_id": self.rule_id,
            "workflow_type": self.workflow_type,
            "business_process": self.business_process,
            "risk_class": self.risk_class,
            "backend_plan": list(self.backend_plan or []),
            "evidence_requirements": list(self.evidence_requirements or []),
            "closure_policy": self.closure_policy,
            "human_handoff_policy": self.human_handoff_policy,
            "verifier_profile": self.verifier_profile,
            "ambiguity_notes": list(self.ambiguity_notes or []),
            "permission_required_fields": list(self.permission_required_fields or []),
            "permission_action_markers": list(self.permission_action_markers or []),
            "safety_forbidden_patterns": list(self.safety_forbidden_patterns or []),
        }


@dataclass(frozen=True, slots=True)
class GovernanceDomainRule:
    rule_id: str
    keywords: list[str]
    profile: GovernanceDomainProfile


class GovernanceDomainResolver:
    """Map a prompt to domain-specific governance policy."""

    def __init__(self, domain_pack_paths: list[str | Path] | None = None):
        paths = (
            [Path(path) for path in domain_pack_paths]
            if domain_pack_paths is not None
            else _default_domain_pack_paths()
        )
        self._rules = _load_domain_rules(paths)

    def resolve(self, objective: str) -> GovernanceDomainProfile:
        text = objective.lower()
        best_profile = GovernanceDomainProfile()
        best_score = 0
        for rule in self._rules:
            score = _service_keyword_score(text, rule.keywords)
            if score > best_score:
                best_score = score
                best_profile = rule.profile
        return best_profile


def apply_domain_profile(
    frame: IntentFrame,
    profile: GovernanceDomainProfile,
) -> IntentFrame:
    """Return an intent frame enriched with domain policy when one exists."""

    if profile.business_process == "unknown":
        return frame
    return replace(
        frame,
        workflow_type=profile.workflow_type,
        business_process=profile.business_process,
        risk_class=_max_risk(frame.risk_class, profile.risk_class),
        backend_plan=list(profile.backend_plan or frame.backend_plan),
        evidence_requirements=list(
            profile.evidence_requirements or frame.evidence_requirements
        ),
        closure_policy=profile.closure_policy,
        human_handoff_policy=profile.human_handoff_policy,
        verifier_profile=profile.verifier_profile,
        ambiguity_notes=list(profile.ambiguity_notes or frame.ambiguity_notes),
    )


def _max_risk(left: str, right: str) -> str:
    order = {
        "normal": 0,
        "sensitive": 1,
        "privileged_action": 2,
    }
    return left if order.get(left, 0) >= order.get(right, 0) else right


def _matches_service_keywords(text: str, keywords: list[str]) -> bool:
    return _service_keyword_score(text, keywords) > 0


def _service_keyword_score(text: str, keywords: list[str]) -> int:
    score = 0
    for keyword in keywords:
        normalized = keyword.lower()
        if not normalized:
            continue
        if " " in normalized or any(not char.isascii() for char in normalized):
            if normalized in text:
                score += 1
            continue
        pattern = rf"\b{re.escape(normalized)}\b"
        if re.search(pattern, text):
            score += 1
    return score


def _default_domain_pack_paths() -> list[Path]:
    repo_root = Path(__file__).resolve().parents[3]
    path = repo_root / "configs" / "governance" / "domain_packs" / "sast_service_desk.yaml"
    return [path] if path.exists() else []


def _load_domain_rules(paths: list[Path]) -> list[GovernanceDomainRule]:
    rules: list[GovernanceDomainRule] = []
    for path in paths:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        pack_id = str(raw.get("pack_id") or path.stem)
        pack_version = str(raw.get("version") or raw.get("pack_version") or "unversioned")
        for item in raw.get("rules", []):
            rule_id = str(item.get("id", ""))
            profile = _profile_from_mapping(item.get("profile", {}))
            rules.append(
                GovernanceDomainRule(
                    rule_id=rule_id,
                    keywords=[str(keyword) for keyword in item.get("keywords", [])],
                    profile=replace(
                        profile,
                        pack_id=pack_id,
                        pack_version=pack_version,
                        rule_id=rule_id,
                    ),
                )
            )
    return rules


def _profile_from_mapping(raw: dict[str, Any]) -> GovernanceDomainProfile:
    return GovernanceDomainProfile(
        workflow_type=str(raw.get("workflow_type", "general")),
        business_process=str(raw.get("business_process", "unknown")),
        risk_class=str(raw.get("risk_class", "normal")),
        backend_plan=[str(item) for item in raw.get("backend_plan", [])],
        evidence_requirements=[
            str(item) for item in raw.get("evidence_requirements", [])
        ],
        closure_policy=str(raw.get("closure_policy", "verify_before_close")),
        human_handoff_policy=str(raw.get("human_handoff_policy", "ask_if_needed")),
        verifier_profile=str(raw.get("verifier_profile", "service_desk")),
        ambiguity_notes=[str(item) for item in raw.get("ambiguity_notes", [])],
        permission_required_fields=[
            str(item) for item in raw.get("permission_required_fields", [])
        ],
        permission_action_markers=[
            str(item) for item in raw.get("permission_action_markers", [])
        ],
        safety_forbidden_patterns=[
            str(item) for item in raw.get("safety_forbidden_patterns", [])
        ],
    )

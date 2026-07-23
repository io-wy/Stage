"""Intent classification — pre-decomposition task routing.

Multi-layer funnel (L0-L4):
- L0 cache: exact match in session cache
- L1 rules: keyword/regex matching
- L2 history: vector similarity (placeholder)
- L3 LLM: structured generation via Pydantic schema
- L4 feedback: user/director override
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from openagents_orchestration.utils.structured_generate import structured_generate

_FALLBACK_REASON = "规则未命中，已使用确定性兜底"
_LLM_ERROR_FALLBACK_REASON = "语义模型暂不可用，已使用确定性兜底"


class IntentSchema(BaseModel):
    """Structured output for intent classification."""

    task_type: str = Field(
        description=(
            "Task type:\n"
            "- code_completion: given a function signature, implement the body\n"
            "- bug_fix: find and fix an existing bug\n"
            "- feature: add new functionality\n"
            "- refactor: restructure code without changing behavior\n"
            "- review: review existing code\n"
            "- test: write or run tests\n"
            "- release: publish, deploy, create PR/merge\n"
            "- doc: write documentation\n"
            "- shell: run shell commands or system tasks\n"
            "- unknown: cannot determine"
        )
    )
    complexity: str = Field(
        description="Complexity: simple, medium, complex"
    )
    external: list[str] = Field(
        default_factory=list,
        description="External integrations needed: github, lark, jira, docker, etc."
    )
    priority: str = Field(
        default="normal",
        description="Priority: urgent, normal, low"
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence in this classification (0-1)"
    )
    reason: str = Field(
        description="One-sentence explanation for the classification"
    )


@dataclass
class IntentResult:
    """Intent classification result."""

    task_type: str
    complexity: str
    external: list[str] = field(default_factory=list)
    priority: str = "normal"
    confidence: float = 0.0
    reason: str = ""
    source: str = "llm"  # L0_cache, L1_rule, L2_history, L3_llm, L4_feedback

    @classmethod
    def from_schema(cls, schema: IntentSchema, source: str = "L3_llm") -> IntentResult:
        return cls(
            task_type=schema.task_type,
            complexity=schema.complexity,
            external=schema.external,
            priority=schema.priority,
            confidence=schema.confidence,
            reason=schema.reason,
            source=source,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "complexity": self.complexity,
            "external": self.external,
            "priority": self.priority,
            "confidence": self.confidence,
            "reason": self.reason,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class IntentFrame:
    """Governance intent frame used by Stage."""

    task_type: str
    complexity: str
    external: list[str] = field(default_factory=list)
    priority: str = "normal"
    confidence: float = 0.0
    reason: str = ""
    source: str = "llm"
    workflow_type: str = "general"
    business_process: str = "unknown"
    risk_class: str = "normal"
    backend_plan: list[str] = field(default_factory=list)
    evidence_requirements: list[str] = field(default_factory=list)
    closure_policy: str = "standard"
    human_handoff_policy: str = "ask_if_needed"
    verifier_profile: str = "standard"
    ambiguity_notes: list[str] = field(default_factory=list)
    execution_adapter: str = ""
    adapter_tools: list[str] = field(default_factory=list)
    permission_required_fields: list[str] = field(default_factory=list)

    @classmethod
    def from_intent_result(
        cls,
        result: IntentResult,
        *,
        objective: str = "",
    ) -> IntentFrame:
        """Upgrade a task intent into a governance frame."""
        objective = objective.strip()
        workflow_type = _infer_workflow_type(result.task_type)
        business_process = _infer_business_process(result.task_type)
        risk_class = _infer_risk_class(result.task_type, objective)
        backend_plan = _infer_backend_plan(result.task_type, result.complexity)
        evidence_requirements = _default_evidence_requirements(workflow_type)
        closure_policy = _infer_closure_policy(workflow_type)
        human_handoff_policy = _infer_human_handoff_policy(
            objective, result.confidence, risk_class
        )
        verifier_profile = _infer_verifier_profile(workflow_type)
        ambiguity_notes = _infer_ambiguity_notes(objective, result.confidence)

        return cls(
            task_type=result.task_type,
            complexity=result.complexity,
            external=list(result.external),
            priority=result.priority,
            confidence=result.confidence,
            reason=result.reason,
            source=result.source,
            workflow_type=workflow_type,
            business_process=business_process,
            risk_class=risk_class,
            backend_plan=backend_plan,
            evidence_requirements=evidence_requirements,
            closure_policy=closure_policy,
            human_handoff_policy=human_handoff_policy,
            verifier_profile=verifier_profile,
            ambiguity_notes=ambiguity_notes,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "complexity": self.complexity,
            "external": list(self.external),
            "priority": self.priority,
            "confidence": self.confidence,
            "reason": self.reason,
            "source": self.source,
            "workflow_type": self.workflow_type,
            "business_process": self.business_process,
            "risk_class": self.risk_class,
            "backend_plan": list(self.backend_plan),
            "evidence_requirements": list(self.evidence_requirements),
            "closure_policy": self.closure_policy,
            "human_handoff_policy": self.human_handoff_policy,
            "verifier_profile": self.verifier_profile,
            "ambiguity_notes": list(self.ambiguity_notes),
            "execution_adapter": self.execution_adapter,
            "adapter_tools": list(self.adapter_tools),
            "permission_required_fields": list(self.permission_required_fields),
        }


class IntentClassifier:
    """Multi-layer intent classification funnel."""

    def __init__(self, llm_client: Any | None = None, rules_path: Path | None = None):
        self._llm = llm_client
        self._cache: dict[str, IntentResult] = {}
        self._rules_path = rules_path
        self._rules: list[dict] = self._load_rules()

    def _load_rules(self) -> list[dict]:
        """Load keyword-based classification rules.

        These fast heuristic rules skip the LLM call for common task patterns.
        Rules are matched in order; the first match wins. Confidence is kept
        moderate so the Director can still override when context suggests
        otherwise.
        """
        return [
            {
                "keywords": ["pytest", "unit test", "write tests"],
                "intent": {
                    "task_type": "test",
                    "complexity": "medium",
                    "external": [],
                    "priority": "normal",
                },
                "confidence": 0.85,
            },
            {
                "keywords": ["bug", "fix", "repair", "broken"],
                "intent": {
                    "task_type": "bug_fix",
                    "complexity": "medium",
                    "external": [],
                    "priority": "normal",
                },
                "confidence": 0.85,
            },
            {
                "keywords": ["refactor", "restructure", "clean up"],
                "intent": {
                    "task_type": "refactor",
                    "complexity": "medium",
                    "external": [],
                    "priority": "normal",
                },
                "confidence": 0.8,
            },
            {
                "keywords": ["api endpoint", "endpoint", "server", "backend"],
                "intent": {
                    "task_type": "feature",
                    "complexity": "medium",
                    "external": [],
                    "priority": "normal",
                },
                "confidence": 0.85,
            },
            {
                "keywords": ["function", "implement", "write a function", "solve"],
                "intent": {
                    "task_type": "feature",
                    "complexity": "simple",
                    "external": [],
                    "priority": "normal",
                },
                "confidence": 0.85,
            },
            {
                "keywords": ["review", "code review"],
                "intent": {
                    "task_type": "review",
                    "complexity": "medium",
                    "external": [],
                    "priority": "normal",
                },
                "confidence": 0.85,
            },
            {
                "keywords": ["readme", "documentation", "doc"],
                "intent": {
                    "task_type": "doc",
                    "complexity": "simple",
                    "external": [],
                    "priority": "normal",
                },
                "confidence": 0.8,
            },
        ]

    async def classify(self, objective: str) -> IntentResult:
        """Classify task intent through L0-L3 funnel."""

        # L0: exact cache
        if objective in self._cache:
            return self._cache[objective]

        # L1: keyword rules
        rule_result = self._match_rules(objective)
        if rule_result:
            self._cache[objective] = rule_result
            return rule_result

        # L3: LLM classification
        if self._llm is None:
            return IntentResult(
                task_type="unknown",
                complexity="medium",
                external=[],
                priority="normal",
                confidence=0.0,
                reason=_FALLBACK_REASON,
                source="fallback",
            )

        try:
            schema, _ = await structured_generate(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an intent classifier for a governed service "
                            "execution platform. Classify the user's request by "
                            "task semantics only: knowledge question, approval "
                            "request, privileged write, configuration change, "
                            "incident handling, documentation, code work, or "
                            "unknown. Do not decide whether the action is allowed; "
                            "policy and permissions are handled by Stage governance."
                        ),
                    },
                    {"role": "user", "content": f"Task: {objective}"},
                ],
                response_model=IntentSchema,
                llm_client=self._llm,
            )
        except Exception:
            result = _fallback_intent(
                reason=_LLM_ERROR_FALLBACK_REASON,
                source="intent_llm_error",
            )
            self._cache[objective] = result
            return result

        result = IntentResult.from_schema(schema, source="L3_llm")
        self._cache[objective] = result
        return result

    def feedback(self, objective: str, corrected: IntentResult) -> None:
        """L4 feedback: record user/director correction."""
        self._cache[objective] = corrected

    def classify_frame(self, objective: str) -> IntentFrame:
        """Classify the objective and upgrade it into a governance frame."""
        return IntentFrame.from_intent_result(
            self._ensure_intent(objective),
            objective=objective,
        )

    async def classify_frame_async(self, objective: str) -> IntentFrame:
        """Classify through the full async funnel and build a governance frame."""
        return IntentFrame.from_intent_result(
            await self.classify(objective),
            objective=objective,
        )

    def _ensure_intent(self, objective: str) -> IntentResult:
        if objective in self._cache:
            return self._cache[objective]
        result = self._match_rules(objective)
        if result:
            self._cache[objective] = result
            return result
        if self._llm is None:
            result = _fallback_intent()
            self._cache[objective] = result
            return result
        result = _run_async_from_sync(self.classify(objective))
        self._cache[objective] = result
        return result

    def _match_rules(self, objective: str) -> IntentResult | None:
        """L1: keyword-based rule matching."""
        obj_lower = objective.lower()
        for rule in self._rules:
            if any(_matches_rule_keyword(obj_lower, kw) for kw in rule["keywords"]):
                intent_data = rule["intent"]
                return IntentResult(
                    task_type=intent_data["task_type"],
                    complexity=intent_data["complexity"],
                    external=list(intent_data.get("external", [])),
                    priority=intent_data.get("priority", "normal"),
                    confidence=rule["confidence"],
                    reason=f"Keyword match: {', '.join(rule['keywords'][:3])}",
                    source="L1_rule",
                )
        return None


def _fallback_intent(
    *,
    reason: str = _FALLBACK_REASON,
    source: str = "fallback",
) -> IntentResult:
    return IntentResult(
        task_type="unknown",
        complexity="medium",
        external=[],
        priority="normal",
        confidence=0.0,
        reason=reason,
        source=source,
    )


def _run_async_from_sync(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    close = getattr(coro, "close", None)
    if close is not None:
        close()
    raise RuntimeError(
        "IntentClassifier.classify_frame cannot call an LLM while an event loop "
        "is already running; use classify_frame_async instead."
    )


def _matches_rule_keyword(text: str, keyword: str) -> bool:
    normalized = keyword.lower()
    if " " in normalized or any(not char.isascii() for char in normalized):
        return normalized in text
    return re.search(rf"\b{re.escape(normalized)}\b", text) is not None


def _infer_workflow_type(task_type: str) -> str:
    if task_type in {"code_completion", "bug_fix", "feature", "refactor", "review", "test", "release", "shell"}:
        return "development"
    if task_type == "doc":
        return "documentation"
    return "general"


def _infer_business_process(task_type: str) -> str:
    if task_type in {"code_completion", "bug_fix", "feature", "refactor", "review", "test", "release", "shell"}:
        return "code_task"
    if task_type == "doc":
        return "documentation_work"
    return "unknown"


def _infer_risk_class(task_type: str, objective: str) -> str:
    text = objective.lower()
    privileged_markers = [
        "password",
        "admin",
        "grant access",
        "permission",
        "approval",
        "modify",
    ]
    if any(kw in text for kw in privileged_markers):
        return "privileged_action"
    if task_type in {"shell", "release"}:
        return "sensitive"
    return "normal"


def _infer_backend_plan(task_type: str, complexity: str) -> list[str]:
    if task_type in {"code_completion", "bug_fix", "feature", "refactor", "review", "test", "release", "shell"}:
        return ["claude_code"]
    if complexity == "simple":
        return ["rag_retrieval"]
    return ["rag_retrieval", "claude_code"]


def _default_evidence_requirements(workflow_type: str) -> list[str]:
    if workflow_type == "development":
        return ["source", "diff_or_patch", "verification"]
    if workflow_type == "documentation":
        return ["source", "summary"]
    return ["source"]


def _infer_closure_policy(workflow_type: str) -> str:
    if workflow_type == "development":
        return "verify_before_close"
    if workflow_type == "documentation":
        return "standard"
    return "verify_before_close"


def _infer_human_handoff_policy(
    objective: str,
    confidence: float,
    risk_class: str,
) -> str:
    text = objective.lower()
    if confidence < 0.7 or risk_class in {"sensitive", "privileged_action"}:
        return "ask_if_needed"
    if any(kw in text for kw in ["?", "maybe", "or ", "which", "where", "how"]):
        return "ask_if_needed"
    return "none"


def _infer_verifier_profile(workflow_type: str) -> str:
    if workflow_type == "development":
        return "code_review"
    if workflow_type == "documentation":
        return "doc_review"
    return "service_desk"


def _infer_ambiguity_notes(objective: str, confidence: float) -> list[str]:
    notes: list[str] = []
    text = objective.lower()
    if confidence < 0.7:
        notes.append("low classifier confidence")
    if any(kw in text for kw in ["maybe", "or ", "which", "where", "how"]):
        notes.append("request contains ambiguity markers")
    return notes

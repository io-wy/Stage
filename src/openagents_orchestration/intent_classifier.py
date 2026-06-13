"""Intent classification — pre-decomposition task routing.

Multi-layer funnel (L0-L4):
- L0 cache: exact match in session cache
- L1 rules: keyword/regex matching
- L2 history: vector similarity (placeholder)
- L3 LLM: structured generation via Pydantic schema
- L4 feedback: user/director override
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from openagents_orchestration.utils.structured_generate import structured_generate


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
                "keywords": ["test", "pytest", "unit test", "write tests"],
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
                "keywords": ["api", "service", "endpoint", "server", "backend"],
                "intent": {
                    "task_type": "feature",
                    "complexity": "complex",
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
                reason="No LLM client available",
                source="fallback",
            )

        schema, _ = await structured_generate(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an intent classifier. Analyze the given task and "
                        "classify it into the correct type and complexity."
                    ),
                },
                {"role": "user", "content": f"Task: {objective}"},
            ],
            response_model=IntentSchema,
            llm_client=self._llm,
        )

        result = IntentResult.from_schema(schema, source="L3_llm")
        self._cache[objective] = result
        return result

    def feedback(self, objective: str, corrected: IntentResult) -> None:
        """L4 feedback: record user/director correction."""
        self._cache[objective] = corrected

    def _match_rules(self, objective: str) -> IntentResult | None:
        """L1: keyword-based rule matching."""
        obj_lower = objective.lower()
        for rule in self._rules:
            if any(kw in obj_lower for kw in rule["keywords"]):
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

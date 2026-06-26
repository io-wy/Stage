"""ClassifyIntentTool — Director tool for intent classification.

The Director calls this inside its ReAct loop when it wants to understand
the objective before deciding whether to decompose or act directly.
"""

from __future__ import annotations

from typing import Any

from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin

from openagents_orchestration.intent_classifier import IntentClassifier


class ClassifyIntentTool(ToolPlugin):
    """Classify the objective into task type, complexity, external needs."""

    name = "classify_intent"
    description = (
        "Analyze the given objective and classify it into a task type, "
        "complexity level, external integrations needed, and priority. "
        "Use this when you need to understand the nature of the request "
        "before deciding whether to decompose it into subtasks or handle it directly."
    )

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(
            concurrency_safe=True,
            side_effects="none",
            default_timeout_ms=30_000,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "objective": {
                    "type": "string",
                    "description": "The objective or task to classify.",
                },
            },
            "required": ["objective"],
        }

    async def invoke(
        self,
        params: dict[str, Any],
        context: Any | None,
    ) -> dict[str, Any]:
        objective = str(params.get("objective", "")).strip()
        if not objective:
            return {"error": "objective is required"}

        llm_client = getattr(context, "llm_client", None) if context is not None else None
        if llm_client is None:
            return {
                "error": "No LLM client available",
                "intent": {
                    "task_type": "unknown",
                    "complexity": "medium",
                    "external": [],
                    "priority": "normal",
                    "confidence": 0.0,
                    "reason": "No LLM client available",
                    "source": "fallback",
                },
            }

        classifier = IntentClassifier(llm_client=llm_client)
        try:
            result = await classifier.classify(objective)
            return {"intent": result.to_dict()}
        except Exception as exc:
            return {
                "error": f"Classification failed: {exc}",
                "intent": {
                    "task_type": "unknown",
                    "complexity": "medium",
                    "external": [],
                    "priority": "normal",
                    "confidence": 0.0,
                    "reason": f"LLM error: {exc}",
                    "source": "fallback",
                },
            }

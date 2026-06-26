"""DecomposeTool — Director tool for task decomposition.

The Director calls this inside its ReAct loop when it decides a complex
objective should be broken into a DAG of subtasks.
"""

from __future__ import annotations

from typing import Any

from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin
from pydantic import BaseModel, Field

from openagents_orchestration.intent_classifier import IntentResult
from openagents_orchestration.models.task import TaskGraph, TaskNode
from openagents_orchestration.utils.structured_generate import structured_generate


class _SubtaskSchema(BaseModel):
    task_id: str
    description: str
    agent_type: str = "coder"
    dependencies: list[str] = Field(default_factory=list)
    expected_artifacts: list[str] = Field(default_factory=list)


class _TaskSchema(BaseModel):
    task_id: str
    description: str
    input_context: str = ""
    agent_type: str = "coder"
    dependencies: list[str] = Field(default_factory=list)
    expected_artifacts: list[str] = Field(default_factory=list)
    subtasks: list[_SubtaskSchema] = Field(default_factory=list)


class _GraphSchema(BaseModel):
    tasks: list[_TaskSchema] = Field(default_factory=list)


class DecomposeTool(ToolPlugin):
    """Decompose an objective into a directed acyclic graph of tasks."""

    name = "decompose"
    description = (
        "Break a complex objective into a small DAG of concrete, verifiable tasks. "
        "Each task gets a unique ID, description, agent type, dependencies, and expected artifacts. "
        "Call this after classify_intent when the intent complexity is 'complex' or when the task "
        "clearly needs multiple agents."
    )

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(
            concurrency_safe=True,
            side_effects="state",
            default_timeout_ms=60_000,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "objective": {
                    "type": "string",
                    "description": "The objective to decompose.",
                },
                "intent": {
                    "type": "object",
                    "description": "Optional intent result from classify_intent.",
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

        board = getattr(getattr(context, "deps", None), "state_board", None)
        if board is None:
            return {"error": "StateBoard not available"}

        llm_client = getattr(context, "llm_client", None) if context is not None else None
        if llm_client is None:
            return {"error": "No LLM client available"}

        raw_intent = params.get("intent") or {}
        intent = IntentResult(
            task_type=str(raw_intent.get("task_type", "unknown")),
            complexity=str(raw_intent.get("complexity", "medium")),
            external=list(raw_intent.get("external", [])),
            priority=str(raw_intent.get("priority", "normal")),
            confidence=float(raw_intent.get("confidence", 0.0)),
            reason=str(raw_intent.get("reason", "")),
            source=str(raw_intent.get("source", "tool")),
        )

        agents_info = self._build_agents_info(context)

        system = (
            "You are an expert task decomposer. Given an objective, break it into "
            "a small DAG of concrete, verifiable tasks. Each task must have a unique "
            "task_id, a clear description, an agent_type from the available roster, "
            "and explicit dependencies on earlier task_ids. Keep the graph small enough "
            "to fit in the orchestration budget."
        )
        intent_section = (
            f"\nIntent: {intent.task_type}/{intent.complexity}, "
            f"external={intent.external}, priority={intent.priority}.\n"
            f"Reasoning: {intent.reason}\n"
        )
        prompt = (
            f"{system}\n\n"
            f"Available agent types:\n{agents_info}\n"
            f"{intent_section}\n"
            f"Objective: {objective}\n\n"
            "Return a JSON object with a 'tasks' list."
        )

        try:
            data, _usage = await structured_generate(
                messages=[{"role": "user", "content": prompt}],
                response_model=_GraphSchema,
                llm_client=llm_client,
            )
        except Exception as exc:
            return {"error": f"Decomposition failed: {exc}"}

        tasks: list[TaskNode] = []
        for item in (data.tasks if hasattr(data, "tasks") else []) or []:
            task = TaskNode(
                task_id=str(getattr(item, "task_id", "")),
                description=str(getattr(item, "description", "")),
                agent_type=str(getattr(item, "agent_type", "coder")),
                dependencies=list(getattr(item, "dependencies", [])),
                expected_artifacts=list(getattr(item, "expected_artifacts", [])),
                input_context=str(getattr(item, "input_context", "")),
            )
            for sub in getattr(item, "subtasks", []) or []:
                task.subtasks.append(
                    TaskNode(
                        task_id=str(getattr(sub, "task_id", "")),
                        description=str(getattr(sub, "description", "")),
                        agent_type=str(getattr(sub, "agent_type", "coder")),
                        dependencies=list(getattr(sub, "dependencies", [])),
                        expected_artifacts=list(getattr(sub, "expected_artifacts", [])),
                    )
                )
            tasks.append(task)

        graph = TaskGraph(objective=objective, tasks=tasks)
        try:
            graph.validate()
        except Exception:
            graph = TaskGraph(
                objective=objective,
                tasks=[TaskNode(task_id="t1", description=objective, agent_type="coder")],
            )

        board.add_tasks(graph)
        return {
            "tasks_added": len(graph.tasks),
            "task_ids": [t.task_id for t in graph.tasks],
        }

    @staticmethod
    def _build_agents_info(context: Any | None) -> str:
        """Build a summary of available agent types for decomposition prompts."""
        runner = getattr(getattr(context, "deps", None), "runner", None)
        agents_by_id = getattr(runner, "_agents_by_id", {})
        lines: list[str] = []
        for aid, agent_def in sorted(agents_by_id.items()):
            desc = getattr(agent_def, "description", "")
            lines.append(f"- {aid}: {desc}")
        return "\n".join(lines) or "- coder: general coding agent"

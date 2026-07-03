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
        "Turn an objective into the SMALLEST task graph that works. decompose is how tasks enter "
        "the board, so `spawn_agent` can only run after it. A coder / reviewer / researcher can each "
        "own a complete task end-to-end — most objectives need just ONE task and ONE agent.\n\n"
        "# Emit MULTIPLE tasks ONLY when at least one holds\n"
        "- The work needs several DISTINCT roles (e.g. research -> code -> review) one agent cannot cover.\n"
        "- There are genuinely INDEPENDENT subtasks that can run in parallel.\n"
        "- The objective is too large to fit one agent's context window.\n\n"
        "# Otherwise emit a SINGLE task\n"
        "- When one agent could plausibly complete the objective, return a one-task graph — that is "
        "the correct, common output, not a failure. Do not manufacture extra tasks to look thorough; "
        "over-splitting adds coordination + token cost and enlarges the failure surface.\n\n"
        "Each task gets a unique ID, description, agent_type, dependencies, and expected artifacts."
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

        # Deterministic fast path: simple objectives are owned by a single agent.
        # The LLM planner below is also instructed to prefer single tasks, but this
        # path guarantees it for simple intents and saves a model call.
        if intent.complexity == "simple" and not intent.external:
            graph = TaskGraph(
                objective=objective,
                tasks=[
                    TaskNode(
                        task_id="t1",
                        description=objective,
                        agent_type="coder",
                    )
                ],
            )
            board.add_tasks(graph)
            return {
                "tasks_added": 1,
                "task_ids": ["t1"],
            }

        agents_info = self._build_agents_info(context)

        system = (
            "You are an expert task planner. Your FIRST job is to decide whether the "
            "objective needs splitting at all. A single capable agent (coder, reviewer, "
            "researcher, ...) can own a complete task end-to-end in its own context window. "
            "If one agent could plausibly finish this objective, return a SINGLE task — that "
            "is a valid and preferred output, not a failure.\n\n"
            "Split into multiple tasks ONLY when the objective genuinely requires several "
            "DISTINCT roles, has INDEPENDENT subtasks that can run in parallel, or is too "
            "large for one agent's context window. Prefer the smallest graph that works; do "
            "not split for the sake of splitting — over-decomposition adds coordination and "
            "token cost and enlarges the failure surface.\n\n"
            "Each task must have a unique task_id, a clear description, an agent_type from "
            "the available roster, and explicit dependencies on earlier task_ids. Keep the "
            "graph small enough to fit in the orchestration budget."
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

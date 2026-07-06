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
        "把一个目标转成**最小的**任务图。decompose 是任务进入 board 的唯一入口，spawn_agent 只能在其后运行。"
        "一个 coder / reviewer / researcher 可以独立完成一个完整任务——大多数目标只需要**一个**任务和**一个** agent。\n\n"
        "# 仅当以下至少一项成立时才产生**多个**任务\n"
        "- 工作需要多个不同的角色（如 research → code → review），一个 agent 无法覆盖\n"
        "- 有真正独立的子任务可以并行运行\n"
        "- 目标太大，一个 agent 的上下文窗口装不下\n\n"
        "# 否则只产生**一个**任务\n"
        "- 当一个 agent 可以完成目标时，返回单任务图——这是正确的常见输出，不是失败。"
        "不要为了显得全面而编造多余的任务；过度拆分增加协调成本和 token 消耗，并扩大失败面。\n\n"
        "每个任务有唯一 ID、描述、agent_type、依赖和期望产物。"
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
            "你是专家级任务规划者。你的第一任务是判断这个目标是否需要拆分。"
            "一个能干的 agent（coder、reviewer、researcher...）可以在自己的上下文窗口中端到端完成一个完整任务。"
            "如果一个 agent 可以完成这个目标，就返回**一个**任务——这是有效且更优的输出，不是失败。\n\n"
            "仅当目标确实需要多个**不同角色**、有**独立的并行**子任务、或太大超出单个 agent 上下文窗口时，"
            "才拆成多个任务。选择能工作的最小图；不要为了拆分而拆分——"
            "过度拆分增加协调成本、token 开销，并扩大失败面。\n\n"
            "每个任务必须有唯一 task_id、清晰描述、来自可用角色列表的 agent_type、"
            "以及对前序任务的显式依赖。保持图足够小以适应编排预算。"
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

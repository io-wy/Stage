"""replan — ask the LLM to re-decompose a failed or stuck task."""

from __future__ import annotations

import json
import re
from typing import Any

from openagents.errors.exceptions import PermanentToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin

from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus


class ReplanTool(ToolPlugin):
    """Re-decompose a task that failed or proved too complex.

    The LLM receives the objective, the current board state, and the
    target task_id. It returns a new set of sub-tasks that replace the
    target task in the StateBoard.
    """

    name = "replan"
    description = (
        "Re-decompose a failed or overly complex task into smaller sub-tasks. "
        "Provide the task_id of the task to replace. The tool calls an LLM "
        "to generate new sub-tasks and inserts them into the plan."
    )
    durable_idempotent = False

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(
            concurrency_safe=False,
            side_effects="writes_state",
            default_timeout_ms=120_000,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task ID to re-decompose.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why the task needs re-decomposition (e.g. 'timed out', 'too complex').",
                },
            },
            "required": ["task_id", "reason"],
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        task_id = str(params.get("task_id", "")).strip()
        reason = str(params.get("reason", "")).strip()
        if not task_id or not reason:
            raise PermanentToolError(
                "task_id and reason are required", tool_name=self.name
            )

        deps = getattr(context, "deps", None)
        board = getattr(deps, "state_board", None) if deps else None
        llm_client = getattr(context, "llm_client", None)

        if board is None:
            raise PermanentToolError("StateBoard not available", tool_name=self.name)
        if llm_client is None:
            raise PermanentToolError("LLM client not available", tool_name=self.name)

        old_task = board.get_task(task_id)
        if old_task is None:
            raise PermanentToolError(
                f"Task '{task_id}' not found", tool_name=self.name
            )

        # Build prompt for re-decomposition
        prompt = self._build_replan_prompt(board, old_task, reason)

        try:
            response = await llm_client.generate(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=2048,
                tools=None,
            )
            text = response.output_text or ""
        except Exception as exc:
            raise PermanentToolError(
                f"LLM replan failed: {exc}", tool_name=self.name
            ) from exc

        # Parse new sub-tasks from JSON
        try:
            new_tasks = self._parse_tasks(text, old_task)
        except ValueError as exc:
            raise PermanentToolError(
                f"Failed to parse replan output: {exc}", tool_name=self.name
            ) from exc

        if not new_tasks:
            raise PermanentToolError(
                "Replan produced no replacement tasks", tool_name=self.name
            )

        new_task_ids = [task.task_id for task in new_tasks]
        if len(new_task_ids) != len(set(new_task_ids)):
            raise PermanentToolError(
                "Replan produced duplicate task IDs", tool_name=self.name
            )

        existing_ids = set(board.tasks) - {task_id}
        collisions = sorted(tid for tid in new_task_ids if tid in existing_ids)
        if collisions:
            raise PermanentToolError(
                f"Replan produced task IDs already in use: {collisions}",
                tool_name=self.name,
            )

        dependent_tasks = [
            task for task in board.tasks.values() if task_id in task.dependencies
        ]
        rewired_dependents = [task.task_id for task in dependent_tasks]
        replacement_exit = new_tasks[-1].task_id

        # New tasks inherit dependencies from the old task, then form a chain.
        base_dependencies = list(old_task.dependencies)
        prev_id = None
        for nt in new_tasks:
            deps = list(base_dependencies)
            if prev_id is not None:
                deps.append(prev_id)
            nt.dependencies = list(dict.fromkeys(deps))
            prev_id = nt.task_id

        # Build the final task graph before mutating board state.
        prospective_tasks: list[TaskNode] = []
        for task in board.tasks.values():
            if task.task_id == task_id:
                continue
            clone = TaskNode.from_dict(task.to_dict())
            if task_id in clone.dependencies:
                clone.dependencies = [
                    replacement_exit if dep == task_id else dep
                    for dep in clone.dependencies
                ]
            prospective_tasks.append(clone)

        dependent_label = ", ".join(rewired_dependents) if rewired_dependents else "none"
        replacement_note = (
            f"Replanned into {', '.join(new_task_ids)}; "
            f"rewired dependents={dependent_label}."
        )
        old_clone = TaskNode.from_dict(old_task.to_dict())
        if old_task.status == TaskStatus.FAILED:
            old_clone.error = (
                f"{old_clone.error} [{replacement_note}]"
                if old_clone.error
                else replacement_note
            )
        else:
            old_clone.status = TaskStatus.SKIPPED
            old_clone.error = replacement_note
        old_clone.result_output = replacement_note
        prospective_tasks.append(old_clone)
        prospective_tasks.extend(TaskNode.from_dict(task.to_dict()) for task in new_tasks)

        temp_graph = TaskGraph(objective=board.objective, tasks=prospective_tasks)
        try:
            temp_graph.validate()
        except ValueError as exc:
            raise PermanentToolError(
                f"Replan produced invalid task graph: {exc}", tool_name=self.name
            ) from exc

        # Commit the replacement and rewire downstream dependents.
        board.update_task(
            task_id,
            status=old_clone.status,
            error=old_clone.error,
            result_output=old_clone.result_output,
        )
        for nt in new_tasks:
            board.add_task(nt)
        for task in dependent_tasks:
            board.update_task(
                task.task_id,
                dependencies=[
                    replacement_exit if dep == task_id else dep
                    for dep in task.dependencies
                ],
            )

        board.log_event(
            "task.replanned",
            task_id=task_id,
            message=(
                f"Replaced with {len(new_tasks)} sub-task(s). "
                f"Reason: {reason}. {replacement_note}"
            ),
            replacement_exit=replacement_exit,
            rewired_dependents=rewired_dependents,
            new_tasks=new_task_ids,
        )

        snapshot = board.snapshot()

        return {
            "replaced_task": task_id,
            "replacement_exit": replacement_exit,
            "new_tasks": new_task_ids,
            "rewired_dependents": rewired_dependents,
            "summary": replacement_note,
            "progress": snapshot["progress"],
            "signals": snapshot["signals"],
            "reason": reason,
        }

    @staticmethod
    def _build_replan_prompt(board: Any, old_task: Any, reason: str) -> str:
        snapshot = board.snapshot()
        return (
            f"The following task failed and needs to be broken into smaller sub-tasks.\n\n"
            f"Original task: {old_task.description}\n"
            f"Failure reason: {reason}\n"
            f"Input context: {old_task.input_context}\n\n"
            f"Current plan:\n"
            f"{json.dumps(snapshot['tasks'], indent=2)}\n\n"
            f"Please output a JSON array of replacement sub-tasks:\n"
            f'[{{"task_id": "t_new_1", "description": "...", '
            f'"input_context": "detailed instructions", '
            f'"agent_type": "coder", "expected_artifacts": ["file.py"]}}]\n\n'
            f"Rules:\n"
            f"1. Each sub-task should be small enough to complete in ~5 minutes\n"
            f"2. agent_type must be one of: coder, reviewer, tester, researcher\n"
            f"3. Include expected output files\n"
            f"4. task_id must be unique (use t_new_1, t_new_2, ...)\n"
            f"5. Output strict JSON only, no markdown fences"
        )

    @staticmethod
    def _parse_tasks(text: str, old_task: Any) -> list[TaskNode]:
        """Extract TaskNode list from LLM JSON output."""
        text = text.strip()
        # Try to extract JSON array
        bracket = text.find("[")
        if bracket == -1:
            raise ValueError("No JSON array found in response")

        depth, end = 0, 0
        for i, ch in enumerate(text[bracket:], start=bracket):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
            if depth == 0:
                end = i + 1
                break

        if end <= bracket:
            raise ValueError("Unclosed JSON array")

        raw = text[bracket:end]
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError(f"Expected JSON array, got {type(data).__name__}")

        tasks: list[TaskNode] = []
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                continue
            tid = str(item.get("task_id", f"{old_task.task_id}_sub_{i}"))
            desc = str(item.get("description", ""))
            if not desc:
                continue
            tasks.append(
                TaskNode(
                    task_id=tid,
                    description=desc,
                    agent_type=str(item.get("agent_type", old_task.agent_type)),
                    dependencies=[],
                    expected_artifacts=list(item.get("expected_artifacts", [])),
                    input_context=str(item.get("input_context", "")),
                )
            )
        return tasks

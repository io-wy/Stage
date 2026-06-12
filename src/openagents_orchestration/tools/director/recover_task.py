"""recover_task — create a minimal recovery task from a failed task summary."""

from __future__ import annotations

from typing import Any

from openagents.errors.exceptions import PermanentToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin

from openagents_orchestration.models.task import TaskNode, TaskStatus
from openagents_orchestration.reporting import summarize_board


class RecoverTaskTool(ToolPlugin):
    """Create a focused recovery task and rewire downstream dependencies."""

    name = "recover_task"
    description = (
        "Create a minimal recovery task for a failed task using existing run summaries "
        "and partial artifacts, then rewire downstream blocked tasks to depend on the "
        "recovery task instead of the failed task."
    )
    durable_idempotent = False

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(
            concurrency_safe=False,
            side_effects="writes_state",
            default_timeout_ms=60_000,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Failed task ID to recover.",
                },
                "reason": {
                    "type": "string",
                    "description": "Optional recovery reason/context from the Director.",
                },
            },
            "required": ["task_id"],
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        task_id = str(params.get("task_id", "")).strip()
        reason = str(params.get("reason", "")).strip()
        if not task_id:
            raise PermanentToolError("task_id is required", tool_name=self.name)

        deps = getattr(context, "deps", None)
        board = getattr(deps, "state_board", None) if deps else None
        if board is None:
            raise PermanentToolError("StateBoard not available", tool_name=self.name)

        failed_task = board.get_task(task_id)
        if failed_task is None:
            raise PermanentToolError(f"Task '{task_id}' not found", tool_name=self.name)
        if failed_task.status != TaskStatus.FAILED:
            raise PermanentToolError(
                f"Task '{task_id}' is not failed (status={failed_task.status.value})",
                tool_name=self.name,
            )

        summaries = summarize_board(board)
        recovery_plan = summaries["orchestration_summary"].get("recovery_plan", {})
        agent_summary = _agent_summary_for_task(summaries["agent_run_summaries"], task_id)
        reusable_artifacts = recovery_plan.get("reusable_artifacts") or agent_summary.get("modified_or_created_files", [])

        recovery_id = self._next_recovery_id(board, task_id)
        recovery_task = TaskNode(
            task_id=recovery_id,
            description=(
                f"Recover failed task {task_id}: {failed_task.description}. "
                f"Preserve and inspect reusable artifacts: {', '.join(reusable_artifacts[:8]) or 'none'}. "
                f"Previous error: {failed_task.error or 'unknown'}. "
                f"Director reason: {reason or 'continue from partial progress'}"
            ),
            agent_type=failed_task.agent_type,
            dependencies=list(failed_task.dependencies),
            expected_artifacts=list(dict.fromkeys([
                *failed_task.expected_artifacts,
                *reusable_artifacts,
            ])),
            input_context=(
                f"This is a recovery task for failed task {task_id}. Do not restart from scratch.\n"
                f"Failure type: {agent_summary.get('failure_type', '')}\n"
                f"Last successful step: {agent_summary.get('last_successful_step', '')}\n"
                f"Tools already used: {', '.join(agent_summary.get('tools_called', [])[:20])}\n"
                f"Reusable artifacts: {', '.join(reusable_artifacts) or 'none'}\n"
                f"Partial output:\n{agent_summary.get('partial_output', '')[:1500]}"
            ),
        )

        dependent_tasks = [task for task in board.tasks.values() if task_id in task.dependencies]
        board.add_task(recovery_task)
        for task in dependent_tasks:
            board.update_task(
                task.task_id,
                dependencies=[recovery_id if dep == task_id else dep for dep in task.dependencies],
            )

        board.log_event(
            "task.recovery_created",
            task_id=task_id,
            message=f"Created {recovery_id}; rewired {[t.task_id for t in dependent_tasks]}",
            recovery_task_id=recovery_id,
            rewired_dependents=[t.task_id for t in dependent_tasks],
            reusable_artifacts=reusable_artifacts,
        )

        return {
            "failed_task": task_id,
            "recovery_task": recovery_id,
            "rewired_dependents": [t.task_id for t in dependent_tasks],
            "reusable_artifacts": reusable_artifacts,
            "signals": board.snapshot()["signals"],
        }

    @staticmethod
    def _next_recovery_id(board: Any, task_id: str) -> str:
        i = 1
        while f"{task_id}_recovery_{i}" in board.tasks:
            i += 1
        return f"{task_id}_recovery_{i}"


def _agent_summary_for_task(agent_summaries: dict[str, dict[str, Any]], task_id: str) -> dict[str, Any]:
    for summary in agent_summaries.values():
        if summary.get("task_id") == task_id:
            return summary
    return {}

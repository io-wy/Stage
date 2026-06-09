"""Recovery planning derived from deterministic run summaries."""

from __future__ import annotations

from typing import Any


def build_recovery_plan(board: Any, orchestration_summary: dict[str, Any]) -> dict[str, Any]:
    """Build the smallest concrete continuation plan after any run state.

    The plan is deterministic and does not call an LLM. It is intended for
    humans, resume tooling, or a future scheduler to continue from partial work.
    """
    failed = orchestration_summary.get("failed_tasks", [])
    blocked = orchestration_summary.get("blocked_tasks", [])
    pending = orchestration_summary.get("pending_tasks", [])
    review = orchestration_summary.get("review_tasks", [])
    fix_needed = orchestration_summary.get("fix_needed_tasks", [])

    if failed:
        task_id = failed[0]
        task = board.get_task(task_id)
        context = _failed_task_context(orchestration_summary, task_id)
        return {
            "mode": "recover_failed_task",
            "task_id": task_id,
            "agent_type": task.agent_type if task else "coder",
            "objective": _retry_objective(task, context),
            "reason": "A failed task is blocking progress; continue from reusable partial artifacts instead of restarting the whole run.",
            "blocked_tasks_unlocked": blocked,
            "reusable_artifacts": context.get("agent_summary", {}).get("modified_or_created_files", []),
            "failure_type": context.get("agent_summary", {}).get("failure_type", ""),
        }

    if fix_needed:
        task = board.get_task(fix_needed[0])
        return {
            "mode": "continue_fix_loop",
            "task_id": task.task_id if task else fix_needed[0],
            "agent_type": task.agent_type if task else "coder",
            "objective": task.description if task else "Continue the reviewer-requested fix loop.",
            "reason": "A task has review feedback and needs another coder iteration.",
            "blocked_tasks_unlocked": [],
            "reusable_artifacts": task.actual_artifacts if task else [],
            "failure_type": "",
        }

    if review:
        task = board.get_task(review[0])
        return {
            "mode": "run_review",
            "task_id": task.task_id if task else review[0],
            "agent_type": "reviewer",
            "objective": task.description if task else "Review the completed implementation.",
            "reason": "A task is waiting for reviewer approval before it can complete.",
            "blocked_tasks_unlocked": [],
            "reusable_artifacts": task.actual_artifacts if task else [],
            "failure_type": "",
        }

    ready = board.tasks_ready()
    if ready:
        task = ready[0]
        return {
            "mode": "run_ready_task",
            "task_id": task.task_id,
            "agent_type": task.agent_type,
            "objective": task.description,
            "reason": "A pending task is ready and can run immediately.",
            "blocked_tasks_unlocked": [],
            "reusable_artifacts": task.actual_artifacts or task.expected_artifacts,
            "failure_type": "",
        }

    if pending:
        task = board.get_task(pending[0])
        return {
            "mode": "inspect_blocked_or_pending_task",
            "task_id": task.task_id if task else pending[0],
            "agent_type": task.agent_type if task else "coder",
            "objective": task.description if task else "Inspect pending task dependencies and decide the next action.",
            "reason": "There are pending tasks, but none are currently ready.",
            "blocked_tasks_unlocked": [],
            "reusable_artifacts": task.actual_artifacts if task else [],
            "failure_type": "",
        }

    return {
        "mode": "final_verification",
        "task_id": "",
        "agent_type": "reviewer",
        "objective": "Run final verification and prepare delivery notes.",
        "reason": "No pending or failed tasks remain.",
        "blocked_tasks_unlocked": [],
        "reusable_artifacts": orchestration_summary.get("current_repo_state", {}).get("verified_artifacts", []),
        "failure_type": "",
    }


def _failed_task_context(orchestration_summary: dict[str, Any], task_id: str) -> dict[str, Any]:
    for context in orchestration_summary.get("failed_task_context", []):
        if context.get("task_id") == task_id:
            return context
    return {}


def _retry_objective(task: Any, context: dict[str, Any]) -> str:
    base = task.description if task else "Continue failed task"
    agent_summary = context.get("agent_summary", {})
    reusable = agent_summary.get("modified_or_created_files", [])
    failure_type = agent_summary.get("failure_type", "unknown")
    if reusable:
        return (
            f"Continue task without restarting from scratch: {base}. "
            f"Inspect and preserve reusable files: {', '.join(reusable[:8])}. "
            f"Previous failure type: {failure_type}."
        )
    return f"Retry or split failed task: {base}. Previous failure type: {failure_type}."

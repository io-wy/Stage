"""Deterministic run summaries for orchestration reports.

This module builds report views from already-recorded runtime facts. It does
not mutate StateBoard and does not call an LLM, so summaries are available even
when a run stops because the model/provider failed.
"""

from __future__ import annotations

from typing import Any

from openagents_orchestration.models.task import TaskStatus
from openagents_orchestration.reporting.recovery import build_recovery_plan


def summarize_agent_run(
    *,
    agent_id: str,
    task_id: str = "",
    status: str,
    error: str = "",
    output: str = "",
    transcript: list[dict[str, Any]] | None = None,
    artifacts: list[Any] | None = None,
    retry_count: int = 0,
    steps_used: int = 0,
    token_used: int = 0,
) -> dict[str, Any]:
    """Summarize a single agent run from local execution facts."""
    transcript = transcript or []
    tool_calls = _tool_calls_from_transcript(transcript)
    artifact_paths = _artifact_paths(artifacts or [])
    return {
        "agent_id": agent_id,
        "task_id": task_id,
        "status": status,
        "error": error,
        "failure_type": classify_failure(error) if status == "failed" else "",
        "retry_count": retry_count,
        "tools_called": tool_calls,
        "modified_or_created_files": artifact_paths,
        "last_successful_step": _last_successful_step(tool_calls, output),
        "partial_output": output[:2000],
        "partial_artifacts_reusable": bool(artifact_paths or output),
        "steps_used": steps_used,
        "token_used": token_used,
    }


def summarize_board(board: Any) -> dict[str, Any]:
    """Build all summaries for a board without mutating it."""
    agent_summaries = _agent_summaries_from_events(board)
    orchestration = summarize_orchestration(board, agent_summaries)
    return {
        "agent_run_summaries": agent_summaries,
        "orchestration_summary": orchestration,
    }


def summarize_orchestration(board: Any, agent_summaries: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Summarize the whole orchestration from board state and summary events."""
    agent_summaries = agent_summaries or _agent_summaries_from_events(board)
    completed = [t.task_id for t in board.tasks.values() if t.status == TaskStatus.COMPLETED]
    failed = [t.task_id for t in board.tasks.values() if t.status == TaskStatus.FAILED]
    pending = [t.task_id for t in board.tasks.values() if t.status == TaskStatus.PENDING]
    review = [t.task_id for t in board.tasks.values() if t.status == TaskStatus.REVIEW]
    fix_needed = [t.task_id for t in board.tasks.values() if t.status == TaskStatus.FIX_NEEDED]
    blocked = [t.task_id for t in board.tasks_blocked()]

    failed_context = []
    for task_id in failed:
        task = board.get_task(task_id)
        agent_id = f"{task.agent_type}-{task_id}" if task else ""
        failed_context.append({
            "task_id": task_id,
            "description": task.description if task else "",
            "error": task.error if task else "",
            "agent_summary": agent_summaries.get(agent_id, {}),
        })

    verified_artifacts = sorted(
        path for path, rec in board.artifacts.items() if rec.status == "verified"
    )
    changed_files = sorted({
        path
        for summary in agent_summaries.values()
        for path in summary.get("modified_or_created_files", [])
        if path and path != "none"
    })
    if not changed_files:
        changed_files = verified_artifacts

    next_task = _recommended_next_task(board, failed, pending, review, fix_needed)
    summary = {
        "completed_tasks": completed,
        "failed_tasks": failed,
        "pending_tasks": pending,
        "review_tasks": review,
        "fix_needed_tasks": fix_needed,
        "blocked_tasks": blocked,
        "failed_task_context": failed_context,
        "current_repo_state": {
            "verified_artifacts": verified_artifacts[-50:],
            "changed_files_from_agents": changed_files[-50:],
            "latest_test_report": board.get_project_context().get("latest_test_report"),
            "recent_errors": board.get_project_context().get("recent_errors", []),
        },
        "next_steps": _next_steps(failed=failed, blocked=blocked, pending=pending, review=review, fix_needed=fix_needed),
        "recommended_minimal_next_task": next_task,
        "progress": board.progress_summary(),
        "budget": board.budget.to_dict(),
    }
    summary["recovery_plan"] = build_recovery_plan(board, summary)
    return summary


def classify_failure(error: str) -> str:
    text = (error or "").lower()
    if any(s in text for s in ("server disconnected", "http 5", "bad gateway", "service unavailable", "timeout", "rate limit", "429")):
        return "transient_upstream"
    if any(s in text for s in ("event loop is closed", "runtimeerror")):
        return "orchestration_runtime"
    if any(s in text for s in ("permission", "denied")):
        return "permission"
    if any(s in text for s in ("syntax", "parse", "validation")):
        return "implementation"
    return "unknown" if error else ""


def _agent_summaries_from_events(board: Any) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    for event in board.events:
        if event.event_type == "agent.run_summary":
            summary = event.payload.get("summary", {})
            agent_id = summary.get("agent_id") or event.agent_id
            if agent_id:
                summaries[agent_id] = dict(summary)
    return summaries


def _tool_calls_from_transcript(transcript: list[dict[str, Any]]) -> list[str]:
    calls: list[str] = []
    for entry in transcript:
        if entry.get("role") != "assistant":
            continue
        for call in entry.get("tool_calls", []):
            name = call.get("function", {}).get("name") or call.get("name")
            if name:
                calls.append(str(name))
    return calls


def _artifact_paths(artifacts: list[Any]) -> list[str]:
    paths: list[str] = []
    for artifact in artifacts:
        path = getattr(artifact, "path", None) or str(artifact)
        if path and path not in paths:
            paths.append(path)
    return paths


def _last_successful_step(tool_calls: list[str], output: str) -> str:
    if tool_calls:
        return f"last tool call: {tool_calls[-1]}"
    if output:
        return output[:200]
    return ""


def _recommended_next_task(
    board: Any,
    failed: list[str],
    pending: list[str],
    review: list[str],
    fix_needed: list[str],
) -> dict[str, str]:
    task = None
    if failed:
        task = board.get_task(failed[0])
    elif fix_needed:
        task = board.get_task(fix_needed[0])
    elif review:
        task = board.get_task(review[0])
    else:
        ready = board.tasks_ready()
        if ready:
            task = ready[0]
        elif pending:
            task = board.get_task(pending[0])
    return {
        "task_id": task.task_id if task else "",
        "description": task.description if task else "",
    }


def _next_steps(
    *,
    failed: list[str],
    blocked: list[str],
    pending: list[str],
    review: list[str],
    fix_needed: list[str],
) -> str:
    if failed:
        return "Review the failed task summary, keep reusable artifacts, then retry or split the failed task into a smaller unit."
    if fix_needed:
        return "Send reviewer feedback back to the assigned coder and continue the fix loop."
    if review:
        return "Run reviewer agents for tasks waiting in review."
    if blocked:
        return "Resolve the failed dependency blocking pending tasks."
    if pending:
        return "Continue with the next ready pending task."
    return "Run final verification and prepare delivery notes."

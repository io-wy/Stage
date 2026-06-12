"""correct_task_status — audited task state correction tool."""

from __future__ import annotations

from typing import Any

from openagents.errors.exceptions import PermanentToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin

from openagents_orchestration.models.task import TaskStatus


class CorrectTaskStatusTool(ToolPlugin):
    """Apply an explicit audited correction to a task status."""

    name = "correct_task_status"
    description = (
        "Correct a task status when external verification proves the recorded state "
        "is wrong. This writes an audit event and should be used instead of editing "
        "snapshots by hand."
    )
    durable_idempotent = False

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(
            concurrency_safe=False,
            side_effects="writes_state",
            default_timeout_ms=30_000,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task to correct."},
                "status": {
                    "type": "string",
                    "enum": [s.value for s in TaskStatus],
                    "description": "Corrected task status.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why this correction is justified.",
                },
                "artifacts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Verified artifacts supporting the correction.",
                },
            },
            "required": ["task_id", "status", "reason"],
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        task_id = str(params.get("task_id", "")).strip()
        status_raw = str(params.get("status", "")).strip()
        reason = str(params.get("reason", "")).strip()
        artifacts = [str(path) for path in params.get("artifacts", [])]
        if not task_id or not status_raw or not reason:
            raise PermanentToolError("task_id, status, and reason are required", tool_name=self.name)

        deps = getattr(context, "deps", None)
        board = getattr(deps, "state_board", None) if deps else None
        if board is None:
            raise PermanentToolError("StateBoard not available", tool_name=self.name)
        task = board.get_task(task_id)
        if task is None:
            raise PermanentToolError(f"Task '{task_id}' not found", tool_name=self.name)
        try:
            status = TaskStatus(status_raw)
        except ValueError as exc:
            raise PermanentToolError(f"Invalid status '{status_raw}'", tool_name=self.name) from exc

        old_status = task.status.value

        # No-op if status isn't changing — avoids spurious invalid_transition events
        if status == task.status:
            # Still update artifacts if provided
            if artifacts:
                board.update_task(task_id, actual_artifacts=artifacts, _force=True)
                for artifact in artifacts:
                    board.verify_artifact(artifact, exists=True)
            return {
                "task_id": task_id,
                "old_status": old_status,
                "new_status": status.value,
                "reason": reason,
                "artifacts": artifacts,
                "note": "Status unchanged, artifacts updated",
                "signals": board.snapshot()["signals"],
            }

        update: dict[str, Any] = {"status": status, "error": None if status == TaskStatus.COMPLETED else task.error}
        if artifacts:
            update["actual_artifacts"] = artifacts
        if status == TaskStatus.COMPLETED and not task.result_output:
            update["result_output"] = f"Corrected to completed: {reason}"
        board.update_task(task_id, **update)
        for artifact in artifacts:
            board.verify_artifact(artifact, exists=True)
        board.log_event(
            "task.status_corrected",
            task_id=task_id,
            message=f"{old_status} -> {status.value}: {reason}",
            old_status=old_status,
            new_status=status.value,
            reason=reason,
            artifacts=artifacts,
        )
        return {
            "task_id": task_id,
            "old_status": old_status,
            "new_status": status.value,
            "reason": reason,
            "artifacts": artifacts,
            "signals": board.snapshot()["signals"],
        }

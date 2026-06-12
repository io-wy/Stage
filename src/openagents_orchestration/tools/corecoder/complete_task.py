"""complete_task — coder-side signal that the assigned task is finished."""

from __future__ import annotations

from typing import Any

from openagents.errors.exceptions import PermanentToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


class CompleteTaskTool(ToolPlugin):
    """Signal that the coder has finished the assigned task.

    Call ONLY when all deliverables are written, verified by tests, and
    ready for the director/reviewer. The summary is returned as the agent's
    final output.
    """

    name = "complete_task"
    description = (
        "Signal that the assigned coding task is complete. "
        "Call ONLY after all expected artifacts have been written to disk, "
        "tests pass, and no further changes are needed. "
        "Provide a concise summary of what was implemented and which files were changed."
    )
    durable_idempotent = False

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(
            concurrency_safe=False,
            side_effects="writes_state",
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": (
                        "Concise summary of what was implemented, which files were "
                        "created or modified, and verification results (e.g. tests passed)."
                    ),
                },
                "artifacts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional list of file paths produced or modified by this task. "
                        "These are claimed in addition to any FILES_CREATED / FILES_MODIFIED markers."
                    ),
                },
            },
            "required": ["summary"],
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> str:
        summary = str(params.get("summary", "")).strip()
        if not summary:
            raise PermanentToolError("summary is required", tool_name=self.name)

        artifacts = params.get("artifacts") or []
        if not isinstance(artifacts, list):
            raise PermanentToolError("artifacts must be a list", tool_name=self.name)
        artifacts = [str(a).strip() for a in artifacts if str(a).strip()]

        ctx = context
        ctx.state["__complete_task_summary__"] = summary
        if artifacts:
            ctx.state["__complete_task_artifacts__"] = artifacts

        # Emit is best-effort — the state flags above are what actually stop the loop
        try:
            await ctx.emit(
                "tool.complete_task",
                summary=summary,
                artifacts=artifacts,
            )
        except Exception:
            pass

        return summary

"""stop_resident — stop a persistent resident agent."""

from __future__ import annotations

from typing import Any

from openagents.errors.exceptions import PermanentToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


class StopResidentTool(ToolPlugin):
    """Stop a persistent resident agent and release its resources."""

    name = "stop_resident"
    description = (
        "Stop a persistent resident agent. The agent will finish any "
        "in-flight work and then shut down. Use this when the resident "
        "is no longer needed or has been idle."
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
                "resident_id": {
                    "type": "string",
                    "description": "The resident ID to stop.",
                },
            },
            "required": ["resident_id"],
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        resident_id = str(params.get("resident_id", "")).strip()
        if not resident_id:
            raise PermanentToolError("resident_id is required", tool_name=self.name)

        deps = getattr(context, "deps", None)
        runner = getattr(deps, "runner", None) if deps else None
        if runner is None:
            raise PermanentToolError("Runner not available", tool_name=self.name)

        try:
            await runner.stop_resident(resident_id)
        except Exception as exc:
            raise PermanentToolError(
                f"Failed to stop resident: {exc}", tool_name=self.name
            ) from exc

        return {
            "resident_id": resident_id,
            "status": "stopped",
            "message": f"Resident {resident_id} stopped.",
        }

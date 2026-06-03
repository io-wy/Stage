"""show_state — read the global StateBoard snapshot for the Director."""

from __future__ import annotations

import json
from typing import Any

from openagents.errors.exceptions import PermanentToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


class ShowStateTool(ToolPlugin):
    """Return a structured snapshot of the current orchestration state."""

    name = "show_state"
    description = (
        "Read the current global state panel. Use this to understand "
        "which tasks are pending/running/done, which agents are active, "
        "what files have been produced, and the remaining budget. "
        "Call this before making scheduling decisions."
    )
    durable_idempotent = True

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(concurrency_safe=True, side_effects="read_only")

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "description": (
                        "Optional: filter to a section. "
                        "One of: tasks, agents, artifacts, signals, budget, events."
                    ),
                },
            },
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> str:
        deps = getattr(context, "deps", None)
        board = getattr(deps, "state_board", None) if deps else None
        if board is None:
            raise PermanentToolError("StateBoard not available", tool_name=self.name)

        section = params.get("section")
        snapshot = board.snapshot()

        payload = snapshot[section] if section and section in snapshot else snapshot

        # Append suggested tools based on current state
        suggested_tools = board.suggest_tools()
        if suggested_tools:
            payload["suggested_next_tools"] = suggested_tools

        # Append fallback suggestions for failed tasks
        failed_tasks = payload.get("tasks", []) if isinstance(payload, dict) else []
        extra_lines: list[str] = []
        if isinstance(failed_tasks, list):
            for task in failed_tasks:
                if task.get("status") == "failed":
                    suggestion = board.suggest_fallback(task["id"])
                    if suggestion:
                        extra_lines.append(suggestion)

        text = json.dumps(payload, indent=2, ensure_ascii=False)
        if extra_lines:
            text += "\n" + "\n".join(extra_lines)
        return text

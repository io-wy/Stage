"""In-memory todo list for tactical agents.

Claude Code's todo_write / todo_read pattern: a lightweight task tracker
stored in the agent's scratch space. Helps agents stay focused during
multi-step work without external state.
"""

from __future__ import annotations

from typing import Any

from openagents.errors.exceptions import ToolError
from openagents.interfaces.run_context import RunContext
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


class TodoWriteTool(ToolPlugin):
    """Set the agent's in-memory todo list. Overwrites the entire list."""

    name = "todo_write"
    description = (
        "Set or update your personal task list. Provide the full list each time; "
        "it replaces the previous one. Use this to track sub-tasks during complex "
        "work. Only ONE task may be 'in_progress' at a time."
    )
    durable_idempotent = False

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(
            concurrency_safe=True,
            side_effects="writes_state",
            default_timeout_ms=5_000,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "description": "List of todo items. Replaces any existing list.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "Unique identifier (e.g. '1', 'a', 'setup').",
                            },
                            "content": {
                                "type": "string",
                                "description": "What needs to be done.",
                            },
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                                "description": "Task status. Only one task may be in_progress.",
                            },
                            "priority": {
                                "type": "string",
                                "enum": ["low", "medium", "high"],
                                "description": "Importance level.",
                            },
                        },
                        "required": ["id", "content", "status"],
                    },
                },
            },
            "required": ["todos"],
        }

    async def invoke(
        self, params: dict[str, Any], context: RunContext[Any] | None
    ) -> dict[str, Any]:
        todos = params.get("todos")
        if not isinstance(todos, list):
            raise ToolError("todos must be a list", tool_name=self.name)

        # Validate each item
        validated: list[dict[str, Any]] = []
        in_progress_count = 0
        for i, item in enumerate(todos):
            if not isinstance(item, dict):
                raise ToolError(f"todo[{i}] must be an object", tool_name=self.name)
            tid = str(item.get("id", "")).strip()
            if not tid:
                raise ToolError(f"todo[{i}] missing id", tool_name=self.name)
            content = str(item.get("content", "")).strip()
            if not content:
                raise ToolError(f"todo[{i}] missing content", tool_name=self.name)
            status = str(item.get("status", "")).strip()
            if status not in ("pending", "in_progress", "completed"):
                raise ToolError(
                    f"todo[{i}] status must be pending/in_progress/completed",
                    tool_name=self.name,
                )
            if status == "in_progress":
                in_progress_count += 1
            validated.append(
                {
                    "id": tid,
                    "content": content,
                    "status": status,
                    "priority": str(item.get("priority", "medium")).strip(),
                }
            )

        if in_progress_count > 1:
            raise ToolError(
                f"Only one task may be in_progress at a time (found {in_progress_count}).",
                tool_name=self.name,
            )

        if context is not None:
            context.scratch["todo_list"] = validated

        counts = {"pending": 0, "in_progress": 0, "completed": 0}
        for t in validated:
            counts[t["status"]] = counts.get(t["status"], 0) + 1

        return {
            "todos": validated,
            "summary": f"{counts['completed']}/{len(validated)} done, {counts['in_progress']} in progress",
            "message": (
                f"Todo list updated: {counts['completed']}/{len(validated)} completed, "
                f"{counts['in_progress']} in_progress, {counts['pending']} pending."
            ),
        }


class TodoReadTool(ToolPlugin):
    """Read the agent's current in-memory todo list."""

    name = "todo_read"
    description = (
        "Read your current task list. Call this at the start of a complex task "
        "or every few turns to stay oriented."
    )
    durable_idempotent = True

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(
            concurrency_safe=True,
            side_effects="none",
            default_timeout_ms=1_000,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
        }

    async def invoke(
        self, params: dict[str, Any], context: RunContext[Any] | None
    ) -> dict[str, Any]:
        todos: list[dict[str, Any]] = []
        if context is not None:
            raw = context.scratch.get("todo_list")
            if isinstance(raw, list):
                todos = raw

        if not todos:
            return {
                "todos": [],
                "message": "No todos yet. Use todo_write to create a task list.",
            }

        counts = {"pending": 0, "in_progress": 0, "completed": 0}
        for t in todos:
            counts[t.get("status", "pending")] = counts.get(t.get("status", "pending"), 0) + 1

        lines = [f"Todo list ({counts['completed']}/{len(todos)} done):"]
        for t in todos:
            mark = {"completed": "[x]", "in_progress": "[>]", "pending": "[ ]"}.get(
                t.get("status", "pending"), "[ ]"
            )
            prio = f" ({t.get('priority', 'medium')})" if t.get("priority") else ""
            lines.append(f"  {mark} {t['id']}: {t['content']}{prio}")

        return {
            "todos": todos,
            "summary": f"{counts['completed']}/{len(todos)} done",
            "message": "\n".join(lines),
        }

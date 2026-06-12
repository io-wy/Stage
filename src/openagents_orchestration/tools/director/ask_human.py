"""ask_human — record a question for human intervention."""

from __future__ import annotations

from typing import Any

from openagents.errors.exceptions import PermanentToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


class AskHumanTool(ToolPlugin):
    """Record a question that requires human input.

    The question is stored in StateBoard. The orchestrator may pause
    and wait for a reply, or continue with a best-effort assumption.
    """

    name = "ask_human"
    description = (
        "Ask the human user a clarifying question. Use when: ambiguous "
        "requirements, missing credentials, design decisions that affect "
        "the whole project. Record the question in the state board."
    )
    durable_idempotent = True

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(
            concurrency_safe=True,
            side_effects="writes_state",
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to ask the human. Be specific.",
                },
                "options": {
                    "type": "string",
                    "description": "Optional: comma-separated options (e.g. 'A, B, C').",
                },
            },
            "required": ["question"],
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> str:
        question = str(params.get("question", "")).strip()
        options = str(params.get("options", "")).strip()
        if not question:
            raise PermanentToolError("question is required", tool_name=self.name)

        deps = getattr(context, "deps", None)
        board = getattr(deps, "state_board", None) if deps else None
        if board is None:
            raise PermanentToolError("StateBoard not available", tool_name=self.name)

        from_agent = getattr(context, "agent_id", "unknown")
        qid = board.ask_human(question, options=options, from_agent=from_agent)

        board.log_event(
            "human.question",
            agent_id=from_agent,
            message=f"Question: {question[:200]}",
        )

        reply = f"Question recorded (id={qid}): {question}"
        if options:
            reply += f" Options: {options}"
        reply += "\nWaiting for human reply."
        return reply

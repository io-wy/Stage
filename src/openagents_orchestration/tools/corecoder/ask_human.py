"""ask_human — CoreCoder-side tool for asking the human a clarifying question.

This is a counterpart to the director-level ``AskHumanTool``. It works both
inside the full Stage orchestrator (where a ``StateBoard`` is available) and
in standalone ``scripts/run_corecoder.py`` runs (where the question is stored
in ``ctx.state`` and surfaced to the caller).
"""

from __future__ import annotations

from typing import Any

from openagents.errors.exceptions import PermanentToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


class CoreCoderAskHumanTool(ToolPlugin):
    """Record a clarifying question for the human user.

    Use when the task is ambiguous, the scope is unclear, or multiple valid
    interpretations exist. The agent loop will pause and wait for a human
    reply before continuing.
    """

    name = "ask_human"
    description = (
        "Ask the human user a clarifying question. Use when: ambiguous "
        "requirements, unclear scope, conflicting evidence, or design decisions "
        "that affect the task outcome. The agent will pause and wait for a reply."
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
                    "description": "The clarifying question to ask the human. Be specific.",
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

        ctx = context
        if ctx is None:
            raise PermanentToolError("RunContext required", tool_name=self.name)

        from_agent = getattr(ctx, "agent_id", "unknown")
        qid: str | None = None

        # Prefer StateBoard when running inside the full Stage orchestrator.
        deps = getattr(ctx, "deps", None)
        board = getattr(deps, "state_board", None) if deps else None
        if board is not None:
            qid = board.ask_human(question, options=options, from_agent=from_agent)
            board.log_event(
                "human.question",
                agent_id=from_agent,
                message=f"Question: {question[:200]}",
            )

        # Always mirror the pending question into ctx.state so standalone callers
        # (and tests) can detect the pause even without a StateBoard.
        ctx.state["__pending_human_question__"] = {
            "qid": qid,
            "question": question,
            "options": options,
            "from_agent": from_agent,
        }
        ctx.state["__awaiting_human_reply__"] = {
            "qid": qid,
            "question": question,
            "options": options,
            "from_agent": from_agent,
        }

        reply = "[Awaiting human reply]"
        if qid:
            reply += f" Question recorded (id={qid}): {question}"
        else:
            reply += f" {question}"
        if options:
            reply += f" Options: {options}"
        return reply

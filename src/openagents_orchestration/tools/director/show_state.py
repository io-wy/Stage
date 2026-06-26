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
        "Read the current global state panel. This is the Director's primary source of truth "
        "for making scheduling decisions.\n\n"
        "# What it returns\n"
        "- objective, budget (tokens/steps/time used and remaining), and progress summary.\n"
        "- tasks: every task with id, description, agent_type, status, dependencies, artifacts, errors, priority.\n"
        "- agents/residents: who is running, idle, failed, or stuck, plus resource usage.\n"
        "- signals: ready_to_run, running, blocked, deadline_overdue, needs_human, pending_messages, unanswered_human_questions.\n"
        "- dlq_summary: dead-letter messages that indicate stuck or failed deliveries.\n"
        "- decision_feedback and strategy_signals: historical success rate and automated warnings.\n"
        "- suggested_next_tools: a short list of tools likely to be useful right now.\n"
        "- fallback suggestions for failed tasks.\n\n"
        "# When to use\n"
        "- At the start of every turn before deciding what to do next.\n"
        "- After a task completes or fails, to update your mental model.\n"
        "- Before deciding retry vs replan vs resident vs ask_human.\n"
        "- Before finalizing, to confirm all tasks are terminal and artifacts are verified.\n"
        "- When you suspect a resident is stuck or a message was lost.\n\n"
        "# When NOT to use\n"
        "- As a substitute for reading files — if a task claims an artifact, still call read_file to verify it.\n"
        "- After every tiny action — batch your reasoning and call it when state may have changed.\n"
        "- When you already have the answer and just need to act.\n\n"
        "# Parameters\n"
        "- section (optional): filter to one of 'tasks', 'agents', 'artifacts', 'signals', 'budget', 'events'. "
        "Useful when you only need a slice, but the full snapshot is usually best.\n\n"
        "# Common mistakes\n"
        "- Trusting claimed artifacts without verifying them with read_file/bash.\n"
        "- Ignoring strategy_signals or decision_feedback and repeating a failing strategy.\n"
        "- Calling finalize without checking unanswered_human_questions."
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

        try:
            section = params.get("section")
            snapshot = board.snapshot()

            payload = snapshot[section] if section and section in snapshot else snapshot

            # Append DLQ summary so Director can spot stuck messages
            dlq_summary = await board.inspect_dlq()
            if dlq_summary:
                payload["dlq_summary"] = dlq_summary

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
        except Exception as exc:
            # Surface internal errors to the Director instead of failing silently.
            # This prevents the Director from getting stuck on transient snapshot
            # or mailbox inspection issues.
            import traceback
            error_text = f"show_state internal error: {exc}\n{traceback.format_exc()}"
            return error_text

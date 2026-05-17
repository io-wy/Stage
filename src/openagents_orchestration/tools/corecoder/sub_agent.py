"""Sub-agent delegation tool.

Faithful port of CoreCoder's AgentTool: spawn an isolated agent run with a
fresh context window, let it use the same tool set (minus ``sub_agent`` to
prevent recursion), and return its final output as a string.

In OpenAgents this is implemented by booting a child :class:`Runtime` with a
``sub_agent_id`` (a separate agent definition in the same ``agent.json`` whose
tools list omits ``sub_agent``). The child run uses a fresh ``session_id`` so
its transcript and memory cannot bleed into the parent.

Configuration:
    - ``agent_config_path``: path to an ``agent.json`` containing the sub-agent.
    - ``sub_agent_id``: the agent id to invoke (default ``corecoder-subagent``).
    - ``max_output_chars``: cap returned text to protect parent context (5000).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from openagents.errors.exceptions import ToolError
from openagents.interfaces.run_context import RunContext
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


_DEFAULT_MAX_OUTPUT = 5_000


class SubAgentTool(ToolPlugin):
    """Spawn an isolated child agent and return its summary."""

    name = "sub_agent"
    description = (
        "Delegate a complex sub-task to an isolated agent with its own context "
        "window. Use for: deep codebase research, multi-step refactors that "
        "should not pollute the main agent's memory. Returns the sub-agent's "
        "final text output (truncated to ~5000 chars)."
    )
    durable_idempotent = False

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})
        self._agent_config_path = self.config.get("agent_config_path")
        self._sub_agent_id = self.config.get("sub_agent_id", "corecoder-subagent")
        self._max_output_chars = int(
            self.config.get("max_output_chars", _DEFAULT_MAX_OUTPUT) or _DEFAULT_MAX_OUTPUT
        )
        self._runtime: Any | None = None  # cached child runtime

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(
            concurrency_safe=False,
            side_effects="external",
            default_timeout_ms=10 * 60 * 1_000,  # sub-agents may take a few minutes
            interrupt_behavior="cancel",
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "What the sub-agent should accomplish.",
                },
            },
            "required": ["task"],
        }

    async def invoke(
        self, params: dict[str, Any], context: "RunContext[Any] | None"
    ) -> dict[str, Any]:
        task = str(params.get("task", "")).strip()
        if not task:
            raise ToolError("task is required", tool_name=self.name)
        runner = _extract_runner(context)
        parent_session = context.session_id if context is not None else "anon"
        sub_session = f"{parent_session}-sub-{uuid4().hex[:8]}"

        try:
            if runner is not None:
                result_text = await runner.run(
                    agent_id=self._sub_agent_id,
                    session_id=sub_session,
                    input_text=task,
                )
            else:
                if not self._agent_config_path:
                    raise ToolError(
                        "SubAgentTool needs config.agent_config_path pointing to an agent.json",
                        tool_name=self.name,
                    )
                config_path = Path(self._agent_config_path).expanduser()
                if not config_path.exists():
                    raise ToolError(
                        f"Sub-agent config not found: {self._agent_config_path}",
                        tool_name=self.name,
                    )
                runtime = self._get_runtime(config_path)
                result_text = await runtime.run(
                    agent_id=self._sub_agent_id,
                    session_id=sub_session,
                    input_text=task,
                )
        except Exception as exc:
            return {
                "task": task,
                "session_id": sub_session,
                "error": f"sub-agent run failed: {exc}",
                "result": "",
                "message": f"Sub-agent error: {exc}",
            }

        text = str(result_text or "")
        truncated = False
        if len(text) > self._max_output_chars:
            text = text[: self._max_output_chars - 200] + "\n... (sub-agent output truncated)"
            truncated = True

        return {
            "task": task,
            "session_id": sub_session,
            "result": text,
            "truncated": truncated,
            "message": f"[Sub-agent completed]\n{text}",
        }

    def _get_runtime(self, config_path: Path) -> Any:
        if self._runtime is None:
            from openagents.runtime.runtime import Runtime

            self._runtime = Runtime.from_config(config_path)
        return self._runtime


def _extract_runner(context: "RunContext[Any] | None") -> Any | None:
    if context is None:
        return None
    deps = getattr(context, "deps", None)
    if deps is None:
        return None
    runner = getattr(deps, "corecoder_runner", None)
    if runner is not None:
        return runner
    if isinstance(deps, dict):
        return deps.get("corecoder_runner")
    return None

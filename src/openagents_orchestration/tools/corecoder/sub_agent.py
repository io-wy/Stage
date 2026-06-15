"""Sub-agent delegation tool for CoreCoder.

Claude Code-style ``Agent`` tool: spawn another agent type with its own context
window to handle a self-contained sub-task, then return a summary. Works both
inside the orchestrator (reuses the runner via context.deps.runner) and in
standalone CoreCoder runs (creates a local runner).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openagents.errors.exceptions import ToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin

from openagents_orchestration.core.runner import OrchestratorRunner

_MAX_SUB_AGENT_DEPTH = 2


@dataclass
class _MinimalDeps:
    """Minimal deps for standalone sub-agent runs."""

    state_board: Any = None
    runner_delegate: Any = None
    runner: Any = None
    artifact_store: Any = None
    matrix_transport: Any = None


class SubAgentTool(ToolPlugin):
    """Delegate a self-contained sub-task to another agent type.

    The sub-agent gets its own context window and returns a concise summary.
    Use for tasks like "audit all uses of X", "research how Y works", or
    "write tests for Z" while the parent agent keeps the high-level plan.
    """

    name = "sub_agent"
    description = (
        "Delegate a self-contained sub-task to a focused agent with its own "
        "context window. Provide a clear instruction and choose agent_type from "
        "any agent configured in agent.json (e.g. reviewer, researcher, coder). "
        "Sub-agents cannot spawn further sub-agents beyond depth 2, "
        "so keep the task self-contained. "
        "Use for tasks that need focused exploration or independent verification, "
        "not for trivial edits under ~3 file reads."
    )
    durable_idempotent = False

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(
            concurrency_safe=False,
            side_effects="external",
            default_timeout_ms=10 * 60 * 1_000,
            interrupt_behavior="cancel",
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "agent_type": {
                    "type": "string",
                    "description": "Type of agent to spawn. Must match an agent id in agent.json.",
                },
                "instruction": {
                    "type": "string",
                    "description": "Clear, self-contained task for the sub-agent.",
                },
                "expected_output": {
                    "type": "string",
                    "description": "Optional hint on desired output format (e.g. 'bullet list', 'code diff').",
                },
            },
            "required": ["agent_type", "instruction"],
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        agent_type = str(params.get("agent_type", "")).strip()
        instruction = str(params.get("instruction", "")).strip()
        expected_output = str(params.get("expected_output", "")).strip()

        if not agent_type:
            raise ToolError("agent_type is required", tool_name=self.name)
        if not instruction:
            raise ToolError("instruction is required", tool_name=self.name)

        current_depth = 0
        if context is not None:
            current_depth = int(getattr(context, "state", {}).get("__sub_agent_depth__", 0) or 0)
        if current_depth >= _MAX_SUB_AGENT_DEPTH:
            raise ToolError(
                f"Sub-agent depth limit ({_MAX_SUB_AGENT_DEPTH}) reached. "
                "Cannot spawn further sub-agents.",
                tool_name=self.name,
            )

        full_instruction = instruction
        if expected_output:
            full_instruction += f"\n\nDesired output format: {expected_output}"

        # Try to reuse the orchestrator runner first.
        runner = getattr(getattr(context, "deps", None), "runner", None)
        if runner is not None and hasattr(runner, "run_agent"):
            return await self._spawn_via_runner(
                runner, agent_type, full_instruction, depth=current_depth + 1
            )

        # Standalone mode: create a local runner.
        return await self._spawn_standalone(
            context, agent_type, full_instruction, depth=current_depth + 1
        )

    async def _spawn_via_runner(
        self, runner: Any, agent_type: str, instruction: str, depth: int
    ) -> dict[str, Any]:
        agent_id = f"sub-{agent_type}-{self._short_id()}"
        try:
            output = await runner.run_agent(
                agent_type=agent_type,
                input_text=instruction,
                agent_id=agent_id,
                state={"__sub_agent_depth__": depth},
            )
        except TypeError:
            # Older runners may not accept state=.
            output = await runner.run_agent(
                agent_type=agent_type,
                input_text=instruction,
                agent_id=agent_id,
            )
        except Exception as exc:
            return {
                "agent_type": agent_type,
                "agent_id": agent_id,
                "status": "failed",
                "output": "",
                "error": str(exc),
                "message": f"Sub-agent {agent_type} failed: {exc}",
            }
        return {
            "agent_type": agent_type,
            "agent_id": agent_id,
            "status": "completed",
            "output": output,
            "error": None,
            "message": f"Sub-agent {agent_type} completed.",
        }

    async def _spawn_standalone(
        self, context: Any, agent_type: str, instruction: str, depth: int
    ) -> dict[str, Any]:
        repo_root = self._find_repo_root(context)
        config_path = repo_root / "agent.json"
        if not config_path.exists():
            return {
                "agent_type": agent_type,
                "status": "failed",
                "output": "",
                "error": "agent.json not found",
                "message": "Cannot spawn sub-agent: agent.json not found.",
            }

        runner = OrchestratorRunner(config_path)
        runner._current_work_dir = repo_root
        runner._deps = _MinimalDeps()

        agent_id = f"sub-{agent_type}-{self._short_id()}"
        try:
            result = await runner._run_single(
                agent_id=agent_id,
                agent_type=agent_type,
                input_text=instruction,
                state={"__sub_agent_depth__": depth},
            )
            output = result.final_output or ""
            status = "completed" if result.stop_reason.value == "completed" else "failed"
            error = None if status == "completed" else str(result.stop_reason)
        except Exception as exc:
            output = ""
            status = "failed"
            error = str(exc)
        finally:
            await runner.close()

        return {
            "agent_type": agent_type,
            "agent_id": agent_id,
            "status": status,
            "output": output,
            "error": error,
            "message": (
                f"Sub-agent {agent_type} completed."
                if status == "completed"
                else f"Sub-agent {agent_type} failed: {error}"
            ),
        }

    def _find_repo_root(self, context: Any) -> Path:
        """Find repo root from context or cwd."""
        if context is not None:
            runner = getattr(getattr(context, "deps", None), "runner", None)
            cwd = getattr(runner, "_current_work_dir", None)
            if isinstance(cwd, (str, Path)):
                return Path(cwd)
            scratch_cwd = getattr(context, "scratch", {}).get("bash_cwd")
            if isinstance(scratch_cwd, str):
                return Path(scratch_cwd)
        return Path.cwd()

    @staticmethod
    def _short_id() -> str:
        """Return a short random suffix."""
        import uuid

        return uuid.uuid4().hex[:6]

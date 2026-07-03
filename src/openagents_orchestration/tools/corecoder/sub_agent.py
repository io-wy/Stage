"""Sub-agent delegation tool for CoreCoder.

Claude Code-style ``Agent`` tool: spawn another agent type with its own context
window to handle a self-contained sub-task, then return a summary. Works both
inside the orchestrator (reuses the runner via context.deps.runner) and in
standalone CoreCoder runs (creates a local runner).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openagents.errors.exceptions import ToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin

from openagents_orchestration.core.agent_loader import (
    AgentSpecError,
    _load_json,
    compile_one_spec,
)
from openagents_orchestration.core.runner import OrchestratorRunner

_MAX_SUB_AGENT_DEPTH = 1


@dataclass
class _MinimalDeps:
    """Minimal deps for standalone sub-agent runs."""

    state_board: Any = None
    runner_delegate: Any = None
    runner: Any = None


class SubAgentTool(ToolPlugin):
    """Delegate a self-contained sub-task to another agent type.

    The sub-agent gets its own context window and returns a concise summary.
    Use for tasks like "audit all uses of X", "research how Y works", or
    "write tests for Z" while the parent agent keeps the high-level plan.
    """

    name = "sub_agent"
    description = (
        "Delegate a self-contained sub-task to a focused agent with its own "
        "context window. Either choose an existing agent_type (e.g. reviewer, "
        "researcher, coder), OR pass agent_spec to define a one-off agent inline "
        "(id + prompts + tools, extends the shared base). "
        "Sub-agents cannot spawn further sub-agents beyond depth 1, "
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
                    "description": (
                        "Existing agent id to spawn (coder, reviewer, researcher, "
                        "github_agent, monitor). Omit if providing agent_spec."
                    ),
                },
                "agent_spec": {
                    "type": "object",
                    "description": (
                        "Optional inline role definition for a one-off agent. "
                        "Keys: id (required), prompts (list of 'module:SYMBOL' refs), "
                        "tools (list, '+tool'/'-tool' deltas on the shared base), "
                        "pattern.config (e.g. {max_steps}). Extends agents/_base.json. "
                        "Tools must be registered names. Takes precedence over agent_type."
                    ),
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
            "required": ["instruction"],
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        agent_type = str(params.get("agent_type", "")).strip()
        agent_spec = params.get("agent_spec")
        instruction = str(params.get("instruction", "")).strip()
        expected_output = str(params.get("expected_output", "")).strip()

        runner = getattr(getattr(context, "deps", None), "runner", None)

        # Inline 角色定义：工具自己编译进 AgentDefinition 并写入 runner，不经过
        # runner.register_agent_spec，保持 runner 简单。
        if agent_spec:
            if not isinstance(agent_spec, dict):
                raise ToolError("agent_spec must be an object", tool_name=self.name)
            if runner is None:
                raise ToolError(
                    "agent_spec requires an orchestrator runner (not available in "
                    "standalone mode); use a registered agent_type instead.",
                    tool_name=self.name,
                )
            agents_dir = Path("agents")
            if hasattr(runner, "_config_path"):
                agents_dir = runner._config_path.parent / "agents"
            base = _load_json(agents_dir / "_base.json")
            try:
                agent_def = compile_one_spec(agent_spec, base=base)
            except AgentSpecError as exc:
                raise ToolError(
                    f"Failed to compile inline agent_spec: {exc}",
                    tool_name=self.name,
                ) from exc
            runner._agents_by_id[agent_def.id] = agent_def
            runner._bundles.pop(agent_def.id, None)
            agent_type = agent_def.id

        if not agent_type:
            raise ToolError("agent_type or agent_spec is required", tool_name=self.name)
        if not instruction:
            raise ToolError("instruction is required", tool_name=self.name)

        current_depth = 0
        if context is not None:
            current_depth = int(
                getattr(context, "state", {}).get("__sub_agent_depth__", 0) or 0
            )
        if current_depth >= _MAX_SUB_AGENT_DEPTH:
            raise ToolError(
                f"Sub-agent depth limit ({_MAX_SUB_AGENT_DEPTH}) reached. "
                "Cannot spawn further sub-agents.",
                tool_name=self.name,
            )

        full_instruction = instruction
        if expected_output:
            full_instruction += f"\n\nDesired output format: {expected_output}"

        # Try to reuse the orchestrator runner first (resolved at invoke start).
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
        # Leaf-ify the child: strip the sub_agent tool so it physically cannot
        # recurse further — a hard guard complementing the depth counter.
        leaf_type = self._leafify(agent_type, runner)
        agent_id = f"sub-{agent_type}-{self._short_id()}"
        try:
            outcome = await runner.run_agent(
                agent_type=leaf_type,
                input_text=instruction,
                agent_id=agent_id,
                state={"__sub_agent_depth__": depth},
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
        output = str(getattr(outcome, "output", "") or "")
        status_value = getattr(getattr(outcome, "status", None), "value", "")
        status = "completed" if status_value == "completed" else "failed"
        return {
            "agent_type": agent_type,
            "agent_id": agent_id,
            "status": status,
            "output": output,
            "error": None if status == "completed" else status_value,
            "message": (
                f"Sub-agent {agent_type} completed. Result: {self._clean_output(output)[:600]}"
                if status == "completed"
                else f"Sub-agent {agent_type} failed: {status_value}"
            ),
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

        leaf_type = self._leafify(agent_type, runner)
        agent_id = f"sub-{agent_type}-{self._short_id()}"
        try:
            result = await runner._run_single(
                agent_id=agent_id,
                agent_type=leaf_type,
                input_text=instruction,
                state={"__sub_agent_depth__": depth},
            )
            output = result.final_output or ""
            status = (
                "completed" if result.stop_reason.value == "completed" else "failed"
            )
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
                f"Sub-agent {agent_type} completed. Result: {self._clean_output(output)[:600]}"
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

    @staticmethod
    def _clean_output(raw: Any) -> str:
        """Extract clean text from a sub-agent's output.

        PIT-001: ``run_agent`` sometimes yields an outcome whose ``output`` is the
        repr of a nested ``PatternOutcome`` rather than plain text. Pull the inner
        ``output=...`` value out so the parent coder sees the real result instead
        of a struct dump.
        """
        s = str(raw or "").strip()
        if s.startswith("PatternOutcome(output="):
            m = re.search(r"output=(['\"])(.*?)\1(?:, status=|\))", s, re.DOTALL)
            if m:
                return m.group(2).strip()
        return s

    @staticmethod
    def _tool_ref_id(ref: Any) -> str:
        """Extract the tool id from a compiled ToolRef (dict or pydantic model)."""
        if isinstance(ref, dict):
            return str(ref.get("id", ""))
        return str(getattr(ref, "id", ""))

    def _leafify(self, agent_type: str, runner: Any) -> str:
        """Return an id for a leaf clone of ``agent_type`` with ``sub_agent`` stripped.

        Children spawned by a coder are leaves: at MAX_DEPTH=1 the coder may
        delegate exactly one level, and that level must not recurse. Removing
        the sub_agent tool from the child is a deterministic guard that does
        not rely on the LLM honoring the depth counter. Falls back to the
        original type if the role is unknown or not clonable (the depth counter
        still bounds it).
        """
        leaf_id = f"{agent_type}__leaf"
        agents = getattr(runner, "_agents_by_id", None)
        if not isinstance(agents, dict):
            return agent_type
        if leaf_id in agents:
            return leaf_id
        src = agents.get(agent_type)
        if src is None or not hasattr(src, "model_copy"):
            return agent_type
        src_tools = list(getattr(src, "tools", []))
        # 本就不带 sub_agent 的角色无需叶子化（避免冗余 __leaf 副本）。
        if not any(self._tool_ref_id(t) == "sub_agent" for t in src_tools):
            return agent_type
        leaf_tools = [t for t in src_tools if self._tool_ref_id(t) != "sub_agent"]
        leaf_def = src.model_copy(update={"id": leaf_id, "tools": leaf_tools})
        agents[leaf_id] = leaf_def
        if hasattr(runner, "_bundles"):
            runner._bundles.pop(leaf_id, None)
        return leaf_id

"""spawn_agent — launch a tactical agent to execute a task.

Supports two modes:
- Single task: task_id="t1"
- Batch parallel: task_ids=["t1", "t2", "t3"] (independent tasks only)
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from openagents.errors.exceptions import PermanentToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin

from openagents_orchestration.core.agent_loader import (
    AgentSpecError,
    _load_json,
    compile_one_spec,
)
from openagents_orchestration.core.state_board import AgentStatus
from openagents_orchestration.models.task import TaskStatus
from prompts.agent_constraints import CODER_CONSTRAINT, REVIEWER_CONSTRAINT


class SpawnAgentTool(ToolPlugin):
    """Execute pending task(s) by spawning tactical agent(s).

    Single task: provide task_id.
    Batch parallel: provide task_ids (all must be independent and ready).
    """

    name = "spawn_agent"
    description = (
        "Spawn a tactical agent to execute one or more ready tasks. "
        "This is the primary way to delegate work to specialist agents.\n\n"
        "# Effects\n"
        "- The agent receives the task description, dependencies, expected artifacts, and your optional 'context'.\n"
        "- It runs in its own context window with the tools configured for its role (coder, reviewer, researcher, etc.).\n"
        "- The runner's ``pattern.after_execute`` hook updates the StateBoard (task status, artifacts, decision history).\n"
        "- This tool itself does NOT modify StateBoard beyond marking the task/agent as RUNNING before delegation.\n"
        "- In batch mode, multiple independent agents run concurrently.\n\n"
        "# When to use\n"
        "- A task is ready (dependencies met) and you want a specialist to execute it.\n"
        "- You need parallel work on multiple independent ready tasks.\n"
        "- The task benefits from a different role's perspective (reviewer, researcher, monitor).\n"
        "- The task is too large for a single context window and needs its own workspace.\n\n"
        "# When NOT to use\n"
        "- The task is not ready (dependencies missing or blocked) — wait or unblock first.\n"
        "- You only need a tiny edit you can verify yourself — use edit_file/apply_patch directly.\n"
        "- You want synchronous back-and-forth — use send_message instead.\n\n"
        "# Parameters\n"
        "- task_id (string, required for single task): the ready task to execute.\n"
        "- task_ids (list of strings, optional, for batch mode): multiple independent ready tasks. "
        "All must be ready and not share unmet dependencies.\n"
        "- context (string, optional but strongly recommended): extra task-shaped instructions. "
        "A good context includes: goal, scope boundaries, key files, constraints, expected output, verification command, and known pitfalls.\n"
        "- agent_spec (dict, optional): define a one-off role inline (id + prompts + tools). "
        "It extends agents/_base.json and overrides the task's agent_type. Use rarely.\n\n"
        "# Batch mode rules\n"
        "- Only batch tasks that are truly independent.\n"
        "- Do NOT batch tasks where one produces an artifact the other needs.\n"
        "- Do NOT batch more tasks than max_concurrent_spawns allows.\n\n"
        "# Common mistakes\n"
        "- Bad context: 'Fix the email thing.' (no file, no verification, no scope).\n"
        "- Good context: 'Implement validate_email in src/utils/validators.py. Read validate_phone for style. "
        "Raise ValueError on empty input, return lower-cased email, no new deps. Add tests in tests/test_validators.py. "
        "Run uv run pytest tests/test_validators.py -q and fix until green.'\n\n"
        "Returns the agent's output summary."
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
                "task_id": {
                    "type": "string",
                    "description": "Single task ID to execute (use this or task_ids).",
                },
                "task_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of task IDs to spawn in parallel (use this or task_id).",
                },
                "agent_spec": {
                    "type": "object",
                    "description": (
                        "Optional inline role definition for a one-off agent. "
                        "Keys: id (required), prompts (list of 'module:SYMBOL' refs), "
                        "tools (list, '+tool'/'-tool' deltas on the shared base), "
                        "pattern.config (e.g. {max_steps}). Extends agents/_base.json. "
                        "Only valid for single task_id."
                    ),
                },
                "context": {
                    "type": "string",
                    "description": (
                        "Optional extra context or instructions from the Director to the "
                        "spawned agent. Appended to the constructed task input."
                    ),
                },
                "verify": {
                    "type": "boolean",
                    "description": (
                        "After the agent completes, spawn a verifier to confirm the task is "
                        "actually done (files exist + match the requirement). Use for key "
                        "tasks that downstream work depends on. Default false."
                    ),
                },
            },
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        deps = getattr(context, "deps", None)
        board = getattr(deps, "state_board", None) if deps else None
        runner_delegate = getattr(deps, "runner_delegate", None) if deps else None
        runner = getattr(deps, "runner", None) if deps else None
        if board is None:
            raise PermanentToolError("StateBoard not available", tool_name=self.name)
        if runner_delegate is None:
            raise PermanentToolError("Runner not available", tool_name=self.name)

        task_ids = params.get("task_ids")
        task_id = str(params.get("task_id", "")).strip()
        agent_spec = params.get("agent_spec")
        extra_context = str(params.get("context", "")).strip()
        verify = bool(params.get("verify", False))
        agent_type_override: str | None = None
        if agent_spec:
            if task_ids:
                raise PermanentToolError(
                    "agent_spec is only valid for single task_id, not batch task_ids",
                    tool_name=self.name,
                )
            agent_type_override = self._compile_inline_agent(agent_spec, runner)

        # Batch mode
        if task_ids:
            return await self._spawn_batch(task_ids, board, runner_delegate, context, verify=verify)

        # Single mode
        if not task_id:
            raise PermanentToolError("task_id or task_ids is required", tool_name=self.name)
        return await self._spawn_single(
            task_id, board, runner_delegate, context,
            agent_type=agent_type_override, extra_context=extra_context, verify=verify,
        )

    @staticmethod
    def _compile_inline_agent(agent_spec: dict[str, Any], runner: Any) -> str:
        """Compile an inline agent_spec and register it on the runner.

        Mirrors the logic in sub_agent.SubAgentTool for one-off roles.
        """
        if not isinstance(agent_spec, dict):
            raise PermanentToolError("agent_spec must be an object", tool_name="spawn_agent")
        if runner is None:
            raise PermanentToolError(
                "agent_spec requires an orchestrator runner",
                tool_name="spawn_agent",
            )
        agents_dir = Path("agents")
        if hasattr(runner, "_config_path"):
            agents_dir = runner._config_path.parent / "agents"
        base = _load_json(agents_dir / "_base.json")
        try:
            agent_def = compile_one_spec(agent_spec, base=base)
        except AgentSpecError as exc:
            raise PermanentToolError(
                f"Failed to compile inline agent_spec: {exc}",
                tool_name="spawn_agent",
            ) from exc
        runner._agents_by_id[agent_def.id] = agent_def
        runner._bundles.pop(agent_def.id, None)
        return agent_def.id

    # -- single task ---------------------------------------------------------

    async def _spawn_single(
        self,
        task_id: str,
        board: Any,
        runner_delegate: Any,
        context: Any,
        *,
        agent_type: str | None = None,
        extra_context: str = "",
        verify: bool = False,
    ) -> dict[str, Any]:
        """Pure delegation: check readiness, run the agent, return the outcome.

        StateBoard updates (task status, agent status, artifacts, decision history)
        are handled by the ``pattern.after_execute`` hook registered on the runner.
        This tool only prepares the agent context and reports the result back to
        the Director.
        """
        task = board.get_task(task_id)
        if task is None:
            raise PermanentToolError(f"Task '{task_id}' not found", tool_name=self.name)

        effective_agent_type = agent_type or task.agent_type
        deps_completed = {
            t.task_id for t in board.tasks.values() if t.status == TaskStatus.COMPLETED
        }
        missing_deps = set(task.dependencies) - deps_completed
        if missing_deps:
            raise PermanentToolError(
                f"Task '{task_id}' has unmet dependencies: {sorted(missing_deps)}",
                tool_name=self.name,
            )

        input_text = self._build_input(
            task, board, agent_type=effective_agent_type, extra_context=extra_context
        )
        agent_id = f"{effective_agent_type}-{task_id}"

        if verify:
            board.update_task(task_id, needs_verify=True)
        board.register_agent(agent_id, effective_agent_type)
        board.update_task(task_id, status=TaskStatus.RUNNING)
        board.update_agent(
            agent_id,
            status=AgentStatus.RUNNING,
            current_task=task_id,
            start_time=time.time(),
        )
        board.log_event(
            "agent.spawned",
            task_id=task_id,
            agent_id=agent_id,
            message=f"Spawning {effective_agent_type} for {task_id}",
        )

        outcome = await runner_delegate(
            agent_type=effective_agent_type,
            input_text=input_text,
            agent_id=agent_id,
        )

        output = str(getattr(outcome, "output", "") or "")
        status_obj = getattr(outcome, "status", None)
        status_value = status_obj.value if hasattr(status_obj, "value") else str(status_obj)
        error = getattr(getattr(outcome, "error", None), "message", "") or ""

        # coder max_steps 时 ContinuationHooks 会在 after_execute 内续命，把 task 推进到
        # COMPLETED/FAILED（或仍 RUNNING）。仅此场景以 board 的 task 状态为准回报 director，
        # 否则它看到续命前的 max_steps，与 board（单一信源）不一致、误导调度。其他 outcome
        # 直接透传（保持既有语义，不影响 failed/completed/batch 路径）。
        if status_value == "max_steps":
            final_task = board.get_task(task_id)
            if final_task is not None:
                status_value = final_task.status.value

        return {
            "task_id": task_id,
            "agent_id": agent_id,
            "status": status_value,
            "output": output[:1000],
            "error": error,
        }

    # -- batch parallel ------------------------------------------------------

    async def _spawn_batch(
        self, task_ids: list[str], board: Any, runner_delegate: Any, context: Any,
        *, verify: bool = False,
    ) -> dict[str, Any]:
        deps_completed = {
            t.task_id for t in board.tasks.values() if t.status == TaskStatus.COMPLETED
        }
        not_ready = []
        for tid in task_ids:
            task = board.get_task(tid)
            if task is None:
                not_ready.append(f"'{tid}' not found")
            elif task.status != TaskStatus.PENDING:
                not_ready.append(f"'{tid}' status={task.status.value}")
            elif not task.is_ready(deps_completed):
                missing = set(task.dependencies) - deps_completed
                not_ready.append(f"'{tid}' missing deps: {sorted(missing)}")

        if not_ready:
            raise PermanentToolError(
                f"Some tasks are not ready: {not_ready}",
                tool_name=self.name,
            )

        async def _spawn_one(tid: str) -> dict[str, Any]:
            try:
                return await self._spawn_single(tid, board, runner_delegate, context, verify=verify)
            except Exception as exc:
                return {"task_id": tid, "status": "failed", "error": str(exc)}

        results = await asyncio.gather(*[_spawn_one(tid) for tid in task_ids])
        succeeded = sum(1 for r in results if r.get("status") == "completed")
        failed = len(results) - succeeded

        return {
            "total": len(task_ids),
            "succeeded": succeeded,
            "failed": failed,
            "results": results,
        }

    @staticmethod
    def _build_input(task: Any, board: Any, *, agent_type: str, extra_context: str = "") -> str:
        """Compose the full input text for a tactical agent.

        Includes: task description + input_context + dependency artifacts +
        pending messages + current working directory.
        """
        import os

        parts: list[str] = []

        # Working directory — critical for correct file placement
        cwd = os.getcwd()
        parts.append(f"# Working Directory\nAll file paths are relative to: {cwd}")
        if task.expected_artifacts:
            parts.append("Expected artifacts:")
            for art in task.expected_artifacts:
                abs_art = os.path.join(cwd, art) if not os.path.isabs(art) else art
                parts.append(f"  - {art}  (absolute: {abs_art})")

        # Core task
        parts.append(f"\n# Task: {task.description}")
        if task.input_context:
            parts.append(task.input_context)

        # Dependency context
        if task.dependencies:
            parts.append("\n# Upstream artifacts")
            for dep_id in task.dependencies:
                dep = board.get_task(dep_id)
                if dep:
                    arts = dep.actual_artifacts or dep.expected_artifacts
                    parts.append(f"- {dep_id}: {', '.join(arts) if arts else '(no artifacts)'}")
                    # If artifact file doesn't exist but we have result_output, inject it
                    for art_path in arts:
                        if not os.path.exists(art_path) and dep.result_output:
                            preview = dep.result_output[:2000]
                            suffix = "\n... (truncated)" if len(dep.result_output) > 2000 else ""
                            parts.append(
                                f"\n# Content of {art_path} (from {dep_id} output, "
                                f"file not yet on disk)\n{preview}{suffix}"
                            )
                            break  # Only inject first missing artifact to save tokens

        # Pending messages addressed to this task or its agent type
        relevant = board.messages_for(task.task_id)
        relevant += board.messages_for(agent_type)
        if relevant:
            parts.append("\n# Messages from other agents")
            for msg in relevant:
                parts.append(f"- From {msg['from']}: {msg['content'][:300]}")

        # Remind agent to check messages periodically
        parts.append(
            "\n# Communication reminder\n"
            "Call `send_message` when you need to talk to another agent, and check "
            "your mailbox (via available tools) every 3-5 turns for replies."
        )

        # Agent-specific hard constraints
        if agent_type == "coder":
            parts.append(CODER_CONSTRAINT)
        if agent_type == "reviewer":
            parts.append(REVIEWER_CONSTRAINT)

        # Director-provided extra context / instructions
        if extra_context:
            parts.append("\n# Additional instructions from the Director\n" + extra_context)

        return "\n\n".join(parts)


"""spawn_agent — launch a tactical agent to execute a task.

Supports two modes:
- Single task: task_id="t1"
- Batch parallel: task_ids=["t1", "t2", "t3"] (independent tasks only)
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any

from openagents.errors.exceptions import PermanentToolError, RetryableToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin

from openagents_orchestration.models.task import TaskStatus
from openagents_orchestration.state_board import AgentStatus


class SpawnAgentTool(ToolPlugin):
    """Execute pending task(s) by spawning tactical agent(s).

    Single task: provide task_id.
    Batch parallel: provide task_ids (all must be independent and ready).
    """

    name = "spawn_agent"
    description = (
        "Execute pending task(s) by spawning tactical agent(s). "
        "Single task: task_id. Batch parallel: task_ids (all must be independent and ready). "
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
            },
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        deps = getattr(context, "deps", None)
        board = getattr(deps, "state_board", None) if deps else None
        runner_delegate = getattr(deps, "runner_delegate", None) if deps else None
        if board is None:
            raise PermanentToolError("StateBoard not available", tool_name=self.name)
        if runner_delegate is None:
            raise PermanentToolError("Runner not available", tool_name=self.name)

        task_ids = params.get("task_ids")
        task_id = str(params.get("task_id", "")).strip()

        # Batch mode
        if task_ids:
            return await self._spawn_batch(task_ids, board, runner_delegate, context)

        # Single mode
        if not task_id:
            raise PermanentToolError("task_id or task_ids is required", tool_name=self.name)
        return await self._spawn_single(task_id, board, runner_delegate, context)

    # -- single task ---------------------------------------------------------

    async def _spawn_single(
        self, task_id: str, board: Any, runner_delegate: Any, context: Any
    ) -> dict[str, Any]:
        task = board.get_task(task_id)
        if task is None:
            raise PermanentToolError(f"Task '{task_id}' not found", tool_name=self.name)

        deps_completed = {
            t.task_id for t in board.tasks.values() if t.status == TaskStatus.COMPLETED
        }
        missing_deps = set(task.dependencies) - deps_completed
        if missing_deps:
            raise PermanentToolError(
                f"Task '{task_id}' has unmet dependencies: {sorted(missing_deps)}",
                tool_name=self.name,
            )

        input_text = self._build_input(task, board)
        agent_id = f"{task.agent_type}-{task_id}"

        board.register_agent(agent_id, task.agent_type)
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
            message=f"Spawning {task.agent_type} for {task_id}",
        )

        max_retries = 3
        retry_delay_base = 2.0
        result_text = ""
        last_exc: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                result_text = await runner_delegate(
                    agent_type=task.agent_type,
                    input_text=input_text,
                    agent_id=agent_id,
                )
                break
            except Exception as exc:
                last_exc = exc
                is_transient = self._is_transient_error(exc)
                board.update_agent(agent_id, status=AgentStatus.FAILED, end_time=time.time())

                if is_transient and attempt < max_retries:
                    delay = retry_delay_base * (2 ** attempt)
                    board.log_event(
                        "agent.retry",
                        task_id=task_id,
                        agent_id=agent_id,
                        message=f"Transient error, retrying in {delay:.0f}s (attempt {attempt + 1}/{max_retries})",
                    )
                    await asyncio.sleep(delay)
                    board.update_agent(agent_id, status=AgentStatus.RUNNING, retry_count=attempt + 1)
                    continue
                else:
                    break

        if last_exc is not None and not result_text:
            board.update_agent(agent_id, status=AgentStatus.FAILED, end_time=time.time())
            error_msg = str(last_exc)
            recommendation = self._classify_error(last_exc)

            agent_state = board.get_agent(agent_id)
            fallback_attempts = (agent_state.fallback_attempts if agent_state else 0) + 1
            board.update_agent(agent_id, fallback_attempts=fallback_attempts)

            if fallback_attempts >= 3:
                recommendation = "ask_human — task failed after 3 fallback attempts"

            enriched_error = f"{error_msg}  [recommendation: {recommendation}]"
            board.update_task(task_id, status=TaskStatus.FAILED, error=enriched_error)
            board.log_event(
                "agent.failed",
                task_id=task_id,
                agent_id=agent_id,
                message=enriched_error,
                recommendation=recommendation,
            )
            raise RetryableToolError(
                f"Agent failed for task '{task_id}': {enriched_error}",
                tool_name=self.name,
            ) from last_exc

        artifacts = self._extract_artifacts(result_text)
        board.claim_artifact(task_id, artifacts)

        # Verify each artifact actually exists on disk
        verified = []
        for art_path in artifacts:
            real = os.path.exists(art_path)
            board.verify_artifact(art_path, exists=real)
            if real:
                verified.append(art_path)

        board.update_agent(agent_id, status=AgentStatus.DONE, end_time=time.time())
        board.update_task(
            task_id,
            status=TaskStatus.COMPLETED,
            result_output=result_text,
            actual_artifacts=verified,
        )
        board.log_event(
            "agent.completed",
            task_id=task_id,
            agent_id=agent_id,
            message=f"Completed with {len(verified)}/{len(artifacts)} verified artifact(s)",
        )

        return {
            "task_id": task_id,
            "agent_id": agent_id,
            "status": "completed",
            "output": result_text[:1000],
            "artifacts": verified,
            "claimed": len(artifacts),
            "verified": len(verified),
        }

    # -- batch parallel ------------------------------------------------------

    async def _spawn_batch(
        self, task_ids: list[str], board: Any, runner_delegate: Any, context: Any
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
                return await self._spawn_single(tid, board, runner_delegate, context)
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
    def _is_transient_error(exc: Exception) -> bool:
        """Check if an error is transient (worth retrying)."""
        msg = str(exc).lower()
        return any(
            keyword in msg
            for keyword in ("timeout", "connection", "rate limit", "429", "too many requests")
        )

    @staticmethod
    def _classify_error(exc: Exception) -> str:
        """Classify an agent failure and recommend a recovery strategy."""
        msg = str(exc).lower()

        if "spawn resident" in msg or "resident coder" in msg:
            return "spawn resident — agent is stuck in a loop, use persistent resident"
        if "timeout" in msg:
            return "retry — timeout, likely transient"
        if "connection" in msg:
            return "retry — network error, likely transient"
        if "rate limit" in msg or "429" in msg or "too many requests" in msg:
            return "retry — rate limited, wait and retry"
        if "no such file" in msg or "file not found" in msg:
            return "replan — path/file error, task may be mis-specified"
        if "permission" in msg or "denied" in msg:
            return "ask_human — permission issue"
        if "invalid" in msg or "syntax" in msg or "parse" in msg:
            return "replan — specification error, break into smaller tasks"
        if "memory" in msg or "oom" in msg or "out of memory" in msg:
            return "replan — task too large, decompose further"
        return "replan or ask_human — unknown error type"

    @staticmethod
    def _build_input(task: Any, board: Any) -> str:
        """Compose the full input text for a tactical agent.

        Includes: task description + input_context + dependency artifacts +
        pending messages.
        """
        parts: list[str] = []

        # Core task
        parts.append(f"# Task: {task.description}")
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
        relevant += board.messages_for(task.agent_type)
        if relevant:
            parts.append("\n# Messages from other agents")
            for msg in relevant:
                parts.append(f"- From {msg['from']}: {msg['content'][:300]}")

        # Remind agent to check messages periodically
        parts.append(
            "\n# Communication reminder\n"
            "You have the check_messages tool. Call it every 3-5 turns to see "
            "if the director or other agents have sent you messages."
        )

        return "\n\n".join(parts)

    @staticmethod
    def _extract_artifacts(output: str) -> list[str]:
        """Parse FILES_CREATED / FILES_MODIFIED markers from agent output."""
        artifacts: list[str] = []
        for key in ("FILES_CREATED", "FILES_MODIFIED"):
            for line in output.splitlines():
                if line.strip().startswith(key):
                    rest = line.split(":", 1)[1] if ":" in line else ""
                    paths = [p.strip() for p in rest.split(",") if p.strip()]
                    artifacts.extend(paths)
        # Also look for standalone file paths with extensions
        file_pattern = re.compile(r"[\w\-/\\]+\.[a-zA-Z0-9_]{1,10}")
        for match in file_pattern.finditer(output):
            path = match.group(0)
            if path not in artifacts and "/" in path:
                artifacts.append(path)
        return list(dict.fromkeys(artifacts))  # dedupe preserving order

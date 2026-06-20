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
from pathlib import Path
from typing import Any

from openagents.errors.exceptions import PermanentToolError, RetryableToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin

from openagents_orchestration.core.state_board import AgentStatus
from openagents_orchestration.models.task import TaskStatus
from openagents_orchestration.reporting import summarize_agent_run
from openagents_orchestration.core.agent_loader import (
    AgentSpecError,
    _load_json,
    compile_one_spec,
)
from prompts.agent_constraints import CODER_CONSTRAINT, REVIEWER_CONSTRAINT
from prompts.corrections import build_hallucination_correction


class SpawnAgentTool(ToolPlugin):
    """Execute pending task(s) by spawning tactical agent(s).

    Single task: provide task_id.
    Batch parallel: provide task_ids (all must be independent and ready).
    """

    name = "spawn_agent"
    description = (
        "Execute pending task(s) by spawning tactical agent(s). "
        "Single task: task_id. Batch parallel: task_ids (all must be independent and ready). "
        "You may also provide agent_spec to define a one-off agent inline "
        "(id + prompts + tools, extends agents/_base.json); it overrides the task's agent_type. "
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
            return await self._spawn_batch(task_ids, board, runner_delegate, context)

        # Single mode
        if not task_id:
            raise PermanentToolError("task_id or task_ids is required", tool_name=self.name)
        return await self._spawn_single(
            task_id, board, runner_delegate, context, agent_type=agent_type_override
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
    ) -> dict[str, Any]:
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

        input_text = self._build_input(task, board, agent_type=effective_agent_type)
        agent_id = f"{effective_agent_type}-{task_id}"

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

        max_retries = 3
        retry_delay_base = 2.0
        result_text = ""
        last_exc: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                result_text = await runner_delegate(
                    agent_type=effective_agent_type,
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
            summary = summarize_agent_run(
                agent_id=agent_id,
                task_id=task_id,
                status="failed",
                error=enriched_error,
                artifacts=task.actual_artifacts or task.expected_artifacts,
                retry_count=agent_state.retry_count if agent_state else 0,
                steps_used=agent_state.steps_used if agent_state else 0,
                token_used=agent_state.token_used if agent_state else 0,
            )
            board.log_event(
                "agent.run_summary",
                task_id=task_id,
                agent_id=agent_id,
                message=f"failed: {summary['failure_type']}",
                summary=summary,
            )
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

        # ---- failure guard ----
        # If the agent already failed (e.g. step budget exhausted), do not mark COMPLETED.
        agent_state = board.get_agent(agent_id)
        if agent_state is not None and agent_state.status == AgentStatus.FAILED:
            error_msg = task.error or f"Agent {agent_id} failed before completing the task"
            board.update_task(task_id, status=TaskStatus.FAILED, error=error_msg)
            summary = summarize_agent_run(
                agent_id=agent_id,
                task_id=task_id,
                status="failed",
                error=error_msg,
                artifacts=task.actual_artifacts or task.expected_artifacts,
                retry_count=agent_state.retry_count,
                steps_used=agent_state.steps_used,
                token_used=agent_state.token_used,
            )
            board.log_event(
                "agent.run_summary",
                task_id=task_id,
                agent_id=agent_id,
                message=f"failed: {summary['failure_type']}",
                summary=summary,
            )
            board.log_event(
                "agent.failed",
                task_id=task_id,
                agent_id=agent_id,
                message=error_msg,
            )
            raise RetryableToolError(
                f"Agent failed for task '{task_id}': {error_msg}",
                tool_name=self.name,
            )

        # ---- coder hallucination guard ----
        # If expected artifacts still contain TODO/placeholder/pass, force a retry
        # regardless of what the coder claimed. The coder may hallucinate that a
        # file is "already implemented" when it only contains a skeleton.
        if effective_agent_type == "coder" and result_text:
            for art_path in (task.expected_artifacts or []):
                if os.path.exists(art_path):
                    content = Path(art_path).read_text()
                    if any(k in content for k in ("TODO", "placeholder", "pass\n", "# TODO")):
                        board.log_event(
                            "agent.hallucination_detected",
                            task_id=task_id,
                            agent_id=agent_id,
                            message=f"File {art_path} still contains placeholder, forcing retry",
                        )
                        import sys
                        print(
                            f"[Orchestrator] Hallucination detected for {agent_id}: "
                            f"{art_path} still contains placeholder. Forcing retry.",
                            file=sys.stderr,
                            flush=True,
                        )
                        correction = build_hallucination_correction(art_path, content)
                        result_text = await runner_delegate(
                            agent_type=effective_agent_type,
                            input_text=correction,
                            agent_id=agent_id,
                        )
                        break

        artifacts = self._extract_artifacts(result_text)
        board.claim_artifact(task_id, artifacts)

        # Verify each artifact against the orchestration work_dir first.
        verified = []
        runner = getattr(getattr(context, "deps", None), "runner", None)
        work_dir = Path(getattr(runner, "_current_work_dir", "") or ".")
        for art_path in artifacts:
            resolved = self._resolve_artifact_path(art_path, work_dir)
            if resolved is None:
                continue
            real = resolved.exists() and resolved.stat().st_size > 0
            board.verify_artifact(art_path, exists=real)
            if real:
                verified.append(art_path)

        board.update_agent(agent_id, status=AgentStatus.DONE, end_time=time.time())

        # Only set task to COMPLETED if _spawn_and_run hasn't already done it.
        # _spawn_and_run marks the task completed on StopReason.COMPLETED, but
        # this tool post-processes the result with artifact verification, so
        # update the artifacts even if the status is already terminal.
        task = board.get_task(task_id)
        if task is not None and task.status != TaskStatus.COMPLETED:
            board.update_task(
                task_id,
                status=TaskStatus.COMPLETED,
                result_output=result_text,
                actual_artifacts=verified,
            )
        elif task is not None:
            # Task already completed — just backfill verified artifacts.  Keep a
            # richer result_output that runner/team-leader may have written
            # instead of overwriting it with a generic tool-loop termination.
            updates = {"actual_artifacts": verified, "_force": True}
            existing_output = str(getattr(task, "result_output", "") or "").strip()
            if not existing_output:
                updates["result_output"] = result_text
            board.update_task(task_id, **updates)
        agent_state = board.get_agent(agent_id)
        summary = summarize_agent_run(
            agent_id=agent_id,
            task_id=task_id,
            status="completed",
            output=result_text,
            artifacts=verified,
            retry_count=agent_state.retry_count if agent_state else 0,
            steps_used=agent_state.steps_used if agent_state else 0,
            token_used=agent_state.token_used if agent_state else 0,
        )
        board.log_event(
            "agent.run_summary",
            task_id=task_id,
            agent_id=agent_id,
            message="completed",
            summary=summary,
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
            for keyword in (
                "server disconnected",
                "remoteprotocolerror",
                "connection reset",
                "connection aborted",
                "temporarily unavailable",
                "timeout",
                "connection",
                "rate limit",
                "429",
                "too many requests",
                "http 500",
                "http 502",
                "http 503",
                "http 504",
                "http 520",
                "http 522",
                "http 524",
                "bad gateway",
                "service unavailable",
                "gateway timeout",
                "web server is returning an unknown error",
            )
        )

    @staticmethod
    def _classify_error(exc: Exception) -> str:
        """Classify an agent failure and recommend a recovery strategy."""
        msg = str(exc).lower()

        if "spawn resident" in msg or "resident coder" in msg:
            return "spawn resident — agent is stuck in a loop, use persistent resident"
        if any(signal in msg for signal in ("server disconnected", "remoteprotocolerror", "connection reset", "connection aborted", "temporarily unavailable")):
            return "retry — upstream connection dropped, likely transient"
        if "timeout" in msg:
            return "retry — timeout, likely transient"
        if "connection" in msg:
            return "retry — network error, likely transient"
        if "rate limit" in msg or "429" in msg or "too many requests" in msg:
            return "retry — rate limited, wait and retry"
        if any(
            signal in msg
            for signal in (
                "http 500",
                "http 502",
                "http 503",
                "http 504",
                "http 520",
                "http 522",
                "http 524",
                "bad gateway",
                "service unavailable",
                "gateway timeout",
                "web server is returning an unknown error",
            )
        ):
            return "retry — upstream API/server error, likely transient"
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
    def _build_input(task: Any, board: Any, *, agent_type: str) -> str:
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
            "You have the check_messages tool. Call it every 3-5 turns to see "
            "if the director or other agents have sent you messages."
        )

        # Agent-specific hard constraints
        if agent_type == "coder":
            parts.append(CODER_CONSTRAINT)
        if agent_type == "reviewer":
            parts.append(REVIEWER_CONSTRAINT)

        return "\n\n".join(parts)

    @staticmethod
    def _resolve_artifact_path(artifact_path: str, work_dir: Path) -> Path | None:
        raw = str(artifact_path or "").strip()
        if not raw or any(ch in raw for ch in ("\n", "\r")):
            return None
        if " " in raw or raw.startswith(("bash:", "pytest:")):
            return None

        path = Path(raw)
        if path.is_absolute():
            return path

        candidates = [work_dir / path]
        parts = path.parts
        work_name = work_dir.name
        if parts and parts[0].lstrip(".") == work_name.lstrip("."):
            candidates.append(work_dir.joinpath(*parts[1:]))
        candidates.append(Path.cwd() / path)

        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0]

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

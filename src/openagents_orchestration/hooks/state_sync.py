"""StateSyncHooks — Pattern events → StateBoard mutations.

This module contains the default handlers that keep ``StateBoard`` in sync
with Pattern execution. It is the glue described in the pattern-centric
architecture: Patterns emit events; these handlers translate the events into
``StateBoard`` method calls.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from openagents_orchestration.core.decision_history import DecisionRecord
from openagents_orchestration.core.state_board import StateBoard
from openagents_orchestration.models.pattern import PatternOutcomeStatus
from openagents_orchestration.utils.agent_id import infer_task_id
from openagents_orchestration.utils.runtime_compat import extract_result_error_message


class StateSyncHooks:
    """Default hook handlers that mirror Pattern events into ``StateBoard``.

    The handler methods are intentionally coarse-grained: one handler per
    lifecycle event rather than one per field, so the event → mutation mapping
    is easy to trace.
    """

    def __init__(self, board: StateBoard | None = None) -> None:
        self.board = board

    def pattern_after_execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply a ``PatternOutcome`` to task and agent state."""
        board = self.board
        if board is None:
            return payload

        outcome = payload.get("outcome")
        agent_id = payload.get("agent_id")
        agent_type = payload.get("agent_type")
        if outcome is None or agent_id is None or agent_type is None:
            return payload

        task_id = payload.get("task_id")
        if task_id is None and isinstance(agent_id, str):
            task_id = infer_task_id(agent_id)

        result = payload.get("result")
        sub_board = payload.get("sub_board")

        # Agent resource accounting
        usage = getattr(outcome, "usage", None)
        tokens = getattr(usage, "total_tokens", 0) or 0
        metadata = getattr(outcome, "metadata", {}) or {}
        steps = metadata.get("steps_used", 0) if isinstance(metadata, dict) else 0
        if agent_id in board.agents:
            board.update_agent(agent_id, token_used=tokens, steps_used=steps)

        # Apply outcome to task + agent state
        board.apply_outcome(outcome, task_id=task_id, agent_id=agent_id, agent_type=agent_type)

        # Cache outcome status for artifact + decision logic below.
        status = getattr(outcome, "status", None)

        # ── artifact 核验层：claimed ≠ verified ──
        # 对完成型 coder 任务，把 director 声明的 expected_artifacts 落地核验。
        # task 是否标 COMPLETED 仍由 apply_outcome 决定；此处只记录真相到 StateBoard，
        # 供 Director snapshot 判断，不阻塞完成（VerifyHooks 负责更严格的二值核验）。
        all_artifact_paths: list[str] = []
        if (
            agent_type == "coder"
            and task_id
            and status == PatternOutcomeStatus.COMPLETED
        ):
            task = board.get_task(task_id)
            if task is not None:
                work_dir = payload.get("work_dir")
                expected = list(getattr(task, "expected_artifacts", []) or [])
                if expected:
                    board.claim_artifact(task_id, expected)
                for art_path in expected:
                    if not art_path:
                        continue
                    path_str = str(art_path)
                    if path_str not in all_artifact_paths:
                        all_artifact_paths.append(path_str)
                    resolved = self._resolve_artifact_path(path_str, work_dir)
                    exists = (
                        resolved is not None
                        and resolved.exists()
                        and resolved.stat().st_size > 0
                    )
                    board.verify_artifact(path_str, exists=exists)

        # Team leader sub-board budget merge
        if agent_type == "team_leader" and sub_board is not None and hasattr(sub_board, "budget"):
            sub_budget = sub_board.budget
            board.add_tokens(getattr(sub_budget, "token_used", 0))
            board.add_steps(getattr(sub_budget, "steps_taken", 0))
            board.log_event(
                "team.budget_merged",
                agent_id=agent_id,
                message=f"tokens={getattr(sub_budget, 'token_used', 0)}, steps={getattr(sub_budget, 'steps_taken', 0)}",
            )

        # Decision history
        if status == PatternOutcomeStatus.COMPLETED:
            board.decision_history.record(
                DecisionRecord(
                    decision_type="spawn_agent",
                    task_id=task_id or "",
                    agent_id=agent_id,
                    agent_type=agent_type,
                    reasoning=f"Spawned {agent_type} for task {task_id}",
                    outcome="completed",
                    artifacts_produced=all_artifact_paths,
                    token_spent=tokens,
                    steps_used=int(steps) if isinstance(steps, int) else 0,
                )
            )
        elif status in (PatternOutcomeStatus.FAILED, PatternOutcomeStatus.MAX_STEPS):
            error_msg = ""
            if result is not None:
                error_msg = extract_result_error_message(result) or ""
            if status == PatternOutcomeStatus.MAX_STEPS and not error_msg:
                error_msg = "step budget exhausted"
            board.decision_history.record(
                DecisionRecord(
                    decision_type="spawn_agent",
                    task_id=task_id or "",
                    agent_id=agent_id,
                    agent_type=agent_type,
                    reasoning=f"Spawned {agent_type} for task {task_id}",
                    outcome="failed",
                    error=error_msg[:300],
                    token_spent=tokens,
                    steps_used=int(steps) if isinstance(steps, int) else 0,
                )
            )

        return payload

    def llm_after_call(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Record LLM call metrics on the agent."""
        board = self.board
        agent_id = payload.get("agent_id")
        metrics = payload.get("metrics")
        if board is None or agent_id is None or metrics is None:
            return payload
        if hasattr(board, "record_sdk_metrics"):
            board.record_sdk_metrics(agent_id, metrics)
        return payload

    def artifact_claimed(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Record claimed artifacts for a task."""
        board = self.board
        task_id = payload.get("task_id")
        paths = payload.get("paths")
        if board is None or not task_id or not paths:
            return payload
        board.claim_artifact(task_id, list(paths))
        return payload

    def artifact_verified(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Record artifact verification result."""
        board = self.board
        path = payload.get("path")
        exists = payload.get("exists", True)
        if board is None or not path:
            return payload
        board.verify_artifact(str(path), exists=bool(exists))
        return payload

    def agent_registered(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Register a new agent on the board."""
        board = self.board
        agent_id = payload.get("agent_id")
        agent_type = payload.get("agent_type")
        if board is None or not agent_id:
            return payload
        board.register_agent(agent_id, agent_type or "unknown")
        return payload

    def state_transition(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Log a state transition event."""
        board = self.board
        if board is None:
            return payload
        board.log_event(
            "state.transition",
            task_id=payload.get("task_id"),
            agent_id=payload.get("agent_id"),
            message=f"{payload.get('entity')} {payload.get('from_state')} -> {payload.get('to_state')}",
        )
        return payload

    @staticmethod
    def _resolve_artifact_path(path: str, work_dir: Any) -> Path | None:
        """Resolve an artifact path relative to the current work directory."""
        if not path:
            return None
        p = Path(path)
        if p.is_absolute():
            return p
        cwd = Path(work_dir) if work_dir else Path.cwd()
        resolved = (cwd / p).resolve()
        return resolved

    @staticmethod
    def _extract_files_created(output: str) -> list[str]:
        """Parse FILES_CREATED markers from legacy agent output."""
        paths: list[str] = []
        for line in output.splitlines():
            match = re.search(r"FILES_CREATED:\s*(.+)", line)
            if match:
                for raw in match.group(1).split(","):
                    path = raw.strip().strip("\"'")
                    if path and " " not in path:
                        paths.append(path)
        return paths

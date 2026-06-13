"""OrchestratorRunner — multi-agent runner built on CoreCoder infrastructure.

Extends CoreCoderLocalRunner patterns to support:
- Multiple agent types (director + tactical agents)
- StateBoard for global coordination
- Agent-to-agent messaging
- Async event bus for observability
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openagents.config.loader import load_config
from openagents.errors.exceptions import ConfigError
from openagents.interfaces.events import RuntimeEvent
from openagents.interfaces.runtime import (
    RunBudget,
    RunRequest,
    RunResult,
    RunUsage,
    StopReason,
)
from openagents.llm.registry import create_llm_client
from openagents.plugins.builtin.events.async_event_bus import AsyncEventBus
from openagents.plugins.loader import LoadedAgentPlugins, load_agent_plugins
from pydantic import BaseModel, Field

from openagents_orchestration.store.artifact_store import ArtifactStore, LocalArtifactStore
from openagents_orchestration.core.collaboration import (
    CollaborationSignal,
    collaboration_message_from_structured,
)
from openagents_orchestration.core.collaboration_state_machine import (
    CollaborationAction,
    CollaborationDecision,
    CollaborationStateMachine,
)
from openagents_orchestration.core.collaboration_executor import (
    CollaborationDecisionExecutor,
)
from openagents_orchestration.core.resident_prompts import get_prompt
from openagents_orchestration.intent_classifier import IntentClassifier, IntentResult
from openagents_orchestration.transport.matrix_transport import (
    MatrixClient,
    MatrixConfig,
    MatrixTransport,
)
from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus
from openagents_orchestration.persistence import (
    EventRecorder,
    SessionResumer,
    StateSnapshotter,
)
from openagents_orchestration.enterprise.project import Project
from openagents_orchestration.reporting import (
    build_verification_report,
    summarize_agent_run,
    summarize_board,
)
from openagents_orchestration.core.resident import ResidentAgent
from openagents_orchestration.core.decision_history import DecisionRecord
from openagents_orchestration.core.state_board import AgentStatus, Budget, StateBoard
from openagents_orchestration.core.sub_state_board import SubStateBoard
from openagents_orchestration.utils.runtime_compat import (
    apply_sdk_patches,
    extract_result_error_message,
    patch_tool_capabilities,
    run_result_error_kwargs,
)
from openagents_orchestration.utils.structured_generate import structured_generate

apply_sdk_patches()
patch_tool_capabilities()


class _AgentScopedEventBus:
    """Wraps the global event bus to inject agent_id into every SDK emit."""

    def __init__(self, inner: Any, agent_id: str):
        self._inner = inner
        self._agent_id = agent_id

    async def emit(self, event_name: str, **payload: Any) -> Any:
        payload["agent_id"] = self._agent_id
        return await self._inner.emit(event_name, **payload)

    def subscribe(self, event_name: str, handler: Any) -> None:
        return self._inner.subscribe(event_name, handler)

    @property
    def history(self) -> list[Any]:
        return self._inner.history


@dataclass
class _AgentBundle:
    agent: Any
    plugins: LoadedAgentPlugins
    llm_client: Any


@dataclass
class _SessionStore:
    """In-memory session store with bounded transcript retention."""

    MAX_MESSAGES = 2000  # prevent unbounded transcript growth

    _messages: dict[str, list[dict[str, Any]]] = None
    _artifacts: dict[str, list[Any]] = None

    def __post_init__(self):
        self._messages = {}
        self._artifacts = {}

    async def load_messages(self, session_id: str) -> list[dict[str, Any]]:
        return list(self._messages.get(session_id, []))

    async def list_artifacts(self, session_id: str) -> list[Any]:
        return list(self._artifacts.get(session_id, []))

    def save(self, session_id: str, *, messages: list[dict[str, Any]], artifacts: list[Any]) -> None:
        # Keep only the most recent messages to prevent unbounded growth
        if len(messages) > self.MAX_MESSAGES:
            # Preserve system/user messages from the start for context,
            # keep the most recent turns for continuity
            system_msgs = [m for m in messages if m.get("role") == "system"]
            tail = messages[-(self.MAX_MESSAGES - len(system_msgs)):]
            messages = system_msgs + tail
        self._messages[session_id] = list(messages)
        self._artifacts[session_id] = list(artifacts)


@dataclass
class RunnerDeps:
    """Dependencies passed to agent context for cross-cutting concerns."""

    state_board: StateBoard
    runner_delegate: Any  # callable: (agent_type, input_text, agent_id=None) -> str
    runner: Any  # OrchestratorRunner reference for resident management
    artifact_store: ArtifactStore | None = None  # shared artifact storage for inter-agent exchange
    matrix_transport: Any | None = None  # optional Matrix transport backend


class OrchestratorRunner:
    """Multi-agent orchestration runner.

    Usage:
        runner = OrchestratorRunner("agent.json")
        report = await runner.run("Build a FastAPI TODO API")
    """

    def __init__(
        self,
        config_path: str | Path,
        *,
        persist_dir: str | None = None,
        collaborative_mode: str = "auto",
        enable_monitor_resident: bool = True,
        max_concurrent_spawns: int = 3,
        max_concurrent_residents: int = 4,
        resident_stuck_threshold_s: float = 120.0,
    ):
        if collaborative_mode not in {"auto", "on", "off"}:
            raise ValueError("collaborative_mode must be one of: auto, on, off")
        self._collaborative_mode = collaborative_mode
        self._max_concurrent_spawns = max_concurrent_spawns
        self._max_concurrent_residents = max_concurrent_residents
        self._resident_stuck_threshold_s = resident_stuck_threshold_s
        self._config_path = Path(config_path)
        self._config = load_config(self._config_path)
        self._agents_by_id = {a.id: a for a in self._config.agents}
        self._bundles: dict[str, _AgentBundle] = {}
        self._residents: dict[str, ResidentAgent] = {}
        self._sessions = _SessionStore()
        # Use events config from agent.json if present
        events_config = getattr(self._config, "events", None)
        if events_config and isinstance(events_config, dict):
            self._event_bus = AsyncEventBus(config=events_config.get("config", {}))
        else:
            self._event_bus = AsyncEventBus()
        self._state_board: StateBoard | None = None
        self._project: Project | None = None
        self._deps: RunnerDeps | None = None
        self._spawn_sem = asyncio.Semaphore(max_concurrent_spawns)
        self._monitor_resident_id: str | None = None
        self._enable_monitor_resident = enable_monitor_resident
        self._collab_wake_event = asyncio.Event()  # signals collaboration loop to wake
        # Persistence layer
        self._persist_dir = Path(persist_dir) if persist_dir else None
        self._session_id: str | None = None
        self._recorder: EventRecorder | None = None
        self._snapshotter: StateSnapshotter | None = None
        self._resumer: SessionResumer | None = None
        self._session_dir: Path | None = None
        self._current_work_dir: Path | None = None

    @property
    def state_board(self) -> StateBoard | None:
        return self._state_board

    @property
    def project(self) -> Project | None:
        return self._project

    def _create_project(
        self,
        objective: str,
        budget: Budget | None = None,
        *,
        work_dir: Path | None = None,
    ) -> Project:
        """Create the default Project for this runner."""
        project = Project(
            objective=objective,
            budget=budget,
            work_dir=work_dir,
        )
        self._project = project
        self._state_board = project.state_board
        return project

    async def _init_matrix_transport(self) -> MatrixTransport | None:
        """Initialize Matrix transport from environment if configured."""
        homeserver = os.environ.get("MATRIX_HOMESERVER", "").strip()
        user_id = os.environ.get("MATRIX_USER_ID", "").strip()
        access_token = os.environ.get("MATRIX_ACCESS_TOKEN", "").strip()
        if not homeserver or not user_id or not access_token:
            return None
        config = MatrixConfig(
            homeserver=homeserver,
            user_id=user_id,
            access_token=access_token,
        )
        client = MatrixClient(config)
        try:
            # Verify connection with a quick sync
            await client.sync(timeout_ms=1000)
            print(
                f"[Orchestrator] Matrix transport enabled: {user_id} @ {homeserver}",
                file=sys.stderr,
                flush=True,
            )
            return MatrixTransport(client)
        except Exception as exc:
            # Degrade gracefully: log and close partial connection
            print(
                f"[Orchestrator] Matrix transport init failed: {exc}; continuing without Matrix.",
                file=sys.stderr,
                flush=True,
            )
            try:
                await client.close()
            except Exception:
                pass
            return None

    # -- public API ----------------------------------------------------------

    async def run(
        self,
        objective: str,
        *,
        session_id: str | None = None,
        resume: bool = False,
        budget: Budget | None = None,
        work_dir: str | Path | None = None,
    ) -> Any:
        """Run full orchestration for an objective.

        Args:
            objective: The high-level goal.
            session_id: Optional session ID for persistence. If not provided,
                a new UUID is generated.
            resume: If True and a persisted session with ``session_id`` exists,
                load it and continue from the saved state.
            budget: Optional custom budget. Defaults to 500k tokens, 1800s, 100 steps.
            work_dir: Optional working directory. All spawned agents run inside
                this directory so relative paths resolve correctly.

        Returns DeliveryReport.
        """
        import contextlib
        import sys

        self._current_work_dir = Path(work_dir) if work_dir else Path.cwd()
        self._session_id = session_id or f"session-{uuid.uuid4().hex[:8]}"
        print(f"\n[Orchestrator] Starting: {objective}", file=sys.stderr, flush=True)
        print(f"[Orchestrator] Session: {self._session_id}", file=sys.stderr, flush=True)
        if work_dir:
            print(f"[Orchestrator] Work dir: {work_dir}", file=sys.stderr, flush=True)

        # Change to work_dir if provided; restore on exit
        cm = contextlib.chdir(work_dir) if work_dir else contextlib.nullcontext()
        with cm:
            # Initialize persistence layer
            if self._persist_dir is not None:
                self._session_dir = self._persist_dir / self._session_id
                self._session_dir.mkdir(parents=True, exist_ok=True)
                self._recorder = EventRecorder(self._session_dir, self._session_id)
                self._snapshotter = StateSnapshotter(self._session_dir / "snapshots")
                self._resumer = SessionResumer(self._persist_dir)
                print(
                    f"[Orchestrator] Persistence: {self._session_dir}",
                    file=sys.stderr,
                    flush=True,
                )

            # -- resume path -------------------------------------------------------
            if resume and self._resumer is not None:
                loaded = self._resumer.load(self._session_id)
                if loaded.snapshot is not None:
                    print(
                        "[Orchestrator] Resuming from snapshot...",
                        file=sys.stderr,
                        flush=True,
                    )
                    # Detect snapshot format: new Project format has "state_board" key
                    if "state_board" in loaded.snapshot:
                        project = Project.from_dict(loaded.snapshot)
                    else:
                        # Legacy StateBoard snapshot: wrap into a default Project
                        state_board = StateBoard.from_dict(
                            loaded.snapshot,
                            recorder=self._recorder,
                            snapshotter=self._snapshotter,
                            mailbox_backend=os.environ.get("MAILBOX_BACKEND", "memory"),
                            redis_url=os.environ.get("REDIS_URL"),
                        )
                        project = Project(
                            objective=state_board.objective,
                            budget=state_board.budget,
                            state_board=state_board,
                        )
                    self._project = project
                    self._state_board = project.state_board
                    # Re-attach persistence hooks
                    if self._state_board is not None:
                        self._state_board._recorder = self._recorder
                        self._state_board._snapshotter = self._snapshotter
                    # Replay events after snapshot
                    if loaded.events_after and self._state_board is not None:
                        from openagents_orchestration.persistence import EventReplayer
                        EventReplayer().replay(self._state_board, loaded.events_after)
                        print(
                            f"[Orchestrator] Replayed {len(loaded.events_after)} event(s)",
                            file=sys.stderr,
                            flush=True,
                        )
                    print(
                        f"[Orchestrator] Resumed: {self._state_board.progress_summary()}",
                        file=sys.stderr,
                        flush=True,
                    )
                    # Skip decomposition — tasks already loaded
                    return await self._continue_run(objective)

            # -- fresh run path ----------------------------------------------------
            # 1. Intent classification
            print("[Orchestrator] Classifying intent...", file=sys.stderr, flush=True)
            intent = await self._classify_intent(objective)
            print(
                f"[Orchestrator] Intent: {intent.task_type}/{intent.complexity}, "
                f"ext={intent.external}, conf={intent.confidence:.2f}",
                file=sys.stderr, flush=True,
            )

            # 2. Initial decomposition (intent-guided)
            print("[Orchestrator] Decomposing objective into tasks...", file=sys.stderr, flush=True)
            task_graph = await self._initial_decompose(objective)
            print(f"[Orchestrator] Decomposed into {len(task_graph.tasks)} task(s)", file=sys.stderr, flush=True)

            # 3. Project + StateBoard (with persistence hooks + mailbox backend)
            project_budget = budget or Budget(
                token_limit=500_000,
                time_limit_s=1800.0,
                max_steps=100,
            )
            project = self._create_project(
                objective=objective,
                budget=project_budget,
                work_dir=self._current_work_dir,
            )
            # Wire persistence hooks into the project's StateBoard
            project.state_board._recorder = self._recorder
            project.state_board._snapshotter = self._snapshotter
            await project.state_board.validate_redis()
            project.state_board.add_tasks(task_graph)
            project.start()

            return await self._continue_run(objective, intent=intent)

    async def _continue_run(self, objective: str, *, intent: IntentResult | None = None) -> Any:
        """Continue orchestration from an initialized StateBoard."""
        import sys

        if self._state_board is None:
            raise RuntimeError("StateBoard was not initialized")

        # 1. Bridge SDK event bus → StateBoard (for behavioral profiling)
        self._bridge_sdk_events()

        # 2. Spawn observer resident
        await self._spawn_monitor_resident()

        # 3. Deps for tools
        artifact_store: ArtifactStore | None = None
        if self._current_work_dir is not None:
            store_dir = self._current_work_dir / ".artifacts"
            artifact_store = LocalArtifactStore(store_dir)
        matrix_transport = await self._init_matrix_transport()
        self._deps = RunnerDeps(
            state_board=self._state_board,
            runner_delegate=self.run_agent,
            runner=self,
            artifact_store=artifact_store,
            matrix_transport=matrix_transport,
        )

        # 4. Wire collaboration wake hook into StateBoard's mail-sent path.
        # Without this, the collaboration loop relies solely on 30s polling
        # timeouts because resident _send_reply (via send_structured) doesn't
        # wake the loop.
        if self._state_board is not None:
            self._state_board.on_mail_sent(lambda: self._collab_wake_event.set())

        # 5. Choose execution mode based on task characteristics
        # Collaborative mode: resident agents work together iteratively
        # Director mode: Director makes every decision (fallback)
        use_collaborative = self._should_use_collaborative_mode()

        if use_collaborative:
            print("[Orchestrator] Collaborative mode: resident agents will iterate directly.", file=sys.stderr, flush=True)
            try:
                await self._run_collaborative()
            except Exception as exc:
                print(f"[Orchestrator] Collaborative mode failed: {exc}, falling back to Director.", file=sys.stderr, flush=True)
                self._state_board.log_event("orchestrator.collab_failed", message=str(exc))
                await self._run_director_mode(objective, intent=intent)
            # If collaborative mode exited with unfinished work, let the Director
            # recover failed tasks or drive remaining pending tasks.
            if (
                self._state_board is not None
                and not self._state_board.all_terminal()
                and not self._state_board.budget.exhausted
            ):
                pending = [t.task_id for t in self._state_board.tasks.values() if not t.is_terminal()]
                print(
                    f"[Orchestrator] Collaborative loop finished with unfinished tasks {pending}; "
                    "falling back to Director.",
                    file=sys.stderr, flush=True,
                )
                self._state_board.log_event(
                    "orchestrator.collab_fallback_director",
                    message=f"Unfinished tasks: {pending}",
                )
                await self._run_director_mode(objective, intent=intent)
        else:
            await self._run_director_mode(objective, intent=intent)

        # Auto-finalize if orchestration exited without a proper finalize
        if not self._state_board._final_summary:
            budget = self._state_board.budget
            progress = self._state_board.progress_summary()
            if budget.exhausted:
                reason = "Budget exhausted"
                if budget.token_remaining <= 0:
                    reason += f" (tokens {budget.token_used}/{budget.token_limit})"
                elif budget.time_remaining_s <= 0:
                    reason += f" (time elapsed {round(time.time() - budget.start_time, 0)}s / {budget.time_limit_s}s)"
                elif budget.steps_taken >= budget.max_steps:
                    reason += f" (steps {budget.steps_taken}/{budget.max_steps})"
            else:
                reason = "Orchestration exited without finalize"
            summary = (
                f"[{reason}] Orchestration stopped. "
                f"Completed: {progress['completed_tasks']}/{progress['total_tasks']}, "
                f"Failed: {progress['failed_tasks']}, "
                f"Skipped: {progress['skipped_tasks']}. "
                f"Token used: {budget.token_used}/{budget.token_limit}."
            )
            if progress['failed_tasks'] > 0:
                failed_ids = [
                    t.task_id for t in self._state_board.tasks.values()
                    if t.status == TaskStatus.FAILED
                ]
                summary += f" Failed tasks: {', '.join(failed_ids)}."
            self._state_board._final_summary = summary
            self._state_board.log_event("orchestrator.auto_finalized", message=summary)

        # 5. Return report
        elapsed = round(time.time() - self._state_board.budget.start_time, 1)
        print(
            f"[Orchestrator] Finished in {elapsed}s. "
            f"Tokens: {self._state_board.budget.token_used}/{self._state_board.budget.token_limit}. "
            f"Steps: {self._state_board.budget.steps_taken}/{self._state_board.budget.max_steps}.",
            file=sys.stderr,
            flush=True,
        )
        report = self._state_board.to_report()
        report.metadata.update(summarize_board(self._state_board))
        report.metadata["verification_report"] = build_verification_report(
            self._state_board,
            work_dir=self._current_work_dir,
        )
        return report

    def _should_use_collaborative_mode(self) -> bool:
        """Determine whether resident collaboration should run.

        Modes:
        - ``off``: always use Director scheduling.
        - ``on``: use collaborative mode for any collaborative task graph.
        - ``auto``: use collaborative mode for multi-collaborative graphs that
          benefit from a producer -> checker -> fix loop.
        """
        if self._state_board is None or self._collaborative_mode == "off":
            return False
        collaborative_tasks = [
            t for t in self._state_board.tasks.values()
            if self._resolve_pattern(t) != "default"
        ]
        if self._collaborative_mode == "on":
            return bool(collaborative_tasks)
        return len(collaborative_tasks) >= 2

    def _resolve_pattern(self, task: Any) -> str:
        """Return the collaboration pattern for a task."""
        pattern = getattr(task, "collaboration_pattern", "default") or "default"
        if pattern != "default":
            return pattern
        if getattr(task, "agent_type", "") == "coder":
            return "coder_reviewer"
        return "default"

    def _resolve_producer_type(self, task: Any, pattern: str | None = None) -> str:
        """Return the agent_type that produces work for this collaborative task."""
        participants = getattr(task, "collaboration_participants", {}) or {}
        producer = participants.get("producer")
        if producer:
            return producer
        return getattr(task, "agent_type", "")

    def _resolve_checker_type(self, task: Any, pattern: str | None = None) -> str:
        """Return the agent_type that checks work for this collaborative task."""
        participants = getattr(task, "collaboration_participants", {}) or {}
        checker = participants.get("checker")
        if checker:
            return checker
        if (pattern or self._resolve_pattern(task)) == "coder_reviewer":
            return "reviewer"
        return "reviewer"

    def _resident_id_for(self, task: Any, agent_type: str) -> str:
        """Return the deterministic resident id for an agent type bound to a task."""
        return f"{agent_type}-{task.task_id}"

    def _role_map_for_task(self, task: Any) -> dict[str, str]:
        """Return producer/checker resident ids for a collaborative task."""
        pattern = self._resolve_pattern(task)
        producer_type = self._resolve_producer_type(task, pattern)
        checker_type = self._resolve_checker_type(task, pattern)
        return {
            "producer": self._resident_id_for(task, producer_type),
            "checker": self._resident_id_for(task, checker_type),
        }

    def _collaborative_tasks_needing_action(self) -> tuple[list[Any], list[Any], list[Any]]:
        """Return ready/review/fix task buckets for configured collaboration patterns."""
        if self._state_board is None:
            return [], [], []
        ready_tasks = [
            t for t in self._state_board.tasks_ready()
            if self._resolve_pattern(t) != "default"
        ]
        review_tasks = [
            t for t in self._state_board.tasks.values()
            if t.status == TaskStatus.REVIEW and self._resolve_pattern(t) != "default"
        ]
        fix_tasks = [
            t for t in self._state_board.tasks.values()
            if (
                t.status == TaskStatus.FIX_NEEDED
                and t.assigned_agent
                and self._resolve_pattern(t) != "default"
            )
        ]
        return ready_tasks, review_tasks, fix_tasks

    async def _run_director_mode(self, objective: str, *, intent: IntentResult | None = None) -> None:
        """Original Director-driven orchestration."""
        import sys

        snapshot = self._state_board.snapshot()
        intent_section = ""
        if intent is not None:
            intent_section = (
                f"# Task Intent\n"
                f"- Type: {intent.task_type}\n"
                f"- Complexity: {intent.complexity}\n"
                f"- External integrations: {intent.external or 'none'}\n"
                f"- Priority: {intent.priority}\n"
                f"- Reason: {intent.reason}\n\n"
            )
        director_input = (
            f"# Objective\n{objective}\n\n"
            f"{intent_section}"
            f"# Current State\n"
            f"{json.dumps(snapshot['tasks'], indent=2)}\n\n"
            f"Start orchestration. Call show_state first, then decide which "
            f"agents to spawn. Ready tasks: {snapshot['signals']['ready_to_run']}"
        )

        print("[Orchestrator] Launching Director...", file=sys.stderr, flush=True)
        try:
            await self._run_director(director_input)
        except Exception as exc:
            print(f"[Orchestrator] Director failed: {exc}", file=sys.stderr, flush=True)
            self._state_board.log_event(
                "orchestrator.error", message=f"Director failed: {exc}"
            )

    # -- collaborative mode: resident agents iterate directly ----------------

    async def _run_collaborative(self) -> None:
        """Collaborative orchestration: resident agents work directly together.

        Event-driven: uses ``_collab_wake_event`` instead of polling sleeps.
        The wake event is set whenever a collaboration signal changes task state,
        so the loop reacts immediately instead of burning polling cycles.
        """
        import sys

        print("[Orchestrator] Collaborative loop starting...", file=sys.stderr, flush=True)

        while self._state_board is not None and not self._state_board.budget.exhausted:
            # 0. Always process thread messages first (Coder may have sent
            # TASK_REVIEW_READY while we were sleeping)
            await self._process_collaborative_messages()

            # 0.5 Detect residents that died while their task is still RUNNING.
            # Without this, a failed LLM call or crashed resident leaves the
            # task stuck in RUNNING and the loop spins forever.
            for task in list(self._state_board.tasks.values()):
                if task.status != TaskStatus.RUNNING or not task.assigned_agent:
                    continue
                resident = self._residents.get(task.assigned_agent)
                if resident is None or not getattr(resident, "_active", False):
                    print(
                        f"[Orchestrator] Resident {task.assigned_agent} died while "
                        f"task {task.task_id} was running; marking failed.",
                        file=sys.stderr, flush=True,
                    )
                    self._state_board.update_task(
                        task.task_id,
                        status=TaskStatus.FAILED,
                        error=f"Resident {task.assigned_agent} stopped or crashed",
                    )
                    task.record_iteration(
                        task.assigned_agent,
                        "resident_died",
                        f"Resident {task.assigned_agent} no longer active",
                    )
                    if resident is not None:
                        await resident.stop()
                        self._residents.pop(task.assigned_agent, None)

            # Find tasks needing action after message processing, since thread
            # messages can change RUNNING/REVIEW/FIX_NEEDED state.
            ready_tasks, review_tasks, fix_tasks = self._collaborative_tasks_needing_action()

            # Nothing to do?
            if not ready_tasks and not review_tasks and not fix_tasks:
                if self._state_board.all_terminal():
                    break

                # If tasks have failed, the collaborative loop cannot unblock
                # downstream work on its own. Exit so the Director can recover.
                failed_tasks = [
                    t for t in self._state_board.tasks.values()
                    if t.status == TaskStatus.FAILED
                ]
                if failed_tasks:
                    failed_ids = [t.task_id for t in failed_tasks]
                    print(
                        f"[Orchestrator] Failed tasks {failed_ids} detected; "
                        "exiting collaborative loop for Director recovery.",
                        file=sys.stderr, flush=True,
                    )
                    self._state_board.log_event(
                        "orchestrator.collab_fallback",
                        message=f"Failed tasks {failed_ids}; handing off to Director",
                    )
                    break

                # Wait for a signal to arrive (event-driven) with a short fallback
                # timeout to guard against missed events.  Clear only after wait
                # returns so a set() that fires just before we block is not lost.
                try:
                    await asyncio.wait_for(self._collab_wake_event.wait(), timeout=10.0)
                except asyncio.TimeoutError:
                    pass  # fallback: re-enter loop to check for stuck tasks
                self._collab_wake_event.clear()
                continue

            # 1. Spawn resident producers for ready tasks
            for task in ready_tasks:
                pattern = self._resolve_pattern(task)
                producer_type = self._resolve_producer_type(task, pattern)
                await self._spawn_resident_for_task(task, producer_type, pattern=pattern)

            # 2. Spawn resident checkers for tasks in REVIEW state
            for task in review_tasks:
                pattern = self._resolve_pattern(task)
                checker_type = self._resolve_checker_type(task, pattern)
                await self._spawn_resident_for_task(task, checker_type, pattern=pattern)

            # 3. Send fix instructions to bound producers
            for task in fix_tasks:
                await self._send_fix_to_producer(task)

            # Event-driven: wait for next signal arrival with a short fallback.
            # Clear only after wait returns so a set() just before blocking is
            # not discarded.
            try:
                await asyncio.wait_for(self._collab_wake_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass  # re-enter loop to check resident health
            self._collab_wake_event.clear()

            # Check for stuck/broken residents
            await self._check_resident_health()

        print("[Orchestrator] Collaborative loop ended.", file=sys.stderr, flush=True)

    async def _process_collaborative_messages(self) -> None:
        """Claim structured SIGNAL messages from mailboxes to advance tasks.

        The state machine is driven exclusively by Mailbox v2 SIGNAL messages.
        Tasks are processed concurrently because each task's signal handling
        only mutates its own task state and is independent of other tasks.
        """
        if self._state_board is None:
            return

        self._state_board.log_event(
            "collab.process_messages",
            message=f"tasks={[(t.task_id, t.status.value) for t in self._state_board.tasks.values()]}",
        )

        tasks = list(self._state_board.tasks.values())
        if not tasks:
            return

        await asyncio.gather(
            *(self._process_signals_from_mailbox(task) for task in tasks),
            return_exceptions=True,
        )

    async def _process_signals_from_mailbox(self, task: Any) -> None:
        """Claim structured SIGNAL messages from task agents' mailboxes.

        Uses peek→targeted peek approach: scans for SIGNAL messages without
        touching non-signal messages, then claims each signal individually.
        Non-signal messages stay in the queue for the agent's own check_messages.
        """
        if self._state_board is None:
            return

        task_id = task.task_id
        agents_to_check: list[str] = []
        if task.assigned_agent:
            agents_to_check.append(task.assigned_agent)

        pattern = self._resolve_pattern(task)
        if pattern != "default":
            role_map = self._role_map_for_task(task)
            for resident_id in role_map.values():
                if resident_id not in agents_to_check:
                    agents_to_check.append(resident_id)
            for agent_type in (getattr(task, "collaboration_participants", {}) or {}).values():
                resident_id = self._resident_id_for(task, agent_type)
                if resident_id not in agents_to_check:
                    agents_to_check.append(resident_id)

        for agent_id in agents_to_check:
            # Peek for SIGNAL messages only — don't touch non-signal messages
            signal_msgs = await self._state_board.peek_mailbox(
                agent_id, limit=10, msg_type="signal",
            )
            if not signal_msgs:
                continue

            for signal_msg in signal_msgs:
                parsed = collaboration_message_from_structured(signal_msg)
                if parsed is None or parsed.task_id != task_id:
                    continue
                # Claim the exact signal by msg_id.  This avoids popping non-signal
                # messages that may be ahead of the signal in the priority queue.
                claimed = await self._state_board.claim_message(agent_id, signal_msg.msg_id)
                if claimed is not None:
                    # Use the message's actual sender, not the mailbox owner.
                    # Signals are delivered to the recipient's mailbox, but the
                    # state machine validates who sent them (coder vs reviewer).
                    real_sender = claimed.header.sender
                    await self._apply_collaboration_signal(task, real_sender, parsed, claimed.text or "")
                    await self._state_board.ack_message(agent_id, claimed.msg_id)

    async def _apply_collaboration_signal(
        self,
        task: Any,
        from_id: str,
        parsed: Any,
        content: str,
    ) -> bool:
        """Apply a parsed collaboration signal to a task. Returns True if handled.

        Delegates decision logic to CollaborationStateMachine and side-effect
        execution to CollaborationDecisionExecutor — both independently testable.
        """
        csm = CollaborationStateMachine(
            max_iterations=getattr(task, "max_iterations", 5) or 5,
            role_map=self._role_map_for_task(task),
        )
        decision = csm.decide(
            task=task,
            signal=parsed.signal,
            from_id=from_id,
            content=content,
            tests_passed=parsed.tests_passed,
        )

        if decision.action == CollaborationAction.NONE:
            return False

        executor = CollaborationDecisionExecutor(self._state_board, self._residents)
        handled = await executor.execute(decision, task, from_id, content)
        if handled:
            self._collab_wake_event.set()  # wake the collaboration loop immediately
        return handled

    async def _send_fix_to_producer(self, task: Any) -> None:
        """Send checker feedback back to the producer for a FIX_NEEDED task."""
        if self._state_board is None or not task.assigned_agent:
            return

        resident = self._residents.get(task.assigned_agent)
        if resident is None:
            return

        pattern = self._resolve_pattern(task)
        ctx = self._state_board.get_project_context()
        recent_errors = ctx.get("recent_errors", [])
        feedback = ""
        for err in recent_errors:
            if err.get("source") == task.task_id:
                feedback = err.get("error", "")
                break

        prompt_factory = get_prompt(pattern, "producer_fix")
        if pattern == "coder_reviewer":
            content = prompt_factory(
                task_id=task.task_id,
                description=task.description,
                feedback=feedback,
                iteration_history_json=json.dumps(task.iteration_history[-3:], ensure_ascii=False),
            )
            sender = "reviewer"
        else:
            content = prompt_factory(
                task_id=task.task_id,
                description=task.description,
                feedback=feedback,
                iteration_history_json=json.dumps(task.iteration_history[-3:], ensure_ascii=False),
            )
            sender = "checker"

        await resident.send({
            "task": f"Fix task {task.task_id}",
            "content": content,
            "from": sender,
            "context": f"Task: {task.description}\nHistory: {json.dumps(task.iteration_history[-3:], ensure_ascii=False)}",
        })
        self._state_board.update_task(task.task_id, status=TaskStatus.RUNNING)
        task.record_iteration(task.assigned_agent, "fix_requested", feedback)

    async def _spawn_resident_for_task(
        self,
        task: Any,
        agent_type: str,
        *,
        pattern: str | None = None,
    ) -> ResidentAgent | None:
        """Spawn a resident agent bound to a specific task."""
        import sys

        if self._state_board is None:
            return None

        # Check budget
        if self._state_board.budget.exhausted:
            return None

        pattern = pattern or self._resolve_pattern(task)
        producer_type = self._resolve_producer_type(task, pattern)
        checker_type = self._resolve_checker_type(task, pattern)
        role = "producer" if agent_type == producer_type else "checker"
        resident_id = self._resident_id_for(task, agent_type)

        # Don't spawn if already exists and active or sleeping
        if resident_id in self._residents:
            resident = self._residents[resident_id]
            if resident._active or resident._sleeping:
                if resident._sleeping:
                    await resident.wake()
                return resident

        print(
            f"[Orchestrator] Spawning resident {resident_id} ({role}) for task {task.task_id}",
            file=sys.stderr, flush=True,
        )

        run_budget = None
        if role == "producer":
            run_budget = RunBudget(max_steps=80, max_duration_ms=600_000)
        elif role == "checker":
            run_budget = RunBudget(max_steps=50, max_duration_ms=300_000)

        try:
            resident = ResidentAgent(
                resident_id=resident_id,
                agent_type=agent_type,
                runner=self,
                board=self._state_board,
                max_idle_s=600.0,  # longer idle for iterative work
                persist_dir=str(self._session_dir) if self._session_dir else None,
                run_budget=run_budget,
            )
            if role == "producer":
                resident.bind_task(task.task_id, auto_verify=True)
            else:
                resident._bound_task_id = task.task_id
            await resident.start()
            self._residents[resident_id] = resident

            if role == "producer":
                checker_id = self._resident_id_for(task, checker_type)
                prompt_factory = get_prompt(pattern, "producer")
                if pattern == "coder_reviewer":
                    content = prompt_factory(
                        task_id=task.task_id,
                        description=task.description,
                        expected_artifacts=task.expected_artifacts,
                        reviewer_id=checker_id,
                    )
                    action = "spawned_coder"
                else:
                    content = prompt_factory(
                        task_id=task.task_id,
                        description=task.description,
                        expected_artifacts=task.expected_artifacts,
                        checker_id=checker_id,
                    )
                    action = "spawned_producer"
                initial_msg = {
                    "task": task.description,
                    "content": content,
                    "from": "director",
                    "context": task.input_context,
                }
                self._state_board.update_task(task.task_id, status=TaskStatus.RUNNING)
                task.assigned_agent = resident_id
                task.record_iteration(resident_id, action, task.description)

            else:
                producer_id = task.assigned_agent or self._resident_id_for(task, producer_type)
                if not producer_id:
                    raise RuntimeError(f"Task {task.task_id} has no assigned producer")
                thread_id = f"task-{task.task_id}"
                prompt_factory = get_prompt(pattern, "checker")
                if pattern == "coder_reviewer":
                    content = prompt_factory(
                        task_id=task.task_id,
                        description=task.description,
                        coder_id=producer_id,
                        thread_id=thread_id,
                        iteration_history=json.dumps(task.iteration_history, ensure_ascii=False),
                    )
                    action = "spawned_reviewer"
                    context_label = "Coder"
                else:
                    content = prompt_factory(
                        task_id=task.task_id,
                        description=task.description,
                        producer_id=producer_id,
                        thread_id=thread_id,
                        iteration_history=json.dumps(task.iteration_history, ensure_ascii=False),
                    )
                    action = "spawned_checker"
                    context_label = "Producer"
                initial_msg = {
                    "task": f"Review task {task.task_id}",
                    "content": content,
                    "from": "director",
                    "context": f"{context_label}: {producer_id}\nThread: {thread_id}\nHistory: {json.dumps(task.iteration_history, ensure_ascii=False)}",
                }
                task.record_iteration(resident_id, action, "")

            await resident.send(initial_msg)
            return resident

        except Exception as exc:
            print(
                f"[Orchestrator] Failed to spawn resident {resident_id}: {exc}",
                file=sys.stderr, flush=True,
            )
            self._state_board.log_event(
                "orchestrator.resident_spawn_failed",
                message=f"{resident_id}: {exc}",
            )
            return None

    async def _check_resident_health(self) -> None:
        """Check if any residents are stuck and need Director intervention."""
        if self._state_board is None:
            return

        stuck_threshold_s = self._resident_stuck_threshold_s
        now = time.time()

        for resident_id, resident in list(self._residents.items()):
            state = resident.state
            idle_s = now - state.last_active

            # Sleeping residents are intentionally idle; don't flag them as stuck.
            if state.status == "sleeping":
                continue

            if state.status == "error" or idle_s > stuck_threshold_s:
                # Resident is stuck — wake up Director
                task_id = resident.bound_task_id
                print(
                    f"[Orchestrator] Resident {resident_id} stuck (idle {idle_s:.0f}s), "
                    f"waking Director...",
                    file=sys.stderr, flush=True,
                )
                self._state_board.log_event(
                    "orchestrator.resident_stuck",
                    agent_id=resident_id,
                    message=f"Idle {idle_s:.0f}s, task={task_id}",
                )
                # Mark task for Director to handle
                if task_id:
                    self._state_board.update_task(
                        task_id,
                        status=TaskStatus.FAILED,
                        error=f"Resident {resident_id} stuck after {idle_s:.0f}s",
                    )

                # Stop the stuck resident
                await resident.stop()

    # -- SDK event bus bridge ------------------------------------------------

    def _bridge_sdk_events(self) -> None:
        """Forward SDK AsyncEventBus events to StateBoard for behavioral profiling."""
        if self._state_board is None:
            return

        async def on_sdk_event(runtime_event: RuntimeEvent) -> None:
            name = runtime_event.name
            payload = dict(runtime_event.payload)
            agent_id = payload.get("agent_id", "unknown")

            if name in ("llm.succeeded", "llm.failed", "llm.cancelled"):
                metrics = payload.get("_metrics")
                if metrics and self._state_board is not None:
                    agent = self._state_board.get_agent(agent_id)
                    if agent and name != "llm.cancelled":
                        # Cancelled calls are lifecycle events, not consumed API
                        # calls, so don't count them against the agent.
                        agent.llm_call_count += 1
                        agent.total_llm_latency_ms += getattr(metrics, "latency_ms", 0)
                        agent.token_used += getattr(metrics, "input_tokens", 0) + getattr(metrics, "output_tokens", 0)

                    self._state_board.log_event(
                        f"sdk.{name}",
                        agent_id=agent_id,
                        message=f"latency={getattr(metrics, 'latency_ms', 0):.0f}ms, "
                                f"tokens={getattr(metrics, 'input_tokens', 0)}+{getattr(metrics, 'output_tokens', 0)}",
                        latency_ms=round(getattr(metrics, "latency_ms", 0), 1),
                        input_tokens=getattr(metrics, "input_tokens", 0),
                        output_tokens=getattr(metrics, "output_tokens", 0),
                    )

            elif name in ("tool.called", "tool.succeeded", "tool.failed"):
                tool_id = payload.get("tool_id", "unknown")
                if self._state_board is not None:
                    agent = self._state_board.get_agent(agent_id)
                    if agent:
                        agent.tool_call_counts[tool_id] = agent.tool_call_counts.get(tool_id, 0) + 1
                        if tool_id in ("write_file", "edit_file") and agent.first_artifact_time == 0:
                            agent.first_artifact_time = time.time()

                    self._state_board.log_event(
                        f"sdk.{name}",
                        agent_id=agent_id,
                        message=f"tool={tool_id}",
                        tool_id=tool_id,
                    )

        self._event_bus.subscribe("*", on_sdk_event)

    async def _spawn_monitor_resident(self) -> None:
        """Spawn the monitor resident agent for continuous monitoring."""
        if self._state_board is None:
            return
        if not self._enable_monitor_resident:
            return
        try:
            if "monitor" not in self._agents_by_id:
                print("[Orchestrator] Monitor agent not configured, skipping.", file=sys.stderr, flush=True)
                return

            resident_id = f"monitor-{uuid.uuid4().hex[:6]}"

            resident = ResidentAgent(
                resident_id=resident_id,
                agent_type="monitor",
                runner=self,
                board=self._state_board,
            )
            await resident.start()
            self._residents[resident_id] = resident
            self._monitor_resident_id = resident_id
            self._state_board.register_resident(resident.state)
            print(f"[Orchestrator] Monitor resident spawned: {resident_id}", file=sys.stderr, flush=True)
        except Exception as exc:
            print(f"[Orchestrator] Failed to spawn monitor: {exc}", file=sys.stderr, flush=True)

    async def run_agent(self, agent_type: str, input_text: str, agent_id: str | None = None) -> str:
        """Spawn a tactical agent. Called by spawn_agent tool.

        Args:
            agent_type: The agent type to spawn (coder, reviewer, etc.)
            input_text: The input text to pass to the agent.
            agent_id: Optional agent ID. If not provided, a random one is generated.

        Returns the agent's final output text.
        """
        import sys
        agent_id = agent_id or f"{agent_type}-{uuid.uuid4().hex[:6]}"

        # Start a trace for this agent run
        task_id = agent_id.replace(f"{agent_type}-", "", 1) if agent_id.startswith(f"{agent_type}-") else agent_id
        if self._state_board is not None:
            parent_trace = self._state_board.get_trace(task_id)
            self._state_board.start_trace(agent_id, parent=parent_trace)

        print(
            f"[Orchestrator] Spawning {agent_type} ({agent_id})...",
            file=sys.stderr,
            flush=True,
        )

        # Team leader gets its own SubStateBoard scoped to the task subgraph
        deps_override = None
        if agent_type == "team_leader" and self._state_board is not None:
            task_id = agent_id.replace(f"{agent_type}-", "", 1) if agent_id.startswith(f"{agent_type}-") else agent_id
            task = self._state_board.get_task(task_id)
            if task is not None and task.subgraph is not None:
                sub_board = SubStateBoard(
                    parent=self._state_board,
                    objective=task.description,
                )
                sub_board.add_tasks(task.subgraph)
                deps_override = RunnerDeps(
                    state_board=sub_board,
                    runner_delegate=self.run_agent,
                    runner=self,
                    artifact_store=getattr(self._deps, "artifact_store", None) if self._deps else None,
                    matrix_transport=getattr(self._deps, "matrix_transport", None) if self._deps else None,
                )
                # Create Leader Room on Matrix if transport is enabled
                mx = getattr(deps_override, "matrix_transport", None)
                if mx is not None and mx.enabled:
                    with contextlib.suppress(Exception):
                        await mx.create_room(
                            name=f"leader-{task_id}",
                            invite=[],
                        )

        async with self._spawn_sem:
            result = await self._run_single(agent_id, agent_type, input_text, deps_override=deps_override)

        # Extract metrics and record to StateBoard (even on failure)
        # NOTE: steps are already added inside _run_single (runner.py:803);
        # do NOT double-count here.
        tokens = result.usage.total_tokens if result.usage else 0
        steps = result.metadata.get("steps_used", 0) if result.metadata else 0
        if self._state_board is not None:
            self._state_board.update_agent(
                agent_id, token_used=tokens, steps_used=steps
            )

        if result.stop_reason == StopReason.FAILED:
            msg = extract_result_error_message(result)
            print(f"[Orchestrator] {agent_type} ({agent_id}) FAILED: {msg}", file=sys.stderr, flush=True)
            # Record failed decision for Director's feedback loop
            if self._state_board is not None:
                task_id = self._task_id_from_agent_id(agent_type, agent_id)
                self._state_board.decision_history.record(DecisionRecord(
                    decision_type="spawn_agent",
                    task_id=task_id,
                    agent_id=agent_id,
                    agent_type=agent_type,
                    reasoning=f"Spawned {agent_type} for task {task_id}",
                    outcome="failed",
                    error=msg[:300],
                    token_spent=tokens,
                    steps_used=int(steps) if isinstance(steps, int) else 0,
                ))
            raise RuntimeError(msg)

        # Step budget exhausted — treat as failure so the director can retry/replan.
        if result.stop_reason == StopReason.MAX_STEPS:
            if self._state_board is not None:
                self._state_board.update_agent(
                    agent_id,
                    status=AgentStatus.FAILED,
                    end_time=time.time(),
                )
                task_id = agent_id.replace(f"{agent_type}-", "", 1) if agent_id.startswith(f"{agent_type}-") else agent_id
                task = self._state_board.get_task(task_id)
                if task is not None and not task.is_terminal():
                    self._state_board.update_task(
                        task_id,
                        status=TaskStatus.FAILED,
                        error=(
                            f"Agent step budget exhausted "
                            f"({result.metadata.get('steps_used', '?')} steps)"
                        ),
                    )
            print(
                f"[Orchestrator] {agent_type} ({agent_id}) MAX_STEPS: step budget exhausted",
                file=sys.stderr,
                flush=True,
            )
            # Record decision outcome for Director's feedback loop
            if self._state_board is not None:
                task_id = self._task_id_from_agent_id(agent_type, agent_id)
                self._state_board.decision_history.record(DecisionRecord(
                    decision_type="spawn_agent",
                    task_id=task_id,
                    agent_id=agent_id,
                    agent_type=agent_type,
                    reasoning=f"Spawned {agent_type} for task {task_id}",
                    outcome="failed",
                    error="step budget exhausted",
                    token_spent=tokens,
                    steps_used=int(steps) if isinstance(steps, int) else 0,
                ))
            return str(result.final_output or "")

        # Mark task as completed on success
        if self._state_board is not None:
            # Extract task_id from agent_id (format: agent_type-task_id, e.g. coder-t1)
            task_id = agent_id.replace(f"{agent_type}-", "", 1) if agent_id.startswith(f"{agent_type}-") else agent_id
            task = self._state_board.get_task(task_id)
            if task is not None and task.status != TaskStatus.COMPLETED:
                result_output = str(result.final_output or "")
                result_artifacts = list(result.artifacts or [])
                if agent_type == "team_leader" and deps_override is not None:
                    sub_board = getattr(deps_override, "state_board", None)
                    summary, artifact_paths = self._summarize_team_sub_board(sub_board)
                    if summary:
                        result_output = summary
                    if artifact_paths:
                        result_artifacts.extend(artifact_paths)

                self._state_board.update_task(
                    task_id,
                    status=TaskStatus.COMPLETED,
                    result_output=result_output[:2000],
                )
                # Verify and record artifacts (strict: must exist and be non-empty)
                for art in result_artifacts:
                    art_path = getattr(art, "path", str(art)) if hasattr(art, "path") else str(art)
                    resolved = self._resolve_artifact_path(str(art_path)) if art_path else None
                    if resolved is not None:
                        exists = resolved.exists() and resolved.stat().st_size > 0
                        self._state_board.verify_artifact(str(art_path), exists=exists)
                        self._state_board.claim_artifact(task_id, [str(art_path)])

            # Merge sub-board budget back to parent for team leaders
            if agent_type == "team_leader" and deps_override is not None:
                sub_board = getattr(deps_override, "state_board", None)
                if sub_board is not None and hasattr(sub_board, "budget"):
                    self._state_board.add_tokens(sub_board.budget.token_used)
                    self._state_board.add_steps(sub_board.budget.steps_taken)
                    self._state_board.log_event(
                        "team.budget_merged",
                        agent_id=agent_id,
                        message=f"tokens={sub_board.budget.token_used}, steps={sub_board.budget.steps_taken}",
                    )

        # Print execution summary
        tokens = result.usage.total_tokens if result.usage else 0
        steps = result.metadata.get("steps_used", "?") if result.metadata else "?"
        tool_calls = result.metadata.get("tool_calls_used", "?") if result.metadata else "?"
        output_preview = str(result.final_output or "")[:200].replace("\n", " ")

        print(
            f"[Orchestrator] {agent_type} ({agent_id}) done. "
            f"tokens={tokens}, steps={steps}, tools={tool_calls}. "
            f"Output: {output_preview}{'...' if len(str(result.final_output or '')) > 200 else ''}",
            file=sys.stderr,
            flush=True,
        )

        # Record decision outcome for Director's feedback loop
        if self._state_board is not None:
            task_id = self._task_id_from_agent_id(agent_type, agent_id)
            artifact_paths = [getattr(a, "path", str(a)) for a in (result.artifacts or [])]
            self._state_board.decision_history.record(DecisionRecord(
                decision_type="spawn_agent",
                task_id=task_id,
                agent_id=agent_id,
                agent_type=agent_type,
                reasoning=f"Spawned {agent_type} for task {task_id}",
                outcome="completed",
                artifacts_produced=artifact_paths,
                token_spent=tokens,
                steps_used=int(steps) if isinstance(steps, int) else 0,
            ))

        return str(result.final_output or "")

    def _resolve_artifact_path(self, artifact_path: str) -> Path | None:
        """Resolve an artifact path against known working directories."""
        raw = str(artifact_path or "").strip()
        if not raw or any(ch in raw for ch in ("\n", "\r")):
            return None
        # Ignore natural-language snippets accidentally extracted as paths.
        if " " in raw or raw.startswith(("bash:", "pytest:")):
            return None

        path = Path(raw)
        if path.is_absolute():
            return path

        candidates: list[Path] = []
        if self._current_work_dir is not None:
            candidates.append(Path(self._current_work_dir) / path)
            # Some agents include the work-dir basename in artifact paths even
            # though writes were already rooted at work_dir. Strip that prefix.
            parts = path.parts
            work_name = Path(self._current_work_dir).name
            if parts and parts[0].lstrip(".") == work_name.lstrip("."):
                candidates.append(Path(self._current_work_dir).joinpath(*parts[1:]))
        candidates.append(Path.cwd() / path)

        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0] if candidates else path

    def _summarize_team_sub_board(self, sub_board: Any) -> tuple[str, list[str]]:
        """Build parent task output/artifacts from a completed team sub-board."""
        if sub_board is None or not hasattr(sub_board, "tasks"):
            return "", []

        completed = [
            task for task in sub_board.tasks.values()
            if getattr(task, "status", None) == TaskStatus.COMPLETED
        ]
        if not completed:
            return "", []

        lines = [f"Team completed {len(completed)}/{len(sub_board.tasks)} subtask(s)."]
        artifacts: list[str] = []
        for task in completed:
            output = str(getattr(task, "result_output", "") or "").strip()
            if output:
                lines.append(f"- {task.task_id}: {output[:300]}")
            else:
                lines.append(f"- {task.task_id}: completed")
            for path in getattr(task, "actual_artifacts", []) or []:
                if path and path not in artifacts:
                    artifacts.append(str(path))

        for path, record in getattr(sub_board, "artifacts", {}).items():
            if getattr(record, "status", "") == "verified" and path not in artifacts:
                artifacts.append(str(path))

        return "\n".join(lines), artifacts

    # -- resident agents -----------------------------------------------------

    async def start_resident(self, agent_type: str) -> str:
        """Start a persistent resident agent.

        Returns the resident_id.
        Raises RuntimeError if max concurrent residents reached.
        """
        if self._state_board is None:
            raise RuntimeError("StateBoard not initialized")
        # Limit concurrent residents to prevent token explosion
        active_residents = sum(
            1 for r in self._residents.values()
            if r.state.status in ("idle", "busy")
        )
        if active_residents >= self._max_concurrent_residents:
            raise RuntimeError(
                f"Max concurrent residents ({self._max_concurrent_residents}) reached. "
                f"Stop an existing resident before starting a new one."
            )
        resident_id = f"{agent_type}-resident-{uuid.uuid4().hex[:6]}"

        # Start a trace for this resident
        self._state_board.start_trace(resident_id)

        persist_dir = self._session_dir if self._session_dir is not None else None
        resident = ResidentAgent(
            resident_id=resident_id,
            agent_type=agent_type,
            runner=self,
            board=self._state_board,
            persist_dir=persist_dir,
        )
        self._residents[resident_id] = resident
        await resident.start()
        return resident_id

    async def send_to_resident(
        self,
        resident_id: str,
        *,
        task: str = "",
        content: str = "",
        context: str = "",
        from_id: str = "director",
    ) -> None:
        """Send a message to a resident agent."""
        resident = self._residents.get(resident_id)
        if resident is None:
            raise RuntimeError(f"Resident '{resident_id}' not found")
        await resident.send({
            "from": from_id,
            "task": task,
            "content": content,
            "context": context,
        })

    def get_resident(self, resident_id: str) -> ResidentAgent | None:
        return self._residents.get(resident_id)

    async def stop_resident(self, resident_id: str) -> None:
        """Stop a resident agent."""
        resident = self._residents.pop(resident_id, None)
        if resident is not None:
            await resident.stop()

    async def _run_resident_single(
        self,
        *,
        resident_id: str,
        agent_type: str,
        input_text: str,
        transcript: list[dict[str, Any]],
        budget: Any | None = None,
    ) -> RunResult[str]:
        """Run one shot for a resident agent with persistent transcript."""
        return await self._run_single(
            agent_id=resident_id,
            agent_type=agent_type,
            input_text=input_text,
            transcript_override=transcript,
            budget=budget,
        )

    # -- internals -----------------------------------------------------------

    async def _classify_intent(self, objective: str) -> IntentResult:
        """Classify task intent before decomposition.

        Transient LLM errors are caught and surfaced as a low-confidence
        fallback so that orchestration always proceeds.  Intent is a
        hint — not a gating decision — so a failed classification
        should never crash the run.
        """
        director_agent = self._agents_by_id.get("director")
        if director_agent is None or director_agent.llm is None:
            return IntentResult(
                task_type="unknown", complexity="medium",
                external=[], priority="normal",
                confidence=0.0, reason="No director LLM configured", source="fallback",
            )
        llm = create_llm_client(director_agent.llm)
        classifier = IntentClassifier(llm_client=llm)
        try:
            return await asyncio.wait_for(classifier.classify(objective), timeout=25.0)
        except (asyncio.TimeoutError, Exception) as exc:
            import sys
            print(
                f"[Orchestrator] Intent classification failed: {exc}; using fallback.",
                file=sys.stderr, flush=True,
            )
            return IntentResult(
                task_type="unknown", complexity="medium",
                external=[], priority="normal",
                confidence=0.0,
                reason=f"LLM error: {exc}",
                source="fallback",
            )

    async def _initial_decompose(self, objective: str) -> TaskGraph:
        """Use structured generation to decompose the objective into a TaskGraph."""
        director_agent = self._agents_by_id.get("director")
        if director_agent is None or director_agent.llm is None:
            raise ConfigError("Director agent not configured")

        llm = create_llm_client(director_agent.llm)
        agents_info = self._build_agents_info()

        class _SubtaskSchema(BaseModel):
            task_id: str
            description: str
            agent_type: str = "coder"
            dependencies: list[str] = Field(default_factory=list)
            expected_artifacts: list[str] = Field(default_factory=list)

        class _TaskSchema(BaseModel):
            task_id: str
            description: str
            input_context: str = ""
            agent_type: str = "coder"
            dependencies: list[str] = Field(default_factory=list)
            expected_artifacts: list[str] = Field(default_factory=list)
            subtasks: list[_SubtaskSchema] = Field(default_factory=list)

        class _GraphSchema(BaseModel):
            tasks: list[_TaskSchema]

        system_prompt = (
            "You are a task decomposer. Break down the objective into a structured task graph.\n\n"
            f"Available agent types:\n{agents_info}\n\n"
            "Rules:\n"
            "1. Each task has a unique task_id (t1, t2, ...)\n"
            "2. List dependencies explicitly\n"
            "3. EACH TASK SHOULD HAVE AT MOST 3-5 expected_artifacts. Split large tasks.\n"
            "4. Keep the graph shallow (2-4 layers). Prefer fewer tasks.\n"
            "5. input_context: detailed instructions for the agent, including file paths and tests to write\n"
            "6. coder agents have a step budget of ~30 steps.\n"
            "7. EVERY coder task MUST produce runnable source code and/or tests. "
            "Do NOT create tasks that only produce analysis, README, or design documents.\n"
            "8. For simple features, use a single coder task that implements code + tests together.\n"
            "9. For complex features that benefit from internal coder+reviewer loops, "
            "set agent_type='team_leader' and provide subtasks (2-4 sub-tasks with dependencies)."
        )

        try:
            result, usage = await structured_generate(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Objective: {objective}"},
                ],
                response_model=_GraphSchema,
                llm_client=llm,
                temperature=0.2,
                max_tokens=4096,
            )
            if self._state_board is not None and usage is not None:
                self._state_board.add_usage(usage)
        except Exception as exc:
            raise RuntimeError(f"Task decomposition failed: {exc}") from exc

        tasks = []
        for item in result.tasks:
            node = TaskNode(
                task_id=str(item.task_id),
                description=str(item.description),
                agent_type=str(item.agent_type),
                dependencies=list(item.dependencies),
                expected_artifacts=list(item.expected_artifacts),
                input_context=str(item.input_context),
            )
            # Convert subtasks to subgraph for team_leader delegation
            if item.subtasks:
                sub_ids = {str(sub.task_id) for sub in item.subtasks}
                sub_tasks = []
                for sub in item.subtasks:
                    # Subgraph dependencies must reference only sibling subtasks.
                    # Dependencies on parent-level tasks are satisfied implicitly
                    # because the parent task already waits for them.
                    deps = [d for d in sub.dependencies if d in sub_ids]
                    sub_tasks.append(TaskNode(
                        task_id=str(sub.task_id),
                        description=str(sub.description),
                        agent_type=str(sub.agent_type),
                        dependencies=deps,
                        expected_artifacts=list(sub.expected_artifacts),
                        input_context=f"Subtask of {item.task_id}: {sub.description}",
                    ))
                node.subgraph = TaskGraph(
                    objective=node.description,
                    tasks=sub_tasks,
                )
                node.subgraph.validate()
            tasks.append(node)
        graph = TaskGraph(objective=objective, tasks=tasks)
        graph.validate()
        return graph

    def _build_agents_info(self) -> str:
        descriptions = {
            "coder": "writes and edits code files",
            "reviewer": "reviews code for quality, security, correctness; writes and runs tests",
            "researcher": "researches topics via web search and analysis",
            "github_agent": "GitHub operations: PRs, issues, CI, code review, repo management",
            "monitor": "monitors orchestration state, detects anomalies, watches system health and performance",
        }
        lines = []
        for aid, _agent in self._agents_by_id.items():
            if aid == "director":
                continue
            desc = descriptions.get(aid, "tactical agent")
            lines.append(f"- {aid}: {desc}")
        return "\n".join(lines) or "- coder: writes code"

    @staticmethod
    def _parse_task_graph(text: str, objective: str) -> TaskGraph:
        import re

        text = text.strip()
        # Extract JSON
        m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
        raw = m.group(1).strip() if m else text

        # Find first JSON object
        brace = raw.find("{")
        if brace == -1:
            raise ValueError("No JSON object found in decomposition")
        depth, end = 0, 0
        for i, ch in enumerate(raw[brace:], start=brace):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            if depth == 0:
                end = i + 1
                break
        raw = raw[brace:end]

        data = json.loads(raw)
        tasks = []
        for item in data.get("tasks", []):
            tasks.append(TaskNode(
                task_id=str(item.get("task_id", "")),
                description=str(item.get("description", "")),
                agent_type=str(item.get("agent_type", "coder")),
                dependencies=list(item.get("dependencies", [])),
                expected_artifacts=list(item.get("expected_artifacts", [])),
                input_context=str(item.get("input_context", "")),
            ))
        graph = TaskGraph(objective=objective, tasks=tasks)
        graph.validate()
        return graph

    async def _run_director(self, input_text: str) -> str:
        """Run the director agent."""
        result = await self._run_single("director", "director", input_text)
        return str(result.final_output or "")

    async def _run_single(
        self,
        agent_id: str,
        agent_type: str,
        input_text: str,
        budget: RunBudget | None = None,
        transcript_override: list[dict[str, Any]] | None = None,
        deps_override: RunnerDeps | None = None,
    ) -> RunResult[str]:
        """Run one agent (director or tactical) — based on CoreCoderLocalRunner."""
        bundle = self._ensure_bundle(agent_type)
        deps = deps_override if deps_override is not None else self._deps
        request = RunRequest(
            agent_id=agent_id,
            session_id=f"session-{agent_id}",
            input_text=input_text,
            budget=budget or self._default_budget(bundle.agent),
            deps=deps,
        )
        usage = RunUsage()
        state: dict[str, Any] = {}

        # Context assembly
        context_assembler = bundle.plugins.context_assembler
        session_state = {"llm_client": bundle.llm_client}
        if transcript_override is not None:
            # Resident agent: use provided persistent transcript
            transcript = list(transcript_override)
            session_artifacts = await self._sessions.list_artifacts(request.session_id)
            assembly_metadata = {}
        elif context_assembler is not None:
            assembly = await context_assembler.assemble(
                request=request,
                session_state=session_state,
                session_manager=self._sessions,
            )
            transcript = assembly.transcript
            session_artifacts = assembly.session_artifacts
            assembly_metadata = assembly.metadata
        else:
            transcript = await self._sessions.load_messages(request.session_id)
            session_artifacts = await self._sessions.list_artifacts(request.session_id)
            assembly_metadata = {}

        # Setup pattern with agent-scoped event bus (injects agent_id into SDK events)
        pattern = bundle.plugins.pattern
        scoped_bus = _AgentScopedEventBus(self._event_bus, agent_id)
        await pattern.setup(
            agent_id=agent_id,
            session_id=request.session_id,
            input_text=input_text,
            state=state,
            tools=bundle.plugins.tools,
            llm_client=bundle.llm_client,
            llm_options=bundle.agent.llm,
            event_bus=scoped_bus,
            transcript=transcript,
            session_artifacts=session_artifacts,
            assembly_metadata=assembly_metadata,
            run_request=request,
            tool_executor=bundle.plugins.tool_executor,
            usage=usage,
            artifacts=[],
        )

        ctx = pattern.context
        if ctx is None:
            raise RuntimeError("Pattern setup did not create context")

        # Memory
        memory = bundle.plugins.memory
        if memory is not None:
            try:
                await memory.inject(ctx)
            except Exception:
                if getattr(bundle.agent.memory, "on_error", "fail") == "fail":
                    raise

        # Execute
        try:
            final_output = await pattern.execute()
        except Exception as exc:
            if agent_type != "director":
                self._print_agent_trace(agent_id, agent_type, ctx, exc=exc)
            # Track resource usage on failure
            if self._state_board is not None:
                self._state_board.add_tokens(usage.total_tokens)
                self._state_board.add_steps(ctx.state.get("__steps_used__", 0))
                summary = summarize_agent_run(
                    agent_id=agent_id,
                    task_id=self._task_id_from_agent_id(agent_type, agent_id),
                    status="failed",
                    error=str(exc),
                    transcript=list(ctx.transcript),
                    artifacts=list(ctx.artifacts),
                    steps_used=ctx.state.get("__steps_used__", 0),
                    token_used=usage.total_tokens,
                )
                self._state_board.log_event(
                    "agent.run_summary",
                    task_id=summary["task_id"],
                    agent_id=agent_id,
                    message=f"failed: {summary['failure_type']}",
                    summary=summary,
                )
            return RunResult(
                run_id=request.run_id,
                final_output=None,
                stop_reason=StopReason.FAILED,
                usage=usage,
                artifacts=list(ctx.artifacts),
                **run_result_error_kwargs(exc),
                metadata={
                    "agent_id": agent_id,
                    "steps_used": getattr(ctx.state, "get", lambda k, d: d)("__steps_used__", 0),
                    "consecutive_tool_failures": ctx.state.get("__consecutive_tool_failures__", 0),
                    "consecutive_empty_responses": ctx.state.get("__consecutive_empty_responses__", 0),
                    "api_error_count": ctx.state.get("__api_error_count__", 0),
                    "transcript": list(ctx.transcript),
                },
            )

        ctx.state["final_output"] = str(final_output or "").strip()

        # Detect step budget exhaustion signalled by the pattern.
        stop_reason = StopReason.COMPLETED
        if ctx.state.get("__step_budget_exhausted__"):
            stop_reason = StopReason.MAX_STEPS

        # Memory writeback
        if memory is not None:
            try:
                await memory.writeback(ctx)
                await memory.compact(ctx)
            except Exception:
                if getattr(bundle.agent.memory, "on_error", "fail") == "fail":
                    raise

        # Persist session
        self._sessions.save(
            request.session_id,
            messages=ctx.transcript,
            artifacts=[*list(ctx.session_artifacts), *list(ctx.artifacts)],
        )

        steps_used = ctx.state.get("__steps_used__", 0)
        tool_calls_used = ctx.state.get("__tool_calls_used__", 0)
        if agent_type != "director":
            self._print_agent_trace(agent_id, agent_type, ctx, final_output=final_output)

        # Track token usage on success
        if self._state_board is not None:
            self._state_board.add_tokens(usage.total_tokens)
            self._state_board.add_steps(steps_used)
            summary = summarize_agent_run(
                agent_id=agent_id,
                task_id=self._task_id_from_agent_id(agent_type, agent_id),
                status="completed",
                output=str(final_output or ""),
                transcript=list(ctx.transcript),
                artifacts=list(ctx.artifacts),
                steps_used=steps_used,
                token_used=usage.total_tokens,
            )
            self._state_board.log_event(
                "agent.run_summary",
                task_id=summary["task_id"],
                agent_id=agent_id,
                message="completed",
                summary=summary,
            )

        result = RunResult(
            run_id=request.run_id,
            final_output=str(final_output or ""),
            stop_reason=stop_reason,
            usage=usage,
            artifacts=list(ctx.artifacts),
            metadata={
                "agent_id": agent_id,
                "steps_used": steps_used,
                "tool_calls_used": tool_calls_used,
                "transcript": list(ctx.transcript),
            },
        )
        if context_assembler is not None:
            finalized = await context_assembler.finalize(
                request=request,
                session_state=session_state,
                session_manager=self._sessions,
                result=result,
            )
            if finalized is not None:
                result = finalized
        return result

    @staticmethod
    def _task_id_from_agent_id(agent_type: str, agent_id: str) -> str:
        prefix = f"{agent_type}-"
        return agent_id.replace(prefix, "", 1) if agent_id.startswith(prefix) else agent_id

    def _ensure_bundle(self, agent_id: str) -> _AgentBundle:
        agent = self._agents_by_id.get(agent_id)
        if agent is None:
            raise ConfigError(
                f"Unknown agent id: '{agent_id}'",
                hint=f"Available: {sorted(self._agents_by_id)}",
            )
        if agent.llm is None:
            raise ConfigError(f"Agent '{agent_id}' has no llm configured")

        plugins = load_agent_plugins(agent)
        self._wrap_tool_invocations(plugins.tools)
        llm_client = create_llm_client(agent.llm)
        return _AgentBundle(agent=agent, plugins=plugins, llm_client=llm_client)

    @staticmethod
    def _wrap_tool_invocations(tools: dict[str, Any]) -> None:
        """Wrap each tool's invoke in a thread pool so sync I/O never blocks the loop.

        The SDK's SafeToolExecutor calls ``await asyncio.wait_for(coro, timeout=...)``
        but ``wait_for`` cannot interrupt a coroutine that is stuck in synchronous
        blocking I/O (e.g. ``path.read_text()``, ``subprocess.run()``).  By running
        the entire tool invocation in a separate thread (with its own event loop),
        the main loop stays responsive and timeouts work correctly.
        """
        import types

        for tool in tools.values():
            original_invoke = tool.invoke

            # Skip if already wrapped (idempotent)
            if getattr(original_invoke, "__oa_thread_wrapped__", False):
                continue

            wrapped = OrchestratorRunner._make_thread_safe_invoke(original_invoke)
            tool.invoke = types.MethodType(wrapped, tool)

    @staticmethod
    def _make_thread_safe_invoke(original_invoke: Any) -> Any:
        """Factory: return an async invoke that runs the original in a thread.

        Named function (not closure) for readability and testability.
        The original_invoke is captured via default argument to avoid the
        classic Python closure-over-loop-variable pitfall.
        """

        async def _thread_safe_invoke(self, params: Any, ctx: Any, _orig: Any = original_invoke) -> Any:
            def _sync_runner():
                return asyncio.run(_orig(params, ctx))

            return await asyncio.to_thread(_sync_runner)

        _thread_safe_invoke.__oa_thread_wrapped__ = True  # type: ignore[attr-defined]
        return _thread_safe_invoke

    def _default_budget(self, agent: Any) -> RunBudget:
        return RunBudget(
            max_steps=agent.runtime.max_steps if hasattr(agent, "runtime") else 20,
            max_duration_ms=agent.runtime.step_timeout_ms if hasattr(agent, "runtime") else 300_000,
            max_validation_retries=3,
        )

    @staticmethod
    def _print_agent_trace(
        agent_id: str,
        agent_type: str,
        ctx: Any,
        *,
        final_output: str | None = None,
        exc: Exception | None = None,
    ) -> None:
        """Print a condensed execution trace of a tactical agent to stderr."""
        import sys

        lines: list[str] = [f"  --- {agent_type} ({agent_id}) trace ---"]
        # Summarize transcript: count turns and list tool calls
        transcript = list(getattr(ctx, "transcript", []))
        user_turns = sum(1 for e in transcript if e.get("role") == "user")
        assistant_turns = sum(1 for e in transcript if e.get("role") == "assistant")
        lines.append(f"  turns: {user_turns} user / {assistant_turns} assistant")

        # List tool calls made
        tool_calls: list[str] = []
        for entry in transcript:
            if entry.get("role") == "assistant" and "tool_calls" in entry:
                for tc in entry["tool_calls"]:
                    tool_calls.append(tc.get("function", {}).get("name", tc.get("name", "?")))
        if tool_calls:
            lines.append(f"  tools: {' → '.join(tool_calls[:8])}{'...' if len(tool_calls) > 8 else ''}")

        # Output snippet
        if exc is not None:
            lines.append(f"  ERROR: {exc}")
        elif final_output:
            preview = str(final_output).replace("\n", " ")[:300]
            lines.append(f"  output: {preview}{'...' if len(str(final_output)) > 300 else ''}")

        print("\n".join(lines), file=sys.stderr, flush=True)

    async def close(self) -> None:
        # Save final snapshot before shutting down
        if self._state_board is not None and self._snapshotter is not None:
            seq = self._recorder._seq if self._recorder is not None else 0
            self._snapshotter.force_snapshot(self._state_board, seq=seq)
        # Flush remaining events
        if self._recorder is not None:
            self._recorder.close()
        # Stop all residents
        for resident in list(self._residents.values()):
            await resident.stop()
        self._residents.clear()
        for bundle in self._bundles.values():
            memory = getattr(bundle.plugins, "memory", None)
            if memory is not None and hasattr(memory, "close"):
                await memory.close()
        await self._event_bus.close()

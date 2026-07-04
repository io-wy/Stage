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
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openagents.errors.exceptions import ConfigError
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

from openagents_orchestration.core.agent_loader import (
    AgentSpecError,
    load_agent_specs,
)
from openagents_orchestration.core.state_board import Budget, StateBoard, TaskStatus
from openagents_orchestration.core.sub_state_board import SubStateBoard
from openagents_orchestration.hooks import (
    ContinuationHooks,
    FailureHooks,
    HookEvent,
    HookManager,
    StateSyncHooks,
    StrategyHooks,
    VerifyHooks,
    load_skills_into_context,
)
from openagents_orchestration.models.pattern import (
    FailureGrade,
    PatternError,
    PatternOutcome,
    PatternOutcomeStatus,
)
from openagents_orchestration.persistence import (
    EventRecorder,
    SessionResumer,
    StateSnapshotter,
)
from openagents_orchestration.projects.project import Project
from openagents_orchestration.reporting import (
    build_verification_report,
)
from openagents_orchestration.skills_registry import SkillRegistry
from openagents_orchestration.tools.mcp.adapter import build_mcp_tools
from openagents_orchestration.tools.mcp.client import McpClientManager
from openagents_orchestration.utils.runtime_compat import (
    apply_sdk_patches,
    extract_result_error_message,
    patch_tool_capabilities,
    run_result_error_kwargs,
)

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
    runner: Any  # OrchestratorRunner reference for tool context access
    hooks: Any | None = None  # HookManager for lifecycle hooks (session.start, tool.*, pattern.before_llm)


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
        max_concurrent_spawns: int = 3,
    ):
        self._max_concurrent_spawns = max_concurrent_spawns
        self._config_path = Path(config_path)
        # agent.json 现在是纯 runtime/events 配置；角色定义由 agents/ 编译层加载。
        self._config = self._load_app_config(self._config_path)
        self._agents_by_id = self._load_agent_definitions()
        self._mcp_servers = self._load_mcp_servers(self._config_path)
        self._mcp_manager: McpClientManager | None = None
        self._mcp_tools: dict[str, Any] = {}
        self._bundles: dict[str, _AgentBundle] = {}
        self._sessions = _SessionStore()
        events_config = self._config.get("events")
        if events_config and isinstance(events_config, dict):
            self._event_bus = AsyncEventBus(config=events_config.get("config", {}))
        else:
            self._event_bus = AsyncEventBus()
        self._state_board: StateBoard | None = None
        self._project: Project | None = None
        self._deps: RunnerDeps | None = None
        self._spawn_sem = asyncio.Semaphore(max_concurrent_spawns)
        # Persistence layer
        self._persist_dir = Path(persist_dir) if persist_dir else None
        self._session_id: str | None = None
        self._recorder: EventRecorder | None = None
        self._snapshotter: StateSnapshotter | None = None
        self._resumer: SessionResumer | None = None
        self._session_dir: Path | None = None
        self._current_work_dir: Path | None = None
        # Skill catalog for L1 progressive disclosure (injected at session start).
        self._skill_registry = SkillRegistry()
        # Hook pipeline for corecoder tool/pattern events (tool.before_invoke,
        # tool.after_invoke, pattern.before_llm). Skill loading is hard-coded
        # below instead of going through declarative hooks.
        self._hook_manager = HookManager()
        self._register_stateboard_hooks()

    @property
    def state_board(self) -> StateBoard | None:
        return self._state_board

    def _register_stateboard_hooks(self) -> None:
        """Wire Pattern outcomes into StateBoard updates via hooks.

        Default handlers live in ``openagents_orchestration.hooks`` so the
        glue logic is reusable and testable. The board reference is updated
        when a run creates the real ``StateBoard``.
        """
        self._state_sync_hooks = StateSyncHooks(self._state_board)
        self._strategy_hooks = StrategyHooks(self._state_board)
        self._failure_hooks = FailureHooks()

        self._hook_manager.register(
            HookEvent.PATTERN_AFTER_EXECUTE,
            self._state_sync_hooks.pattern_after_execute,
        )
        self._hook_manager.register(
            HookEvent.LLM_AFTER_CALL,
            self._state_sync_hooks.llm_after_call,
        )
        self._hook_manager.register(
            HookEvent.ARTIFACT_CLAIMED,
            self._state_sync_hooks.artifact_claimed,
        )
        self._hook_manager.register(
            HookEvent.ARTIFACT_VERIFIED,
            self._state_sync_hooks.artifact_verified,
        )
        self._hook_manager.register(
            HookEvent.AGENT_REGISTERED,
            self._state_sync_hooks.agent_registered,
        )
        self._hook_manager.register(
            HookEvent.STATE_TRANSITION,
            self._state_sync_hooks.state_transition,
        )
        self._hook_manager.register(
            HookEvent.DIRECTOR_ADVISE,
            self._strategy_hooks.director_advise,
        )
        self._hook_manager.register(
            HookEvent.TOOL_FAILURE,
            self._failure_hooks.tool_failure,
        )
        # max_steps 续命:注册在 state_sync 之后、verify 之前。arun 遍历时 apply_outcome
        # 已把 max_steps 的 task 留在 RUNNING（不当 FAILED），续命在此接管（护栏内重 spawn /
        # 护栏外标 FAILED 升级 director fallback）；续命跑完最终 COMPLETED 才轮到 verify。
        self._continuation_hooks = ContinuationHooks(self)
        self._hook_manager.register(
            HookEvent.PATTERN_AFTER_EXECUTE,
            self._continuation_hooks.continuation_after_execute,
        )
        # 完成度核验:注册在 state_sync 之后 → arun 遍历时在 task 标 COMPLETED 之后跑。
        # async handler,由 after_execute 的 await arun 驱动。
        self._verify_hooks = VerifyHooks(self)
        self._hook_manager.register(
            HookEvent.PATTERN_AFTER_EXECUTE,
            self._verify_hooks.verify_after_execute,
        )

    @staticmethod
    def _load_app_config(config_path: Path) -> dict[str, Any]:
        """读 agent.json（纯 runtime/events 配置）。"""
        try:
            with config_path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _load_agent_definitions(self) -> dict[str, Any]:
        """从 ``agents/<role>.json`` 编译层加载角色定义。"""
        agents_dir = self._config_path.parent / "agents"
        try:
            specs = load_agent_specs(agents_dir)
            return {d.id: d for d in specs}
        except AgentSpecError as exc:
            raise ConfigError(
                f"加载 agents/ 角色定义失败: {exc}",
                hint=f"检查 {agents_dir} 下各角色 json",
            ) from exc

    @staticmethod
    def _load_mcp_servers(config_path: Path) -> dict[str, dict[str, Any]]:
        """Load optional MCP server definitions from the raw agent.json.

        The SDK's AppConfig ignores unknown top-level keys, so we read the raw
        JSON to preserve ``mcp_servers`` without changing the schema.
        """
        try:
            with config_path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            return {}
        servers = raw.get("mcp_servers") or {}
        if isinstance(servers, dict):
            return servers
        return {}

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
        # Update hook handlers with the real board reference.
        if hasattr(self, "_state_sync_hooks"):
            self._state_sync_hooks.board = self._state_board
        if hasattr(self, "_strategy_hooks"):
            self._strategy_hooks.board = self._state_board
        return project

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

        The Runner is infrastructure only: it creates the StateBoard, wires
        dependencies, and runs the Director agent. All orchestration decisions
        (classify, decompose, spawn, finalize) live inside DirectorPattern.

        Returns DeliveryReport.
        """
        # work_dir 落地为绝对路径：相对路径在 chdir 后会被下游(_wire_run_deps 的
        # store、注入给 agent 的 cwd 等)二次解析 → 目录套娃。库边界统一绝对化，
        # 保证编排内部全程是绝对路径(任何入口传相对都在此被规整)。
        self._current_work_dir = Path(work_dir).resolve() if work_dir else Path.cwd()
        self._session_id = session_id or f"session-{uuid.uuid4().hex[:8]}"
        print(f"\n[Orchestrator] Starting: {objective}", file=sys.stderr, flush=True)
        print(f"[Orchestrator] Session: {self._session_id}", file=sys.stderr, flush=True)
        if work_dir:
            print(f"[Orchestrator] Work dir: {self._current_work_dir}", file=sys.stderr, flush=True)

        cm = contextlib.chdir(self._current_work_dir) if work_dir else contextlib.nullcontext()
        with cm:
            await self._ensure_mcp_connected()
            try:
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

                if resume and self._resumer is not None:
                    await self._resume_run(objective)
                else:
                    await self._start_run(objective, budget=budget)

                # Wire runtime dependencies for tools.
                await self._wire_run_deps()

                # Hand control to the Director.
                print("[Orchestrator] Running Director...", file=sys.stderr, flush=True)
                await self.run_agent("director", objective, agent_id="director-root")

                # Auto-finalize if the Director exited without calling finalize.
                if self._state_board is not None and not self._state_board._final_summary:
                    await self._auto_finalize()

                return self._build_report()
            finally:
                await self._close_mcp()

    async def _resume_run(self, objective: str) -> None:
        """Resume from a persisted snapshot."""

        loaded = self._resumer.load(self._session_id)
        if loaded.snapshot is None:
            return

        print("[Orchestrator] Resuming from snapshot...", file=sys.stderr, flush=True)
        if "state_board" in loaded.snapshot:
            project = Project.from_dict(loaded.snapshot)
        else:
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
        if self._state_board is not None:
            self._state_board._recorder = self._recorder
            self._state_board._snapshotter = self._snapshotter
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

    async def _start_run(self, objective: str, *, budget: Budget | None = None) -> None:
        """Initialize a fresh StateBoard for the objective."""
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
        project.state_board._recorder = self._recorder
        project.state_board._snapshotter = self._snapshotter
        await project.state_board.validate_redis()
        project.start()

    async def _wire_run_deps(self) -> None:
        """Build the RunnerDeps passed to agent tools."""
        self._deps = RunnerDeps(
            state_board=self._state_board,
            runner_delegate=self.run_agent,
            runner=self,
            hooks=self._hook_manager,
        )

    async def _auto_finalize(self) -> None:
        """Generate a final summary if the Director exited without finalizing."""
        import time

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

    def _build_report(self) -> Any:
        """Assemble the DeliveryReport from the final StateBoard state."""
        import time

        elapsed = round(time.time() - self._state_board.budget.start_time, 1)
        print(
            f"[Orchestrator] Finished in {elapsed}s. "
            f"Tokens: {self._state_board.budget.token_used}/{self._state_board.budget.token_limit}. "
            f"Steps: {self._state_board.budget.steps_taken}/{self._state_board.budget.max_steps}.",
            file=sys.stderr,
            flush=True,
        )
        report = self._state_board.to_report()
        report.metadata["verification_report"] = build_verification_report(
            self._state_board,
            work_dir=self._current_work_dir,
        )
        return report

    async def run_agent(
        self,
        agent_type: str,
        input_text: str,
        agent_id: str | None = None,
        state: dict[str, Any] | None = None,
    ) -> PatternOutcome:
        """Run any agent once and return a structured ``PatternOutcome``.

        ``state`` seeds the pattern's ``ctx.state`` (e.g. the
        ``__sub_agent_depth__`` counter for bounded recursive spawning). It
        defaults to None for all top-level/director callers; only ``sub_agent``
        passes it to propagate the recursion depth to the child.

        This is the single execution primitive used by the Director's
        ``spawn_agent`` tool and by ``Runner.run()``. StateBoard mutations flow
        through the ``pattern.after_execute`` hook; this method does not update
        global state directly.
        """
        agent_id = agent_id or f"{agent_type}-{uuid.uuid4().hex[:6]}"

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
        sub_board = None
        if agent_type == "team_leader" and self._state_board is not None:
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
                    hooks=self._hook_manager,
                )

        async with self._spawn_sem:
            result = await self._run_single(
                agent_id, agent_type, input_text,
                deps_override=deps_override, state=state,
            )

        # Translate SDK RunResult into PatternOutcome
        awaiting_human = result.metadata.get("awaiting_human_reply") if result.metadata else None
        if result.stop_reason == StopReason.FAILED:
            outcome = PatternOutcome(
                output=str(result.final_output or ""),
                status=PatternOutcomeStatus.FAILED,
                usage=result.usage,
                error=PatternError(
                    message=extract_result_error_message(result) or "agent failed",
                    grade=FailureGrade.AGENT_FATAL,
                ),
            )
        elif result.stop_reason == StopReason.MAX_STEPS:
            outcome = PatternOutcome(
                output=str(result.final_output or ""),
                status=PatternOutcomeStatus.MAX_STEPS,
                usage=result.usage,
                error=PatternError(
                    message="step budget exhausted",
                    grade=FailureGrade.RECOVERABLE,
                ),
            )
        elif awaiting_human:
            outcome = PatternOutcome(
                output=str(result.final_output or ""),
                status=PatternOutcomeStatus.AWAITING_HUMAN,
                usage=result.usage,
                metadata={"awaiting_human_reply": awaiting_human},
            )
        else:
            result_output = str(result.final_output or "")
            if agent_type == "team_leader" and sub_board is not None:
                summary, _ = self._summarize_team_sub_board(sub_board)
                if summary:
                    result_output = summary
            outcome = PatternOutcome(
                output=result_output,
                status=PatternOutcomeStatus.COMPLETED,
                usage=result.usage,
                metadata={
                    "agent_id": agent_id,
                    "steps_used": result.metadata.get("steps_used", 0) if result.metadata else 0,
                    "tool_calls_used": result.metadata.get("tool_calls_used", 0) if result.metadata else 0,
                },
            )

        # Hook: pattern.after_execute — StateBoard updates happen here.
        if self._hook_manager is not None:
            await self._hook_manager.arun(
                HookEvent.PATTERN_AFTER_EXECUTE,
                {
                    "outcome": outcome,
                    "agent_id": agent_id,
                    # task_id was derived above by stripping the real agent_type
                    # prefix (line ~604), so it is correct for any role — including
                    # dynamic ones outside infer_task_id's whitelist. Passing it
                    # explicitly lets StateSyncHooks skip the whitelist fallback.
                    "task_id": task_id,
                    "agent_type": agent_type,
                    "result": result,
                    "ctx_artifacts": list(result.artifacts) if result.artifacts else [],
                    "sub_board": sub_board,
                    "work_dir": self._current_work_dir,
                },
            )

        return outcome

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

    # -- internals -----------------------------------------------------------

    async def _run_single(
        self,
        agent_id: str,
        agent_type: str,
        input_text: str,
        budget: RunBudget | None = None,
        transcript_override: list[dict[str, Any]] | None = None,
        deps_override: RunnerDeps | None = None,
        state: dict[str, Any] | None = None,
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
        state = dict(state) if state else {}

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

        # Inject skill catalog at session start (hard-coded; not declarative hook).
        load_skills_into_context({
            "context": ctx,
            "agent_type": agent_type,
            "tool_names": list(bundle.plugins.tools.keys()),
            "registry": self._skill_registry,
        })

        # Execute
        try:
            outcome = await pattern.execute()
            # PIT-001: pattern.execute() 返回 PatternOutcome；取 .output 解包成纯文本。
            # 否则下游 str(final_output) 得到 "PatternOutcome(output=...)" 的 repr，
            # 污染 director 看 coder、coder 看 subagent 的所有输出。状态判断走 ctx.state，
            # 不依赖这里的 outcome.status，故只取 .output 是安全的。
            final_output = getattr(outcome, "output", outcome)
        except Exception as exc:
            if agent_type != "director":
                self._print_agent_trace(agent_id, agent_type, ctx, exc=exc)
            # Track resource usage on failure
            if self._state_board is not None:
                self._state_board.add_tokens(usage.total_tokens)
                self._state_board.add_steps(ctx.state.get("__steps_used__", 0))
                print(
                    f"[LLMUsage] {agent_id}: input={usage.input_tokens} "
                    f"output={usage.output_tokens} total={usage.total_tokens} "
                    f"cached={usage.input_tokens_cached} "
                    f"cache_creation={usage.input_tokens_cache_creation} "
                    f"(failed: {exc.__class__.__name__})",
                    file=sys.stderr,
                    flush=True,
                )
                summary = {
                    "agent_id": agent_id,
                    "task_id": self._task_id_from_agent_id(agent_type, agent_id),
                    "status": "failed",
                    "message": str(exc),
                    "steps_used": ctx.state.get("__steps_used__", 0),
                    "token_used": usage.total_tokens,
                }
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

        # Detect step budget exhaustion or human-wait state signalled by the pattern.
        stop_reason = StopReason.COMPLETED
        if ctx.state.get("__step_budget_exhausted__"):
            stop_reason = StopReason.MAX_STEPS

        awaiting_human = ctx.state.get("__awaiting_human_reply__")

        # Memory writeback
        if memory is not None and not awaiting_human:
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
        if agent_type != "director" and not awaiting_human:
            self._print_agent_trace(agent_id, agent_type, ctx, final_output=final_output)

        # Track token usage on success
        if self._state_board is not None:
            self._state_board.add_tokens(usage.total_tokens)
            self._state_board.add_steps(steps_used)
            print(
                f"[LLMUsage] {agent_id}: input={usage.input_tokens} "
                f"output={usage.output_tokens} total={usage.total_tokens} "
                f"cached={usage.input_tokens_cached} "
                f"cache_creation={usage.input_tokens_cache_creation}",
                file=sys.stderr,
                flush=True,
            )
            status = "waiting_for_human" if awaiting_human else "completed"
            summary = {
                "agent_id": agent_id,
                "task_id": self._task_id_from_agent_id(agent_type, agent_id),
                "status": status,
                "message": str(final_output or ""),
                "steps_used": steps_used,
                "token_used": usage.total_tokens,
            }
            self._state_board.log_event(
                "agent.run_summary",
                task_id=summary["task_id"],
                agent_id=agent_id,
                message=status,
                summary=summary,
            )

        metadata: dict[str, Any] = {
            "agent_id": agent_id,
            "steps_used": steps_used,
            "tool_calls_used": tool_calls_used,
            # 续命死循环护栏（ContinuationHooks）读取的机械空转信号
            "consecutive_tool_failures": ctx.state.get("__consecutive_tool_failures__", 0),
            "consecutive_empty_responses": ctx.state.get("__consecutive_empty_responses__", 0),
            "transcript": list(ctx.transcript),
        }
        if awaiting_human:
            metadata["awaiting_human_reply"] = awaiting_human

        result = RunResult(
            run_id=request.run_id,
            final_output=str(final_output or ""),
            stop_reason=stop_reason,
            usage=usage,
            artifacts=list(ctx.artifacts),
            metadata=metadata,
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

    async def _ensure_mcp_connected(self) -> None:
        """Connect to configured MCP servers if not already connected.

        Failures are logged but not fatal — agents continue with built-in tools.
        """
        if self._mcp_manager is not None:
            return
        if not self._mcp_servers:
            return
        self._mcp_manager = McpClientManager(self._mcp_servers)
        try:
            results = await self._mcp_manager.connect_all()
            for name, status in results.items():
                if status != "ok":
                    print(
                        f"[Orchestrator] MCP server '{name}' connection: {status}",
                        file=sys.stderr,
                        flush=True,
                    )
                else:
                    print(
                        f"[Orchestrator] MCP server '{name}' connected",
                        file=sys.stderr,
                        flush=True,
                    )
            # Cache tool adapters so sync _ensure_bundle can inject them later.
            tools_by_server = await self._mcp_manager.list_tools()
            for server_name, tools in tools_by_server.items():
                for adapter in build_mcp_tools(self._mcp_manager, server_name, tools):
                    self._mcp_tools[adapter.name] = adapter
        except Exception as exc:
            print(
                f"[Orchestrator] MCP connection failed: {exc}; continuing without MCP tools.",
                file=sys.stderr,
                flush=True,
            )
            self._mcp_manager = None
            self._mcp_tools.clear()

    async def _close_mcp(self) -> None:
        if self._mcp_manager is not None:
            await self._mcp_manager.close()
            self._mcp_manager = None
        self._mcp_tools.clear()

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
        # Inject cached MCP tools if available.
        if self._mcp_tools:
            plugins.tools.update(self._mcp_tools)
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
        for bundle in self._bundles.values():
            memory = getattr(bundle.plugins,"memory", None)
            if memory is not None and hasattr(memory, "close"):
                await memory.close()
        # Close composed services in StateBoard (Redis connections, etc.)
        if self._state_board is not None:
            await self._state_board.close()
        await self._event_bus.close()

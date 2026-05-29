"""OrchestratorRunner — multi-agent runner built on CoreCoder infrastructure.

Extends CoreCoderLocalRunner patterns to support:
- Multiple agent types (director + tactical agents)
- StateBoard for global coordination
- Agent-to-agent messaging
- Async event bus for observability
"""

from __future__ import annotations

import asyncio
import json
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

from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus
from openagents_orchestration.resident import ResidentAgent
from openagents_orchestration.utils.runtime_compat import (
    apply_sdk_patches,
    extract_result_error_message,
    is_retryable_llm_error,
    patch_tool_capabilities,
    run_result_error_kwargs,
)
from openagents_orchestration.state_board import Budget, StateBoard
from openagents_orchestration.persistence import EventRecorder, StateSnapshotter, SessionResumer

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
    """In-memory session store."""

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
        self._messages[session_id] = list(messages)
        self._artifacts[session_id] = list(artifacts)


@dataclass
class RunnerDeps:
    """Dependencies passed to agent context for cross-cutting concerns."""

    state_board: StateBoard
    runner_delegate: Any  # callable: (agent_type, input_text, agent_id=None) -> str
    runner: Any  # OrchestratorRunner reference for resident management


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
    ):
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
        self._deps: RunnerDeps | None = None
        self._spawn_sem = asyncio.Semaphore(3)
        self._observer_resident_id: str | None = None
        # Persistence layer
        self._persist_dir = Path(persist_dir) if persist_dir else None
        self._session_id: str | None = None
        self._recorder: EventRecorder | None = None
        self._snapshotter: StateSnapshotter | None = None
        self._resumer: SessionResumer | None = None
        self._session_dir: Path | None = None

    @property
    def state_board(self) -> StateBoard | None:
        return self._state_board

    # -- public API ----------------------------------------------------------

    async def run(
        self,
        objective: str,
        *,
        session_id: str | None = None,
        resume: bool = False,
    ) -> Any:
        """Run full orchestration for an objective.

        Args:
            objective: The high-level goal.
            session_id: Optional session ID for persistence. If not provided,
                a new UUID is generated.
            resume: If True and a persisted session with ``session_id`` exists,
                load it and continue from the saved state.

        Returns DeliveryReport.
        """
        import sys

        self._session_id = session_id or f"session-{uuid.uuid4().hex[:8]}"
        print(f"\n[Orchestrator] Starting: {objective}", file=sys.stderr, flush=True)
        print(f"[Orchestrator] Session: {self._session_id}", file=sys.stderr, flush=True)

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
                self._state_board = StateBoard.from_dict(
                    loaded.snapshot,
                    recorder=self._recorder,
                    snapshotter=self._snapshotter,
                )
                # Replay events after snapshot
                if loaded.events_after:
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
        # 1. Initial decomposition
        print("[Orchestrator] Decomposing objective into tasks...", file=sys.stderr, flush=True)
        task_graph = await self._initial_decompose(objective)
        print(f"[Orchestrator] Decomposed into {len(task_graph.tasks)} task(s)", file=sys.stderr, flush=True)

        # 2. StateBoard (with persistence hooks)
        self._state_board = StateBoard(
            objective=objective,
            budget=Budget(
                token_limit=200_000,
                time_limit_s=1200.0,
                max_steps=100,
            ),
            recorder=self._recorder,
            snapshotter=self._snapshotter,
        )
        self._state_board.add_tasks(task_graph)

        return await self._continue_run(objective)

    async def _continue_run(self, objective: str) -> Any:
        """Continue orchestration from an initialized StateBoard."""
        import sys

        if self._state_board is None:
            raise RuntimeError("StateBoard was not initialized")

        # 1. Bridge SDK event bus → StateBoard (for behavioral profiling)
        self._bridge_sdk_events()

        # 2. Spawn observer resident
        await self._spawn_observer_resident()

        # 3. Deps for tools
        self._deps = RunnerDeps(
            state_board=self._state_board,
            runner_delegate=self.run_agent,
            runner=self,
        )

        # 4. Run director
        snapshot = self._state_board.snapshot()
        director_input = (
            f"# Objective\n{objective}\n\n"
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

        # Auto-finalize if director exited without a proper finalize
        if not self._state_board._final_summary:
            summary = (
                f"[Auto-finalized] Director exited without finalize. "
                f"Progress: {self._state_board.progress_summary()}"
            )
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
        return self._state_board.to_report()

    # -- SDK event bus bridge ------------------------------------------------

    def _bridge_sdk_events(self) -> None:
        """Forward SDK AsyncEventBus events to StateBoard for behavioral profiling."""
        if self._state_board is None:
            return

        async def on_sdk_event(runtime_event: RuntimeEvent) -> None:
            name = runtime_event.name
            payload = dict(runtime_event.payload)
            agent_id = payload.get("agent_id", "unknown")

            if name in ("llm.succeeded", "llm.failed"):
                metrics = payload.get("_metrics")
                if metrics and self._state_board is not None:
                    agent = self._state_board.get_agent(agent_id)
                    if agent:
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

    async def _spawn_observer_resident(self) -> None:
        """Spawn the observer resident agent for continuous monitoring."""
        if self._state_board is None:
            return
        try:
            if "observer" not in self._agents_by_id:
                print("[Orchestrator] Observer agent not configured, skipping.", file=sys.stderr, flush=True)
                return

            resident_id = f"observer-{uuid.uuid4().hex[:6]}"

            resident = ResidentAgent(
                resident_id=resident_id,
                agent_type="observer",
                runner=self,
                board=self._state_board,
            )
            await resident.start()
            self._residents[resident_id] = resident
            self._observer_resident_id = resident_id
            self._state_board.register_resident(resident.state)
            print(f"[Orchestrator] Observer resident spawned: {resident_id}", file=sys.stderr, flush=True)
        except Exception as exc:
            print(f"[Orchestrator] Failed to spawn observer: {exc}", file=sys.stderr, flush=True)

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
        print(
            f"[Orchestrator] Spawning {agent_type} ({agent_id})...",
            file=sys.stderr,
            flush=True,
        )
        async with self._spawn_sem:
            result = await self._run_single(agent_id, agent_type, input_text)

        # Extract metrics and record to StateBoard (even on failure)
        tokens = result.usage.total_tokens if result.usage else 0
        steps = result.metadata.get("steps_used", 0) if result.metadata else 0
        if self._state_board is not None:
            self._state_board.update_agent(
                agent_id, token_used=tokens, steps_used=steps
            )
            self._state_board.add_steps(steps)

        if result.stop_reason == StopReason.FAILED:
            msg = extract_result_error_message(result)
            print(f"[Orchestrator] {agent_type} ({agent_id}) FAILED: {msg}", file=sys.stderr, flush=True)
            raise RuntimeError(msg)
        # Print execution summary + monitor thresholds
        tokens = result.usage.total_tokens if result.usage else 0
        steps = result.metadata.get("steps_used", "?") if result.metadata else "?"
        tool_calls = result.metadata.get("tool_calls_used", "?") if result.metadata else "?"
        output_preview = str(result.final_output or "")[:200].replace("\n", " ")

        # Threshold warnings
        warnings: list[str] = []
        if isinstance(steps, int) and steps >= 15:
            warnings.append(f"HIGH_STEP_COUNT({steps})")
        if tokens > 50000:
            warnings.append(f"HIGH_TOKEN({tokens})")
        warning_str = f" [{' | '.join(warnings)}]" if warnings else ""

        print(
            f"[Orchestrator] {agent_type} ({agent_id}) done.{warning_str} "
            f"tokens={tokens}, steps={steps}, tools={tool_calls}. "
            f"Output: {output_preview}{'...' if len(str(result.final_output or '')) > 200 else ''}",
            file=sys.stderr,
            flush=True,
        )

        # If thresholds exceeded, raise with resident-fallback recommendation
        if warnings:
            raise RuntimeError(
                f"Agent {agent_id} exceeded thresholds: {', '.join(warnings)}. "
                f"[recommendation: spawn resident — agent is stuck in a loop, "
                f"use a persistent resident coder to complete this task]"
            )

        return str(result.final_output or "")

    # -- resident agents -----------------------------------------------------

    async def start_resident(self, agent_type: str) -> str:
        """Start a persistent resident agent.

        Returns the resident_id.
        """
        if self._state_board is None:
            raise RuntimeError("StateBoard not initialized")
        resident_id = f"{agent_type}-resident-{uuid.uuid4().hex[:6]}"
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
    ) -> RunResult[str]:
        """Run one shot for a resident agent with persistent transcript."""
        return await self._run_single(
            agent_id=resident_id,
            agent_type=agent_type,
            input_text=input_text,
            transcript_override=transcript,
        )

    # -- internals -----------------------------------------------------------

    async def _initial_decompose(self, objective: str) -> TaskGraph:
        """Use the director's LLM to decompose the objective into a TaskGraph."""
        director_agent = self._agents_by_id.get("director")
        if director_agent is None or director_agent.llm is None:
            raise ConfigError("Director agent not configured")

        llm = create_llm_client(director_agent.llm)
        agents_info = self._build_agents_info()

        prompt = (
            f"Decompose the following objective into a structured task graph.\n\n"
            f"Objective: {objective}\n\n"
            f"Available agent types:\n{agents_info}\n\n"
            f"Rules:\n"
            f"1. Each task has a unique task_id (t1, t2, ...)\n"
            f"2. List dependencies explicitly\n"
            f"3. EACH TASK SHOULD HAVE AT MOST 3-5 expected_artifacts (files). "
            f"If a task needs more than 5 files, SPLIT IT into smaller subtasks.\n"
            f"4. For project scaffold/init tasks, split into granular subtasks: "
            f"   - t1: project structure + config files (pyproject.toml, requirements.txt)\n"
            f"   - t2: core module files (config, database, security)\n"
            f"   - t3: models and schemas\n"
            f"   - t4: API routes and services\n"
            f"5. Keep the graph shallow (2-4 layers)\n"
            f"6. input_context: detailed instructions for the agent\n"
            f"7. coder agents have a step budget of ~30 steps. A task creating 10+ files will fail.\n\n"
            f'Output strict JSON: {{"tasks": [...]}}\n'
            f'Each task: {{"task_id": "t1", "description": "...", '
            f'"input_context": "...", "agent_type": "coder", '
            f'"dependencies": [], "expected_artifacts": ["file.py"]}}'
        )

        response = None
        last_exc: BaseException | None = None
        for attempt in range(3):
            try:
                response = await llm.generate(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                    max_tokens=4096,
                    tools=None,
                )
                break
            except BaseException as exc:
                last_exc = exc
                if attempt == 2 or not is_retryable_llm_error(exc):
                    raise
                await asyncio.sleep(1.5 * (attempt + 1))
        if response is None and last_exc is not None:
            raise last_exc
        text = response.output_text or ""
        # Track decomposer token usage
        if self._state_board is not None and response.usage is not None:
            self._state_board.add_tokens(response.usage.total_tokens)
        return self._parse_task_graph(text, objective)

    def _build_agents_info(self) -> str:
        lines = []
        for aid, agent in self._agents_by_id.items():
            if aid == "director":
                continue
            lines.append(f"- {aid}: tactical agent")
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
    ) -> RunResult[str]:
        """Run one agent (director or tactical) — based on CoreCoderLocalRunner."""
        bundle = self._ensure_bundle(agent_type)
        request = RunRequest(
            agent_id=agent_id,
            session_id=f"session-{agent_id}",
            input_text=input_text,
            budget=budget or self._default_budget(bundle.agent),
            deps=self._deps,
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
            # Track token usage on failure
            if self._state_board is not None:
                self._state_board.add_tokens(usage.total_tokens)
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

        result = RunResult(
            run_id=request.run_id,
            final_output=str(final_output or ""),
            stop_reason=StopReason.COMPLETED,
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

    def _ensure_bundle(self, agent_id: str) -> _AgentBundle:
        if agent_id in self._bundles:
            return self._bundles[agent_id]

        agent = self._agents_by_id.get(agent_id)
        if agent is None:
            raise ConfigError(
                f"Unknown agent id: '{agent_id}'",
                hint=f"Available: {sorted(self._agents_by_id)}",
            )
        if agent.llm is None:
            raise ConfigError(f"Agent '{agent_id}' has no llm configured")

        plugins = load_agent_plugins(agent)
        llm_client = create_llm_client(agent.llm)
        bundle = _AgentBundle(agent=agent, plugins=plugins, llm_client=llm_client)
        self._bundles[agent_id] = bundle
        return bundle

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

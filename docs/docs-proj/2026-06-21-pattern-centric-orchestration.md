# Pattern-Centric Orchestration Rework

> Status: implementation in progress  
> Date: 2026-06-21  
> Updated: 2026-06-21  
> Scope: `src/openagents_orchestration/core/runner.py`, `src/openagents_orchestration/patterns/`, `src/openagents_orchestration/core/state_board.py`, hooks system, new Director tools (`classify_intent`, `decompose`)

> This doc describes the target architecture and the current implementation state. Items marked **✅ implemented** are in the tree; items marked **⏸️ deprioritized** are intentionally deferred; items marked **❌ not yet** are still TODO.

## 1. Background

The current orchestrator has an unclear split of responsibilities:

- `Runner` does intent classification, task decomposition, Director loop, collaborative-mode switching, artifact path resolution, and strategy-signal generation.
- `Pattern` only runs the inner ReAct loop and emits opaque `ctx.state["__xxx__"]` flags that `Runner` re-interprets.
- `StateBoard` is a passive store but also contains advisory methods (`suggest_fallback`, `strategy_signals`, `suggest_tools`).
- Data flow from Pattern to StateBoard is mostly direct method calls from Runner, not a uniform hook mechanism.

This document proposes a cleaner model:

> **Pattern is the orchestration brain. Runner is the runtime container. StateBoard is the single source of truth. Hooks are the glue.**

## 2. Design Decisions (Confirmed)

The following decisions were confirmed during design review:

| # | Topic | Decision | State |
|---|-------|----------|-------|
| 1 | Runner scope | Runner is infrastructure only: registry, session, persistence, event bus, MCP, concurrency limits. No orchestration logic. | ✅ implemented |
| 2 | Pattern split | CoreCoderPattern = single-agent ReAct engine. DirectorPattern = orchestration brain with its own ReAct loop. | ✅ implemented |
| 3 | `run_agent` | Single execution primitive. Replaces `_run_single` as the public face; internally still uses `_run_single`. Returns `PatternOutcome`. | ✅ implemented |
| 4 | `spawn_agent` tool | Delegation only: dependency check, build input, call `run_agent`, retry transient errors. No artifact verification or hallucination detection. | ✅ implemented |
| 5 | Director reads StateBoard | Via `show_state` tool only. No dynamic prompt injection of full snapshot. Persistent snapshot file is for external observation/resume only. | ✅ implemented |
| 6 | StateBoard | Passive store only. Strategy advice removed to `StrategyAdvisor` + hooks. | ⏸️ deprioritized (`suggest_*` still present but unused) |
| 7 | Hooks | Pattern-to-StateBoard glue. Hook points cover pattern/llm/tool/artifact/agent lifecycle and director advice. | ✅ implemented (Phase 2a) |
| 8 | `PatternOutcome` | Minimal: `status`, `output`, `usage`, `error`. Artifacts and verification flow through dedicated hooks. | ✅ implemented |
| 9 | Tool failure | Five-level grading: TRANSIENT, RECOVERABLE, TOOL_FATAL, AGENT_FATAL, ORCHESTRATION_FATAL. | ⏸️ placeholder only (`FailureHooks.tool_failure` exists but does not drive Pattern behavior yet) |
| 10 | Replan | Inside DirectorPattern via `ReplanPolicy`. Per-task max 3, global max 10. | ⏸️ not implemented |
| 11 | Agent state model | Five-state: NEW, READY, RUNNING, WAITING, TERMINATED. | ❌ not implemented |
| 12 | Resident lifecycle | DirectorPattern decides spawn/destroy. Runner provides APIs and health checks. | ⏸️ partially (Runner APIs exist, Director does not manage residents yet) |
| 13 | Concurrency | `spawn_agent` synchronous by default. Async event-driven optional in future. | ✅ implemented |
| 14 | Memory | Out of scope for phase 1. | ⏸️ unchanged |
| 15 | Testing | Existing tests may be removed or rewritten. New tests will be written after interfaces stabilize. | ✅ in progress (`tests/test_director_e2e_mock.py` added) |

## 3. Target Architecture

```
┌────────────────────────────────────────────┐
│ L4: Tools                                  │
│ spawn_agent · send_message · ask_human     │
│ edit_file · bash · read_file · ...         │
├────────────────────────────────────────────┤
│ L3: Patterns                               │
│ CoreCoderPattern  : single-agent ReAct     │
│ DirectorPattern   : multi-agent orchestration│
│ TeamLeaderPattern : sub-graph delegation   │
│ ResidentPattern   : persistent message loop│
├────────────────────────────────────────────┤
│ L2: Hooks                                  │
│ state.sync · strategy.advise               │
│ tool.failure · verification.check          │
│ artifact.track · budget.account            │
├────────────────────────────────────────────┤
│ L1: StateBoard (single mutable state source)│
│ tasks · agents · residents · artifacts     │
│ budget · events · mailbox · human_channel  │
├────────────────────────────────────────────┤
│ L0: Runner (infrastructure)                │
│ session · persistence · event_bus          │
│ tool registry · llm client factory         │
│ mcp connection · lifecycle entrypoint      │
└────────────────────────────────────────────┘
```

## 3. Runner: Infrastructure Only

`Runner` must not contain orchestration logic. Its responsibilities are limited to:

1. **Session management**: load/save transcripts, resume snapshots.
2. **Persistence**: `EventRecorder`, `StateSnapshotter`, `SessionResumer`.
3. **Event bus**: SDK `AsyncEventBus` wiring.
4. **Agent/tool registry**: load `agents/` specs, build `ToolPlugin` maps, inject MCP tools.
5. **LLM client factory**: create provider-specific clients from agent specs.
6. **MCP connection lifecycle**: connect on start, close on shutdown.
7. **Lifecycle entrypoint**: `run(objective)` and `close()`.
8. **Concurrency guardrails**: `max_concurrent_spawns`, `max_concurrent_residents`.

Removed from Runner:

- intent classification
- task decomposition
- Director ReAct loop
- collaborative-mode switch
- artifact path resolution / existence checks
- strategy-signal generation
- direct `state_board.update_task` / `update_agent` calls (moved to hooks)

The entrypoint becomes:

```python
async def run(self, objective: str) -> DeliveryReport:
    await self._ensure_mcp_connected()
    self._state_board = self._create_state_board(objective)
    self._deps = RunnerDeps(
        state_board=self._state_board,
        runner_delegate=self.run_agent,
        runner=self,
        hooks=self._hook_manager,
    )
    await self.run_agent("director", objective, agent_id="director-root")
    return self._state_board.to_report()
```

`run_agent` is the single public primitive for executing any agent:

```python
async def run_agent(
    self,
    agent_type: str,
    input_text: str,
    agent_id: str | None = None,
) -> PatternOutcome:
    agent_id = agent_id or f"{agent_type}-{uuid.uuid4().hex[:6]}"
    # ... setup bundle, run _run_single, translate RunResult -> PatternOutcome,
    # trigger pattern.after_execute hook, return outcome
```

`_run_single` remains the internal execution engine; `run_agent` is the thin wrapper that converts its `RunResult` into a `PatternOutcome` and fires the hook pipeline.

## 4. Pattern: The Orchestration Brain

### 4.1 CoreCoderPattern

`CoreCoderPattern` remains the single-agent ReAct engine. It is augmented with explicit internal machinery described in Section 6.

### 4.2 DirectorPattern

`DirectorPattern` extends `CoreCoderPattern` and becomes the multi-agent orchestrator. The Director is a ReAct agent with a Director-specific toolset and prompt; there is no hard-coded orchestration state machine inside the Pattern.

Director responsibilities:

- decide when to classify intent
- decide when to decompose the objective
- decide when to spawn agents or perform work directly
- decide when to ask the human or finalize

DirectorPattern tools:

- `classify_intent` (observation: understand the objective)
- `decompose` (action: build a task graph)
- `show_state` (real-time snapshot, primary observation mechanism)
- `read_file` / `search` (observation of files and persisted state)
- `spawn_agent`
- `spawn_resident`
- `replan`
- `ask_human`
- `finalize`
- `skip_task`
- standard CoreCoder tools (`bash`, `write_file`, `edit_file`, etc.) for any direct work the Director chooses to do

The Director ReAct loop is the standard `CoreCoderPattern` loop:

```python
async def execute(self) -> PatternOutcome:
    # The Director decides inside the loop when to call classify_intent,
    # decompose, spawn_agent, finalize, or any other tool.
    return await super().execute()
```

`_should_accept_text_response()` is overridden so the Director only accepts a text-only final turn after `finalize` has set `board._final_summary`.

The "brain" is the combination of:
- the Director system prompt (`prompts.roles.director:PRINCIPLES`)
- the Director toolset (`agents/director.json`)
- the LLM
- `StateBoard` as the shared memory via `show_state`

### 4.3 TeamLeaderPattern / ResidentPattern

They remain tactical variants that use the same state model and hook system.

## 5. StateBoard and Hooks

### 5.1 StateBoard is Passive

StateBoard exposes only:

- CRUD for tasks, agents, residents, artifacts
- budget accounting
- event logging
- snapshot / report generation
- mailbox / human-channel proxies

It must NOT contain:

- `suggest_fallback`
- `strategy_signals`
- `suggest_tools`
- direct Pattern knowledge

**Current state**: `suggest_fallback` / `strategy_signals` / `suggest_tools` still exist on `StateBoard` but are no longer used by the Director. They are scheduled for removal in Phase 2b (currently deprioritized).

### 5.2 Hooks Are the Glue

Extend the `HookManager` registry:

```python
HOOK_REGISTRY = {
    "tool.before_invoke",
    "tool.after_invoke",
    "tool.failure",
    "pattern.before_llm",
    "pattern.before_execute",
    "pattern.after_execute",
    "llm.before_call",
    "llm.after_call",
    "artifact.claimed",
    "artifact.verified",
    "agent.registered",
    "agent.completed",
    "agent.failed",
    "director.advise",
    "budget.exhausted",
    "state.transition",
}
```

Default system hooks (implemented in `openagents_orchestration.hooks`):

```python
class StateSyncHooks:
    def pattern_after_execute(self, payload):
        outcome = payload["outcome"]
        agent_id = payload["agent_id"]
        agent_type = payload["agent_type"]
        self.board.apply_outcome(outcome, task_id=..., agent_id=agent_id, agent_type=agent_type)

    def llm_after_call(self, payload):
        self.board.record_sdk_metrics(payload["agent_id"], payload["metrics"])

    def artifact_claimed(self, payload):
        self.board.claim_artifact(payload["task_id"], payload["paths"])

    def artifact_verified(self, payload):
        self.board.verify_artifact(payload["path"], payload["exists"])

    def agent_registered(self, payload):
        self.board.register_agent(payload["agent_id"], payload["agent_type"])

    def state_transition(self, payload):
        self.board.log_event("state.transition", ...)

class StrategyHooks:
    def director_advise(self, payload):
        # Currently a placeholder: StrategyAdvisor is deprioritized.
        advisor = StrategyAdvisor(payload.get("board"))
        return advisor.suggest()

class FailureHooks:
    def tool_failure(self, payload):
        # Currently a placeholder: grades the failure but does not yet drive
        # Pattern-level retry/disable/abort decisions.
        grade = self._classify(payload["error"], payload.get("exception"))
        return {"grade": grade, "decision": FailureDecision(...)}
```

Pattern code never calls `state_board.update_task` directly; it emits events through hooks.

### 5.3 PatternOutcome

`PatternOutcome` is the minimal, explicit return value of `Pattern.execute()`:

```python
class PatternOutcome:
    status: Literal["completed", "failed", "max_steps", "awaiting_human"]
    output: str
    usage: RunUsage
    error: PatternError | None
```

Artifacts and verification status are **not** carried in `PatternOutcome`. They flow through dedicated hooks (`artifact.claimed`, `artifact.verified`) so that `StateBoard` is updated as soon as the agent creates or verifies a file, rather than waiting until the agent finishes. This avoids duplication between outcome fields and hook events.

`Runner` uses `PatternOutcome` to decide the high-level result of an agent run; `StateBoard.apply_outcome()` is invoked via the `pattern.after_execute` hook.

## 6. Agent Five-State Model

> **Status**: ❌ Not implemented. Kept as a future design note.

Borrowing from OS thread scheduling, agent runtime state uses five explicit states:

| State | Meaning | Transitions |
|-------|---------|-------------|
| `NEW` | Agent registered but not started yet | `NEW → READY` when input prepared |
| `READY` | Agent ready to run, waiting for concurrency slot | `READY → RUNNING` when scheduled |
| `RUNNING` | Agent is executing | `RUNNING → WAITING` on human/blocking I/O; `RUNNING → DONE/FAILED` on completion |
| `WAITING` | Agent paused for human reply, external signal, or sub-task | `WAITING → READY` when unblocked |
| `TERMINATED` | Agent finished (`DONE` or `FAILED`) | terminal |

Mapping to current `AgentStatus`:

- `NEW` → new
- `READY` → idle / pending
- `RUNNING` → running
- `WAITING` → waiting_for_human / stalled
- `TERMINATED` → done / failed

Benefits:

- clearer lifecycle for both one-shot agents and residents
- `WAITING` captures not only human-await but also blocked-on-subtask states
- `READY` makes concurrency scheduling explicit

Task state machine remains separate but analogous (`PENDING`, `READY`, `RUNNING`, `WAITING`, `REVIEW`, `FIX_NEEDED`, `COMPLETED`, `FAILED`, `SKIPPED`, `REPLANNED`).

## 7. Pattern Internal Mechanisms

> **Status**: The sections below describe the intended end-state design. They are **not yet implemented** in the current code. The current code uses the simpler ReAct-loop Director described in §4.2 and the placeholder hooks described in §5.2.

These mechanisms belong inside Pattern, not Runner.

### 7.1 Tool Failure Grading

`_dispatch_single_tool` classifies failures before deciding whether to return them to the LLM:

| Grade | Examples | Pattern Action |
|-------|----------|----------------|
| `TRANSIENT` | 429, timeout, connection reset | retry with exponential backoff |
| `RECOVERABLE` | file not found, old_string mismatch, bad params | return error to LLM |
| `TOOL_FATAL` | permission denied, PermanentToolError | disable tool temporarily, return error |
| `AGENT_FATAL` | MCP down, repeated LLM failures | abort agent, return failed outcome |
| `ORCHESTRATION_FATAL` | budget exhausted, impossible objective | trigger finalize or ask_human |

Decision object:

```python
class FailureDecision:
    action: Literal["retry", "escalate", "disable_tool", "abort_agent", "abort_run"]
    delay: float | None
    reason: str
```

**Current state**: `FailureDecision` and `FailureGrade` exist in `models/pattern.py`, and `FailureHooks.tool_failure` provides a basic classifier. The Pattern does **not yet** read the hook's `decision` to drive retry/disable/abort behavior; failures are still returned to the LLM as tool errors.

### 7.2 Director Decision Policy

> **Status**: ❌ Not implemented. The current Director has no `DecisionPolicy`; the LLM decides directly via tool calls.

DirectorPattern uses a pluggable `DecisionPolicy` instead of prompt rules:

```python
class DirectorDecisionPolicy:
    def decide(self, snapshot, history, context) -> DirectorAction:
        if snapshot.budget.exhausted:
            return Finalize(reason="budget_exhausted")
        if snapshot.needs_human:
            return WaitForHuman()
        for task in snapshot.failed_tasks:
            if history.fallback_count(task) >= 2:
                return Replan(task_id=task.task_id)
            if self._should_ask_human(task):
                return AskHuman(task_id=task.task_id)
        ready = snapshot.ready_to_run
        if ready:
            if self._use_resident(ready[0]):
                return SpawnResident(task_id=ready[0].task_id)
            return SpawnAgent(task_ids=[t.task_id for t in ready[:batch]])
        if snapshot.all_terminal():
            return Finalize()
        return WaitForSignal(timeout=5.0)
```

### 7.3 Replan Policy

> **Status**: ⏸️ Not implemented. The `replan` tool exists and updates the task graph, but there is no `ReplanPolicy` enforcing budgets or trigger conditions.

Replan is triggered when:

- same task fails twice with same agent_type
- task step-budget exhausted with no verified artifacts
- human clarifies that task spec is wrong
- upstream dependency failed and downstream cannot proceed
- global decision success rate drops below threshold

Replan actions:

- decompose task into subtasks
- change agent_type
- add dependency corrections
- generate clarification question

Replan budget:

- per-task max 3 replans
- global max 10 replans per run
- exceeded → `ask_human` or `finalize`

### 7.4 DirectorPattern State Machine

> **Status**: ❌ Not implemented. The current Director is a single ReAct loop; there is no explicit state machine.

```
SETUP
  │
  ▼
CLASSIFY ──► simple ──► DIRECT_SOLO
  │
  ▼
DECOMPOSE
  │
  ▼
ORCHESTRATE
  │
  ├── spawn_agent / spawn_resident
  │
  ├── handle result / failure
  │
  └── loop
  │
  ▼
FINALIZE
```

Events drive transitions:

- `intent_complex` → `CLASSIFY → DECOMPOSE`
- `tasks_imported` → `DECOMPOSE → ORCHESTRATE`
- `all_terminal` → `ORCHESTRATE → FINALIZE`
- `budget_exhausted` → any → `FINALIZE`
- `human_reply` → `WAITING → ORCHESTRATE`

### 7.5 Failure Escalation Ladder

> **Status**: ⏸️ Not implemented. The current Pattern returns tool errors to the LLM; there is no automatic escalation ladder.

For any failed task:

```
retry same agent_type (×2)
    │
    ▼
switch agent_type
    │
    ▼
replan (decompose / change approach)
    │
    ▼
ask_human
    │
    ▼
skip (if non-critical)
    │
    ▼
finalize with partial failure
```

## 8. Why CoreCoderPattern Also Needs These Mechanisms

The failure-grading, retry, and guardrail mechanisms are not Director-only. A single `coder` agent also needs:

- transient error retry for LLM calls and file I/O
- clear handling when `edit_file` repeatedly fails
- explicit state for pending verification
- budget-aware step warnings
- recovery suggestions when exploration stalls

Currently these are spread across `CoreCoderPattern` methods (`_build_edit_recovery_message`, `_build_diagnosis_message`, tool gating). They should be refactored into the same reusable components:

- `ToolFailureHandler`
- `ExplorationGuard`
- `VerificationTracker`
- `BudgetGuard`

So the rework benefits both tactical and orchestrator Patterns.

## 9. Migration Path

Phase 1: documentation and interfaces

1. Finalize this design doc.
2. Define `PatternOutcome` dataclass.
3. Extend `HookManager` registry.
4. Sketch `DecisionPolicy`, `ReplanPolicy`, `ToolFailureHandler` interfaces.

Phase 2: hook-driven StateBoard

1. Implement `StateSyncHooks`, `StrategyHooks`, `FailureHooks`.
2. Replace direct `state_board.update_*` calls in Runner with hook emissions.
3. Remove `suggest_fallback`, `strategy_signals`, `suggest_tools` from StateBoard into `StrategyAdvisor`.

Phase 3: Runner diet

1. Move `_classify_intent` and `_initial_decompose` into `DirectorPattern`.
2. Move `_run_director_mode` loop into `DirectorPattern.execute`.
3. Delete `_run_single`; collapse into `run_agent`.
4. Remove artifact path resolution from Runner; move to `ArtifactService` called via hooks.

Phase 4: CoreCoderPattern refactor

1. Extract `ToolFailureHandler`, `VerificationTracker`, `ExplorationGuard`, `BudgetGuard`.
2. Replace ad-hoc guardrail methods with these components.

Phase 5: agent five-state model

1. Update `AgentStatus` enum.
2. Update Runner scheduling and resident lifecycle.
3. Update StateBoard snapshot and reports.

Phase 6: testing

1. Remove or rewrite obsolete tests that no longer match the new interfaces.
2. Write new unit tests for `DecisionPolicy`, `ReplanPolicy`, `ToolFailureHandler` as pure functions.
3. Write mock-LLM integration tests for `DirectorPattern`.
4. Run full regression via `uv run pytest tests/ -q`.

## 10. Component Interaction and Interfaces

This section consolidates the roles, interfaces, and data flows of the six key components: `Runner`, `Pattern`, `run_agent`, `spawn_agent`, `DirectorPattern`, and `StateBoard`.

### 10.1 Runner

**One-sentence role**: the runtime container that provides infrastructure for agents to execute.

**Owns**:
- agent/tool registry loading
- LLM client creation
- session persistence
- event bus wiring
- MCP connection lifecycle
- concurrency limits (spawns, residents)
- top-level entrypoint `run(objective)`

**Does not own**:
- orchestration decisions
- intent classification
- task decomposition
- artifact verification logic
- strategy advice

**Public interface**:

```python
class OrchestratorRunner:
    async def run(self, objective: str) -> DeliveryReport: ...
    async def run_agent(self, agent_type, input_text, agent_id=None) -> PatternOutcome: ...
    async def start_resident(self, agent_type) -> str: ...
    async def stop_resident(self, resident_id) -> None: ...
    async def close(self) -> None: ...
```

**Current problems**:
- `run()` contains intent classification and decomposition.
- `_run_single` is a thin wrapper that leaks implicit state via `RunResult.metadata`.
- `_bridge_sdk_events` mutates `AgentState` fields directly instead of using hooks.

### 10.2 Pattern

**One-sentence role**: the decision-and-execution engine for a single agent instance.

**Owns**:
- ReAct loop
- system prompt composition
- tool schema rendering
- tool dispatch
- single-agent guardrails (empty responses, read-only budget, tool gating)
- returning a structured `PatternOutcome`

**Does not own**:
- other agents' lifecycles
- global task graph
- global budget management
- persistence

**Public interface**:

```python
class PatternPlugin:
    async def setup(self, **kwargs) -> None: ...
    async def execute(self) -> PatternOutcome: ...
    async def teardown(self) -> None: ...
```

**Subtypes**:

| Pattern | Responsibility |
|---------|----------------|
| `CoreCoderPattern` | single-agent coding assistant |
| `DirectorPattern` | multi-agent orchestration brain |
| `TeamLeaderPattern` | sub-graph delegation within a task |
| `ResidentPattern` | persistent message-loop agent |

### 10.3 run_agent

**One-sentence role**: the single primitive for executing any agent once.

**Responsibilities**:
1. allocate `agent_id`
2. build agent bundle (pattern, tools, llm)
3. setup pattern with `RunContext`
4. inject memory and skills
5. call `pattern.execute()`
6. trigger hooks for state sync
7. save session
8. return `PatternOutcome`

**Interface**:

```python
async def run_agent(
    self,
    agent_type: str,
    input_text: str,
    agent_id: str | None = None,
) -> PatternOutcome: ...
```

**Note**: `_run_single` is removed; its logic merges into `run_agent`.

### 10.4 spawn_agent (Tool)

**One-sentence role**: the tool that a Pattern uses to delegate a ready task to another agent.

**Responsibilities**:
- dependency check
- input text construction
- call `runner_delegate` (= `run_agent`)
- retry transient failures
- classify error and recommend recovery

**Does not own**:
- artifact verification (moved to hook/ArtifactService)
- hallucination detection (moved to VerificationService)
- task state mapping (moved to `StateBoard.apply_outcome` via hooks)

**Interface**:

```python
class SpawnAgentTool(ToolPlugin):
    async def invoke(self, params, context) -> dict[str, Any]: ...
```

**Current problems**:
- extracts artifacts from `result_text` via regex
- performs hallucination detection on expected artifacts
- duplicates artifact claim/verify logic already done by Runner

### 10.5 DirectorPattern (主脑)

**One-sentence role**: the orchestrator agent that decides when to spawn, replan, ask human, or finalize.

**Owns**:
- intent classification
- task decomposition
- task-graph updates
- Director ReAct loop
- `DecisionPolicy` for spawn/replan/ask_human/finalize
- `ReplanPolicy`
- failure escalation for the whole project

**Uses**:
- `show_state` to read snapshot
- `spawn_agent` / `spawn_resident` to delegate
- `replan` to restructure task graph
- `ask_human` to pause for input
- `finalize` to end orchestration

**Internal state machine**:

```
SETUP → CLASSIFY → DECOMPOSE → ORCHESTRATE → FINALIZE
              │
              └──► DIRECT_SOLO (for simple tasks)
```

**Interface**:

```python
class DirectorPattern(CoreCoderPattern):
    async def _classify_intent(self) -> IntentResult: ...
    async def _decompose(self) -> TaskGraph: ...
    async def _decide(self, snapshot, history) -> DirectorAction: ...
    async def _execute_action(self, action: DirectorAction) -> None: ...
```

### 10.6 StateBoard

**One-sentence role**: the single mutable source of truth for the whole orchestration.

**Owns**:
- tasks, agents, residents
- artifacts and their lifecycle states
- budget (token/step/time)
- event log
- mailbox / human-channel proxies
- snapshot generation
- final `DeliveryReport`

**Does not own**:
- orchestration strategy
- decision logic
- Pattern execution details

**Public interface**:

```python
class StateBoard:
    # tasks
    def add_tasks(self, graph: TaskGraph) -> None: ...
    def update_task(self, task_id: str, **fields) -> None: ...
    def get_task(self, task_id: str) -> TaskNode | None: ...
    def tasks_ready(self) -> list[TaskNode]: ...

    # agents
    def register_agent(self, agent_id, agent_type) -> None: ...
    def update_agent(self, agent_id, **fields) -> None: ...
    def get_agent(self, agent_id) -> AgentState | None: ...

    # artifacts
    def claim_artifact(self, task_id, paths) -> None: ...
    def verify_artifact(self, path, exists=True) -> None: ...

    # budget
    def add_tokens(self, n) -> None: ...
    def add_steps(self, n) -> None: ...

    # events / snapshot / report
    def log_event(self, event_type, **kwargs) -> None: ...
    def snapshot(self) -> dict[str, Any]: ...
    def to_report(self) -> DeliveryReport: ...

    # high-level outcome application
    def apply_outcome(self, task_id: str, outcome: PatternOutcome) -> None: ...
```

**Current problems**:
- contains advisory methods (`suggest_fallback`, `strategy_signals`, `suggest_tools`)
- is mutated directly by Runner in several places
- lacks a single `apply_outcome` primitive

### 10.7 Interaction Diagram

```
User
 │
 ▼
Runner.run(objective)
 │
 ▼
run_agent("director", objective)
 │
 ▼
DirectorPattern.execute()
 │
 ├── _classify_intent()
 ├── _decompose() ──► StateBoard.add_tasks()
 └── ReAct loop
      │
      ├── show_state ──► StateBoard.snapshot()
      │
      ├── spawn_agent tool
      │       │
      │       ├── dependency check ──► StateBoard.get_task()
      │       └── runner_delegate()
      │               │
      │               ▼
      │       run_agent("coder", input)
      │               │
      │               ▼
      │       CoreCoderPattern.execute()
      │               │
      │               ▼
      │       PatternOutcome
      │               │
      │               ▼
      │       hooks.run("pattern.after_execute")
      │               │
      │               ▼
      │       StateBoard.apply_outcome()
      │
      ├── replan tool ──► StateBoard.add_tasks()
      │
      ├── ask_human tool ──► HumanChannelService
      │
      └── finalize tool ──► StateBoard._final_summary
 │
 ▼
StateBoard.to_report() ──► DeliveryReport
```

### 10.8 Data Flow Summary

| Producer | Data | Consumer | Mechanism |
|----------|------|----------|-----------|
| `DirectorPattern` | `TaskGraph` | `StateBoard` | direct call (`add_tasks`) |
| `CoreCoderPattern` | `PatternOutcome` | `StateBoard` | hook (`pattern.after_execute`) |
| `CoreCoderPattern` | LLM metrics | `StateBoard` | hook (`llm.after_call`) |
| Tools | tool results | `StateBoard` | hook (`artifact.claimed`, `artifact.verified`) |
| `StrategyAdvisor` | hints/signals | `DirectorPattern` | hook (`director.advise`) |
| `StateBoard` | snapshot | `DirectorPattern` | `snapshot()` call |
| `StateBoard` | report | User | `to_report()` |

## 11. Open Questions

1. Should `DirectorPattern` spawn tasks synchronously (`spawn_agent` awaits result) or asynchronously with event-driven waits?
2. Should `replan` mutate the existing task graph or produce a new subgraph?
3. How do residents report completion back to `DirectorPattern`? Mailbox signal or shared StateBoard state?
4. Should `StrategyAdvisor` be a hook or a direct service used by `DirectorPattern`?
5. How do we prevent `DirectorPattern` from recursively spawning itself?

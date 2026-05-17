---
name: task-orchestrator-dev
description: >
  Use when developing, modifying, refactoring, or reviewing the task-orchestrator
  multi-agent orchestration engine. Covers design principles, architecture
  decisions, and development guardrails derived from the Multi-Agent Harness
  design reference. Triggered by changes to src/openagents_orchestration/,
  adding new orchestration features, or questions about orchestrator design.
---

# Task Orchestrator Development Guide

Develop and maintain the task-orchestrator with the design principles from
[Multi-Agent Harness Design Reference](../../docs/design-reference/multi-agent-harness-design.md)
as the architecture compass. This skill enforces the key tenets and maps them
to concrete code locations.

## Design Reference

The authoritative design reference lives at
`docs/design-reference/multi-agent-harness-design.md`. Read it before any
architecture-level change. The article structures the problem space into five
pillars; the table below maps each pillar to the project's current
implementation and gaps.

| Pillar | Status | Primary Code |
|--------|--------|-------------|
| Architecture & Orchestration | ✅ Implemented (Iteration 8-9) | `patterns/orchestrator.py`, `patterns/react_orchestrator.py`, `engine.py` |
| Tool Governance | ⚠️ Partial (ToolRegistry via SDK, no RBAC/risk) | `templates/*.json`, SDK `ToolPlugin` |
| State & Memory | ⚠️ Partial (checkpoint, SDK memory plugins) | `engine.py` checkpoint, SDK `MemoryPlugin` |
| Evaluation System | ✅ Implemented (Iteration 6, 9) | `strategies/evaluator.py`, `benchmarks/` |
| Cost Control | ⚠️ Partial (max_concurrency only) | `engine.py` budget, `patterns/orchestrator.py` semaphore |
| MCP Integration | ❌ Not yet | — |

## Core Tenets (enforced)

### 1. Agent-Orchestrator Separation

> Agent 负责局部智能，Harness 负责全局控制。

The orchestrator must hold **five exclusive decision rights**:

| Right | Implementation | Must NOT be delegated to |
|-------|---------------|--------------------------|
| Task lifecycle | `TaskStatus` state machine in `models/task.py` | LLM prompt |
| Plan adjudication | `_execute_graph()` / `react_orchestrator` action handlers | Planner Agent |
| Agent routing | `_infer_agent_type()` / template dispatch | LLM |
| Failure handling | `_run_task()` retry loop + `_classify_failure()` | Child agent |
| Hard termination | `RunBudget` (max_steps/max_tokens/max_duration) in `engine.py` | — |

**Code rule:** `orchestrator.py` and `react_orchestrator.py` are the **only**
modules allowed to call `router.delegate()`. Child agents never spawn other
agents directly. The `sub_agent` tool in `corecoder` is recursive-safe but must
not bypass the orchestrator's routing layer.

**Plan format rule:** Plans are declarative (`{step, intent, agent, input}`),
never imperative (`await agent.run()`). The orchestrator must be able to
reorder, parallelize, reject, or audit every step.

### 2. Tool Governance

> 工具不是函数调用，而是生产资源的对外授权点。

Every tool exposed to an agent must be registered through the SDK's
`ToolPlugin` interface. The `templates/*.json` files define per-agent tool sets.

**Registry checklist** (aspirational — current SDK covers items 1-3, 5, 8):

1. ✅ Unique name — defined in tool `describe()`
2. ✅ Description — for LLM consumption
3. ✅ Input JSON Schema — in `describe().parameters`
4. ⬜ Allowed agent list (RBAC) — **add to template `tools` field**
5. ✅ Timeout — `SafeToolExecutor` config
6. ⬜ Risk level (low/medium/high) — **add to tool metadata**
7. ⬜ Human-in-the-loop required — **add via `execution_policy` hook**
8. ✅ Output structure — returned by tool implementation
9. ⬜ Audit log policy — **wire to SDK event bus**

**Constraint:** Never add a tool globally across all agents. Each template
declares its own allowlist. When adding a new tool, update only the templates
that genuinely need it.

### 3. State & Memory Layering

| Layer | Scope | Current Implementation |
|-------|-------|----------------------|
| Working State | Single step, ephemeral | `ctx.scratch` in pattern execution |
| Session State | Multi-agent, TTL-bound | `ctx.state` + checkpoint JSON |
| Execution Log | Immutable, entire run | `ctx.memory_view["history"]` |

| Memory Type | Purpose | Current Implementation |
|------------|---------|----------------------|
| Episodic | Past failures, patterns | `_FailureRecord` → `failure_log` in checkpoint v2 |
| Semantic | Domain rules, constraints | Prompt templates in `prompts/task_prompt.py` |

**Forgetting rule:** `_FailureRecord` entries are capped (≥3 same-type →
`add_task` rejected). Long-term memory should prune by access frequency ×
recency × importance — not yet implemented, but the failure cap is the first
step.

**Injection timing:** Hybrid mode (pre-inject high-confidence + expose
`memory_search` tool) is preferred. Current project pre-injects via
system prompt composition in `compose_system_prompt()`.

### 4. Evaluation Pipeline

Four layers, two already implemented:

| Layer | Status | Location |
|-------|--------|----------|
| Component Eval | ⚠️ Implicit in template tests | `tests/test_templates.py` |
| Trajectory Eval | ⚠️ Benchmarks score decomposition | `benchmarks/metrics.py` |
| Task Completion | ✅ `_evaluate_task_completion()` | `patterns/orchestrator.py` |
| End-to-End | ✅ Benchmarks with 4 objectives | `benchmarks/runner.py` |

**LLM-as-Judge rule:** Use `_evaluate_task_completion()` for semantic
evaluation (expression completeness, reasoning coherence). Use deterministic
checks for: code runability, schema validation, artifact existence, safety
constraints. The `_fallback_artifact_check()` is the deterministic last resort.

**CI requirement:** Every prompt change, model swap, or tool addition must
re-run `run_benchmark.py` before merge. Prompt = code, tool schema = interface,
execution trace = log, eval = test suite.

### 5. Cost Control

Current tier: **Phase 1 (MVP)** — basic `RunBudget` + `max_concurrency`
semaphore. The article defines a three-strategy framework that should guide
future iterations:

| Strategy | Priority | Where to implement |
|----------|----------|-------------------|
| Model Routing | P1 | `_infer_agent_type()` → choose model tier per task complexity |
| Context Compression | P1 | `_build_task_input()` → summarize upstream results instead of full text |
| Budget Degradation | P2 | `engine.py`: green(>50%) / yellow(20-50%) / red(5-20%) / fuse(<5%) |

**Monitoring metrics** (add to delivery metadata):
- Per-task token total
- Per-agent token share
- Tool result token share
- Retry token share
- Budget fuse count
- Cost per completed task

### 6. MCP Integration

Not yet implemented. When adding MCP support, follow these rules:

1. **Never expose MCP Server directly to Agent.** Route through SDK `ToolPlugin`.
2. **Per-server quota.** One rogue MCP server must not exhaust global budget.
3. **Whitelist, not blacklist.** Even if a server exposes 50 tools, only expose
   the needed subset per agent template.
4. **HITL for high-risk tools.** File write, delete, code exec, DB write,
   external payments — must route through `execution_policy`.
5. **Trace every MCP call.** Tool source, params, result, caller must be
   attributable in audit.

## Development Workflow

### Before Writing Code

1. Read `docs/design-reference/multi-agent-harness-design.md` — the full
   design reference.
2. Read `CLAUDE.md` for project conventions and SDK integration rules.
3. Run `python -m pytest tests/ -v` to establish baseline.

### Architecture Decision Checklist

When adding a new capability, answer these 10 questions from the article:

1. How do tasks enter the system? (`engine.run()`)
2. Who decomposes? (`_decompose()` / ReAct `add_task`)
3. Who schedules? (`_execute_graph()` / ReAct `run_layer`)
4. How do tools connect? (`templates/*.json` + `ToolPlugin`)
5. Where does state live? (`ctx.state`, `ctx.scratch`, checkpoint)
6. How is memory retrieved? (`compose_system_prompt()`, `_build_task_input()`)
7. How is budget controlled? (`RunBudget`, semaphore)
8. How are trajectories evaluated? (benchmarks, `_evaluate_task_completion()`)
9. How are failures handled? (retry loop, `_FailureRecord`, ReAct actions)
10. How is audit preserved? (checkpoint, delivery report, event bus)

### After Changing Code

1. Run `python -m pytest tests/ -v` — all 155+ tests must pass.
2. If touching `patterns/orchestrator.py` or `react_orchestrator.py`: run
   `python run_demo.py` with real LLMs.
3. If changing prompts: re-run benchmarks and compare scores.
4. Update `README.md` Iteration History if this is a milestone change.

## File-Specific Guardrails

### `patterns/orchestrator.py` (~500 lines, resist growing)

- Phase 1-4 structure is canonical. New phases require architecture review.
- `_decompose()` output format is the contract with downstream phases — change
  carefully.
- No task-specific logic. Generic decomposition only.

### `patterns/react_orchestrator.py` (~700 lines)

- 7 action handlers (`inspect_graph`, `run_layer`, `run_task`, `add_task`,
  `modify_task`, `invoke_skill`, `finalize`) are the complete action surface.
  Adding an 8th requires a design review.
- `_canonical_task_id()` normalization must handle all suffix patterns
  documented in Iteration 9.
- `_classify_failure()`: 6 types. Adding a type requires updating
  `_render_failure_history()` warnings.

### `engine.py`

- `_build_runtime_config()` is the single source of truth for runtime wiring.
- API keys live here or in `.env`, never hardcoded.
- `fresh=False` auto-resumes; `fresh=True` deletes checkpoint and restarts.

### `models/task.py`

- `TaskNode.expected_artifacts`: max 2 per task (Iteration 5 rule).
- `TaskGraph.topological_layers()`: Kahn's algorithm variant. Must remain
  deterministic given the same graph.

## Known Anti-Patterns

1. **Adding task-specific logic to orchestrator core.** The orchestrator is
   general-purpose. Task-specific behavior goes in prompts (`prompts/task_prompt.py`),
   skill plugins (`skills/`), or templates.
2. **Bypassing ToolPlugin.** Every tool added to an agent must implement
   `ToolPlugin.describe()` with proper JSON Schema. Raw function calls
   circumvent the governance layer.
3. **Silent error swallowing.** Errors must surface through `TaskOutcome.error`,
   `_FailureRecord`, or event bus. Never `pass` on unexpected exceptions.
4. **Prompt as code without regression tests.** Every prompt change needs a
   corresponding test in `tests/test_prompts_task_prompt.py` or benchmarks.
5. **Delegating control decisions to child agents.** Child agents execute
   tasks; they never decide to retry, replan, or route to another agent.
   Those are orchestrator decisions.

## References

- `docs/design-reference/multi-agent-harness-design.md` — Full design reference article
- `docs/ARCHITECTURE.md` — System architecture document
- `docs/PATTERNS.md` — Orchestration patterns detail
- `docs/BEST_PRACTICES.md` — Best practices
- `docs/REFACTOR_PROPOSAL.md` — Refactoring proposals

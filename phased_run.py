"""Phased orchestration — run each phase of a complex task independently.

Usage:
    # Phase 1: Director decomposes objective into a plan
    python phased_run.py plan "做一个 FastAPI 商城后端" -o shop_plan.json

    # Phase 1.5: Review the plan (no LLM call)
    python phased_run.py review-plan shop_plan.json

    # Phase 2: Spawn a single agent directly
    python phased_run.py spawn coder "写一个 Python 函数 add(a, b)"

    # Phase 3: Execute one task from a saved plan
    python phased_run.py execute-task shop_plan.json t1

    # Phase 4: Execute all currently ready tasks (respects dependencies)
    python phased_run.py step shop_plan.json

    # Phase 5: Full orchestration (same as run.py)
    python phased_run.py full "做一个 FastAPI 商城后端"

State files:
    After each execute-task or step, task status is saved to
    <plan_file>.state.json so subsequent steps resume correctly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Ensure src/ is on PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent / "src"))

from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus
from openagents_orchestration.runner import OrchestratorRunner, RunnerDeps
from openagents_orchestration.state_board import StateBoard, Budget
from openagents_orchestration.tools.spawn_agent import SpawnAgentTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_env() -> None:
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _plan_path(path: str) -> Path:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Plan file not found: {p}")
    return p


def _state_path(plan_path: Path) -> Path:
    return plan_path.with_suffix(".state.json")


def _load_state(plan_path: Path) -> dict[str, Any]:
    sp = _state_path(plan_path)
    if sp.exists():
        with open(sp, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_state(plan_path: Path, state: dict[str, Any]) -> None:
    sp = _state_path(plan_path)
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


class _SimpleCtx:
    """Minimal context for tool.invoke() outside of a full Pattern loop."""

    def __init__(self, deps: Any):
        self.deps = deps


# ---------------------------------------------------------------------------
# Phase commands
# ---------------------------------------------------------------------------


async def cmd_plan(objective: str, output_file: Path) -> int:
    """Phase 1: Director decomposes objective into TaskGraph."""
    _load_env()
    runner = OrchestratorRunner(Path(__file__).parent / "agent.json")

    print(f"\n{'='*60}")
    print(f"PHASE 1: PLAN")
    print(f"{'='*60}")
    print(f"Objective: {objective}")
    print("Decomposing... (this calls the Director LLM)")

    graph = await runner._initial_decompose(objective)
    graph.validate()

    # Save
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(graph.to_dict(), f, indent=2, ensure_ascii=False)

    # Print human-readable review
    _print_plan_review(graph)
    print(f"\nPlan saved to: {output_file}")
    print(f"State file: {_state_path(output_file)}")
    return 0


def _print_plan_review(graph: TaskGraph) -> None:
    layers = graph.topological_layers()
    print(f"\n  Tasks: {len(graph.tasks)} | Layers: {len(layers)}")

    for i, layer in enumerate(layers):
        print(f"\n  --- Layer {i} ({len(layer)} task{'s' if len(layer) > 1 else ''}) ---")
        for t in layer:
            deps = f"  (deps: {', '.join(t.dependencies)})" if t.dependencies else ""
            arts = f"  -> {', '.join(t.expected_artifacts)}" if t.expected_artifacts else ""
            print(f"    [{t.agent_type:8}] {t.task_id}: {t.description}{deps}{arts}")
            if t.input_context:
                preview = t.input_context[:120].replace("\n", " ")
                print(f"             context: {preview}{'...' if len(t.input_context) > 120 else ''}")

    # Warnings
    issues: list[str] = []
    for t in graph.tasks:
        if not t.input_context:
            issues.append(f"{t.task_id}: missing input_context")
        if not t.expected_artifacts:
            issues.append(f"{t.task_id}: no expected_artifacts")
        if t.estimated_complexity > 3:
            issues.append(f"{t.task_id}: high complexity ({t.estimated_complexity})")
    if issues:
        print(f"\n  Warnings ({len(issues)}):")
        for issue in issues:
            print(f"    ! {issue}")
    else:
        print("\n  No warnings.")


async def cmd_review_plan(plan_file: Path) -> int:
    """Phase 1.5: Review a saved plan without calling LLM."""
    with open(plan_file, "r", encoding="utf-8") as f:
        graph = TaskGraph.from_dict(json.load(f))

    print(f"\n{'='*60}")
    print(f"PHASE 1.5: REVIEW PLAN")
    print(f"{'='*60}")
    print(f"File: {plan_file}")

    try:
        graph.validate()
        print("Validation: PASS (no cycles, no unknown deps)")
    except ValueError as e:
        print(f"Validation: FAIL — {e}")
        return 1

    _print_plan_review(graph)

    # Show current execution state if exists
    state = _load_state(plan_file)
    if state:
        print(f"\n  Execution state ({_state_path(plan_file)}):")
        for tid, tstate in state.get("tasks", {}).items():
            print(f"    {tid}: {tstate['status']}")
    else:
        print(f"\n  No execution state yet. Run 'execute-task' or 'step' to progress.")

    return 0


async def cmd_spawn(agent_type: str, input_text: str) -> int:
    """Phase 2: Spawn a single tactical agent directly."""
    _load_env()

    # Resolve @file.txt references
    if input_text.startswith("@"):
        input_text = Path(input_text[1:]).read_text(encoding="utf-8")

    runner = OrchestratorRunner(Path(__file__).parent / "agent.json")

    print(f"\n{'='*60}")
    print(f"PHASE 2: SPAWN")
    print(f"{'='*60}")
    print(f"Agent: {agent_type}")
    print(f"Input:\n{'-'*40}\n{input_text}\n{'-'*40}")

    try:
        result = await runner.run_agent(agent_type, input_text)
    finally:
        await runner.close()

    print(f"\n--- Result ---\n{result}")
    return 0


async def cmd_execute_task(plan_file: Path, task_id: str) -> int:
    """Phase 3: Execute a single task from a saved plan."""
    _load_env()
    with open(plan_file, "r", encoding="utf-8") as f:
        graph = TaskGraph.from_dict(json.load(f))

    task = graph.get_task(task_id)
    if task is None:
        print(f"Task '{task_id}' not found in plan")
        return 1

    print(f"\n{'='*60}")
    print(f"PHASE 3: EXECUTE TASK")
    print(f"{'='*60}")
    print(f"Task: {task_id}")
    print(f"Description: {task.description}")
    print(f"Agent: {task.agent_type}")
    print(f"Dependencies: {task.dependencies or '(none)'}")

    # Build StateBoard with plan + saved execution state
    board = StateBoard(graph.objective, budget=Budget(), echo=True)
    board.add_tasks(graph)

    # Load and apply saved state
    state = _load_state(plan_file)
    for tid, tstate in state.get("tasks", {}).items():
        board.update_task(tid, status=TaskStatus(tstate["status"]))

    # Check if task is ready
    completed = {t.task_id for t in board.tasks.values() if t.status == TaskStatus.COMPLETED}
    if not task.is_ready(completed):
        missing = set(task.dependencies) - completed
        print(f"\nCannot execute: unmet dependencies {sorted(missing)}")
        print(f"Completed so far: {sorted(completed) or '(none)'}")
        return 1

    if task.status != TaskStatus.PENDING:
        print(f"\nTask status is '{task.status.value}', not pending. Skipping.")
        return 0

    # Execute
    runner = OrchestratorRunner(Path(__file__).parent / "agent.json")
    runner._state_board = board

    deps = RunnerDeps(
        state_board=board,
        runner_delegate=runner.run_agent,
        runner=runner,
    )
    ctx = _SimpleCtx(deps=deps)
    tool = SpawnAgentTool()

    try:
        result = await tool.invoke({"task_id": task_id}, ctx)
    except Exception as exc:
        print(f"\nTask failed: {exc}")
        # Save failed state
        state["tasks"] = state.get("tasks", {})
        state["tasks"][task_id] = {"status": board.get_task(task_id).status.value}
        _save_state(plan_file, state)
        return 1
    finally:
        await runner.close()

    # Save state
    state["tasks"] = state.get("tasks", {})
    state["tasks"][task_id] = {"status": "completed"}
    _save_state(plan_file, state)

    print(f"\nTask completed. Artifacts: {result.get('artifacts', [])}")
    print(f"State saved to: {_state_path(plan_file)}")
    return 0


async def cmd_step(plan_file: Path) -> int:
    """Phase 4: Execute all currently ready tasks from the plan."""
    _load_env()
    with open(plan_file, "r", encoding="utf-8") as f:
        graph = TaskGraph.from_dict(json.load(f))

    print(f"\n{'='*60}")
    print(f"PHASE 4: STEP")
    print(f"{'='*60}")

    # Build StateBoard with saved state
    board = StateBoard(graph.objective, budget=Budget(), echo=True)
    board.add_tasks(graph)

    state = _load_state(plan_file)
    for tid, tstate in state.get("tasks", {}).items():
        board.update_task(tid, status=TaskStatus(tstate["status"]))

    # Find ready tasks
    ready = board.tasks_ready()
    if not ready:
        blocked = board.tasks_blocked()
        running = [t for t in board.tasks.values() if t.status == TaskStatus.RUNNING]
        if running:
            print(f"No ready tasks. Currently running: {[t.task_id for t in running]}")
        elif blocked:
            print(f"No ready tasks. Blocked by failed deps: {[t.task_id for t in blocked]}")
        elif board.all_terminal():
            print("All tasks are in terminal state. Use 'review-plan' to see results.")
        else:
            print("No ready tasks. Waiting for dependencies...")
        return 0

    print(f"Ready tasks ({len(ready)}): {[t.task_id for t in ready]}")

    # Execute each ready task
    runner = OrchestratorRunner(Path(__file__).parent / "agent.json")
    runner._state_board = board

    deps = RunnerDeps(
        state_board=board,
        runner_delegate=runner.run_agent,
        runner=runner,
    )
    ctx = _SimpleCtx(deps=deps)
    tool = SpawnAgentTool()

    exit_code = 0
    for task in ready:
        print(f"\n--- Executing {task.task_id} ---")
        try:
            result = await tool.invoke({"task_id": task.task_id}, ctx)
            state["tasks"] = state.get("tasks", {})
            state["tasks"][task.task_id] = {"status": "completed"}
            print(f"  Done. Artifacts: {result.get('artifacts', [])}")
        except Exception as exc:
            print(f"  Failed: {exc}")
            state["tasks"] = state.get("tasks", {})
            state["tasks"][task.task_id] = {"status": board.get_task(task.task_id).status.value}
            exit_code = 1

    _save_state(plan_file, state)
    await runner.close()

    # Summary
    completed = sum(1 for t in board.tasks.values() if t.status == TaskStatus.COMPLETED)
    failed = sum(1 for t in board.tasks.values() if t.status == TaskStatus.FAILED)
    pending = sum(1 for t in board.tasks.values() if t.status == TaskStatus.PENDING)
    print(f"\nStep complete. Completed: {completed}, Failed: {failed}, Pending: {pending}")
    print(f"State saved to: {_state_path(plan_file)}")
    return exit_code


async def cmd_full(objective: str) -> int:
    """Phase 5: Full orchestration (same as run.py)."""
    _load_env()
    from run import main as run_main

    # Patch sys.argv so run.py gets the objective
    original_argv = sys.argv
    sys.argv = ["run.py", objective]
    try:
        await run_main()
    finally:
        sys.argv = original_argv
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phased orchestration — test each phase independently",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s plan "做一个 FastAPI 商城后端" -o shop.json
  %(prog)s review-plan shop.json
  %(prog)s spawn coder "写一个 add(a,b) 函数"
  %(prog)s execute-task shop.json t1
  %(prog)s step shop.json
  %(prog)s full "做一个 FastAPI 商城后端"
        """,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # plan
    p = sub.add_parser("plan", help="Decompose objective into a TaskGraph plan")
    p.add_argument("objective", nargs="+", help="The objective to decompose")
    p.add_argument("-o", "--output", type=Path, default=Path("plan.json"), help="Output plan file")

    # review-plan
    p = sub.add_parser("review-plan", help="Review a saved plan without LLM calls")
    p.add_argument("plan_file", type=Path, help="Path to plan.json")

    # spawn
    p = sub.add_parser("spawn", help="Spawn a single agent directly")
    p.add_argument("agent_type", choices=["coder", "reviewer", "tester", "researcher", "monitor"], help="Agent type")
    p.add_argument("input", help='Input text (use @file.txt to read from file)')

    # execute-task
    p = sub.add_parser("execute-task", help="Execute one task from a saved plan")
    p.add_argument("plan_file", type=Path, help="Path to plan.json")
    p.add_argument("task_id", help="Task ID to execute")

    # step
    p = sub.add_parser("step", help="Execute all currently ready tasks")
    p.add_argument("plan_file", type=Path, help="Path to plan.json")

    # full
    p = sub.add_parser("full", help="Full orchestration (same as run.py)")
    p.add_argument("objective", nargs="+", help="The objective")

    args = parser.parse_args()

    if args.command == "plan":
        objective = " ".join(args.objective)
        return asyncio.run(cmd_plan(objective, args.output))
    elif args.command == "review-plan":
        return asyncio.run(cmd_review_plan(args.plan_file))
    elif args.command == "spawn":
        return asyncio.run(cmd_spawn(args.agent_type, args.input))
    elif args.command == "execute-task":
        return asyncio.run(cmd_execute_task(args.plan_file, args.task_id))
    elif args.command == "step":
        return asyncio.run(cmd_step(args.plan_file))
    elif args.command == "full":
        objective = " ".join(args.objective)
        return asyncio.run(cmd_full(objective))

    return 0


if __name__ == "__main__":
    sys.exit(main())

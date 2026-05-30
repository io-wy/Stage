"""Orchestrator demo entrypoint.

Usage:
    uv run python run.py "Write a Python CLI calculator"
    uv run python run.py "Build a FastAPI app" --token-limit 1000000 --max-steps 200
    uv run python run.py "Build a FastAPI app" -t 1M -s 200
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path


def _parse_token_limit(s: str) -> int:
    """Parse token limit with optional K/M suffix."""
    s = s.strip().upper()
    if s.endswith("K"):
        return int(s[:-1]) * 1_000
    if s.endswith("M"):
        return int(s[:-1]) * 1_000_000
    return int(s)


async def main():
    parser = argparse.ArgumentParser(
        description="Run the orchestrator with a given objective",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Budget options:
  --token-limit  Tokens budget (supports K/M suffix, e.g. 500K, 1M)
  --max-steps    Max director steps (default: 100)
  --time-limit   Time limit in seconds (default: 1800)

Examples:
  uv run python run.py "Write a hello world script"
  uv run python run.py "Build FastAPI app" --token-limit 1M --max-steps 200
        """,
    )
    parser.add_argument("objective", nargs="+", help="The objective to achieve")
    parser.add_argument(
        "-t", "--token-limit",
        type=_parse_token_limit,
        default=500_000,
        help="Token budget (default: 500000, supports K/M suffix)",
    )
    parser.add_argument(
        "-s", "--max-steps",
        type=int,
        default=100,
        help="Max steps budget (default: 100)",
    )
    parser.add_argument(
        "--time-limit",
        type=float,
        default=1800.0,
        help="Time limit in seconds (default: 1800)",
    )
    args = parser.parse_args()

    objective = " ".join(args.objective)

    # Ensure src/ is on PYTHONPATH for local imports
    sys.path.insert(0, str(Path(__file__).parent / "src"))

    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

    from openagents_orchestration.runner import OrchestratorRunner
    from openagents_orchestration.state_board import Budget

    persist_dir = Path(__file__).parent / ".claude" / "persist"
    persist_dir.mkdir(parents=True, exist_ok=True)

    runner = OrchestratorRunner(
        Path(__file__).parent / "agent.json",
        persist_dir=str(persist_dir),
    )

    budget = Budget(
        token_limit=args.token_limit,
        time_limit_s=args.time_limit,
        max_steps=args.max_steps,
    )

    print(f"\n[Orchestrator] Budget: {budget.token_limit:,} tokens, {budget.max_steps} steps, {budget.time_limit_s:.0f}s")

    try:
        report = await runner.run(objective, budget=budget)
    finally:
        await runner.close()

    print("\n" + "=" * 60)
    print("ORCHESTRATION REPORT")
    print("=" * 60)
    print(f"Objective: {report.objective}")
    print(f"Summary:   {report.summary}")
    print(f"Success:   {report.success_rate:.0%}")
    print(f"\nTasks ({len(report.task_results)}):")
    for tr in report.task_results:
        icon = "[OK]" if tr.status == "completed" else "[FAIL]"
        print(f"  {icon} {tr.task_id}: {tr.status}")
        if tr.artifacts:
            print(f"       artifacts: {', '.join(tr.artifacts)}")
        if tr.error:
            print(f"       error: {tr.error[:100]}")

    if report.final_output:
        print(f"\nFinal output:\n{report.final_output}")

    print("=" * 60)

    # Dump full event timeline for debugging
    if runner.state_board is not None:
        print("\n--- EVENT TIMELINE ---", file=sys.stderr)
        print(runner.state_board.format_events(), file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())

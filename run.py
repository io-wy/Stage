"""Orchestrator demo entrypoint.

Usage:
    uv run python run.py "Write a Python CLI calculator"
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


async def main():
    objective = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Write a hello world Python script"

    # Ensure src/ is on PYTHONPATH for local imports
    sys.path.insert(0, str(Path(__file__).parent / "src"))

    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

    from openagents_orchestration.runner import OrchestratorRunner

    runner = OrchestratorRunner(Path(__file__).parent / "agent.json")
    try:
        report = await runner.run(objective)
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

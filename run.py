"""Orchestrator entrypoint — objective → 编排 → DeliveryReport.

精简版：只保留核心编排流程（GlobalOrchestrator → OrchestratorRunner.run）+
report 打印。评估（eval/judge）、metrics、audit 后处理已移除，core 流程更清晰。

Usage:
    uv run python run.py "Write a Python CLI calculator"
    uv run python run.py "Build a FastAPI app" -t 1M -s 200 --teams backend:coder,reviewer
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path
from typing import Any


def _parse_token_limit(s: str) -> int:
    """Parse token limit with optional K/M suffix."""
    s = s.strip().upper()
    if s.endswith("K"):
        return int(s[:-1]) * 1_000
    if s.endswith("M"):
        return int(s[:-1]) * 1_000_000
    return int(s)


def _parse_teams(s: str) -> list[dict[str, Any]]:
    """Parse --teams flag: 'backend:coder,reviewer;docs:coder'."""
    if not s:
        return []
    result = []
    for part in s.split(";"):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            name, types = part.split(":", 1)
            result.append({
                "name": name.strip(),
                "agent_types": [t.strip() for t in types.split(",")],
            })
        else:
            result.append({"name": part, "agent_types": ["coder", "reviewer"]})
    return result


def _load_env() -> None:
    """Load .env into os.environ (does not override existing vars)."""
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the orchestrator with a given objective",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Budget options:
  --token-limit  Tokens budget (supports K/M suffix, e.g. 500K, 1M)
  --max-steps    Max director steps (default: 100)
  --time-limit   Time limit in seconds (default: 1800)

Execution:
  --teams        Team specs, e.g. backend:coder,reviewer;docs:coder
  --monitor      Enable active heartbeat monitor (default: on)

Examples:
  uv run python run.py "Write a hello world script"
  uv run python run.py "Build FastAPI app" -t 1M -s 200 --teams backend:coder,reviewer
        """,
    )
    parser.add_argument("objective", nargs="+", help="The objective to achieve")
    parser.add_argument(
        "-t", "--token-limit", type=_parse_token_limit, default=500_000,
        help="Token budget (default: 500000, supports K/M suffix)",
    )
    parser.add_argument(
        "-s", "--max-steps", type=int, default=100,
        help="Max steps budget (default: 100)",
    )
    parser.add_argument(
        "--time-limit", type=float, default=1800.0,
        help="Time limit in seconds (default: 1800)",
    )
    parser.add_argument(
        "--teams", type=str, default="",
        help="Team specs, e.g. backend:coder,reviewer;docs:coder",
    )
    parser.add_argument(
        "--monitor", choices=["on", "off"], default="on",
        help="Enable active heartbeat monitor (default: on)",
    )
    parser.add_argument(
        "--work-dir", default=".",
        help="Working directory for the orchestration (default: current dir)",
    )
    args = parser.parse_args()

    objective = " ".join(args.objective)

    # Resolve target directory from objective (e.g. "在 example 文件夹 ...")
    dir_match = re.search(
        r'(?:在|under|in)\s*[\'"]?(\w+)[\'"]?\s*(?:文件夹|folder|目录|directory)',
        objective, re.IGNORECASE,
    )
    if dir_match:
        work_dir = Path(args.work_dir) / dir_match.group(1)
        work_dir.mkdir(parents=True, exist_ok=True)
    else:
        work_dir = Path(args.work_dir)
    # 源头绝对化：相对 work_dir 不流进编排系统（避免 chdir 后被二次解析 → 套娃）
    work_dir = work_dir.resolve()

    # Ensure src/ is on PYTHONPATH for local imports
    sys.path.insert(0, str(Path(__file__).parent / "src"))
    _load_env()

    from openagents_orchestration.core.state_board import Budget
    from openagents_orchestration.projects.global_orchestrator import (
        GlobalOrchestrator,
    )
    from openagents_orchestration.projects.team import TeamSpec

    team_specs = None
    if args.teams:
        team_specs = [
            TeamSpec(name=t["name"], agent_types=t["agent_types"])
            for t in _parse_teams(args.teams)
        ]

    orchestrator = GlobalOrchestrator(
        Path(__file__).parent / "agent.json",
        enable_monitor=(args.monitor == "on"),
    )

    budget = Budget(
        token_limit=args.token_limit,
        time_limit_s=args.time_limit,
        max_steps=args.max_steps,
    )

    print(
        f"\n[Orchestrator] Budget: {budget.token_limit:,} tokens, "
        f"{budget.max_steps} steps, {budget.time_limit_s:.0f}s"
    )
    print(f"[Orchestrator] Work dir: {work_dir.absolute()}")
    if team_specs:
        print(f"[Orchestrator] Teams: {[t.name for t in team_specs]}")
    print(f"[Orchestrator] Monitor: {args.monitor}")

    if args.monitor == "on":
        await orchestrator.start_monitor()

    try:
        report = await orchestrator.run(
            objective,
            budget=budget,
            work_dir=work_dir,
            team_specs=team_specs,
        )
    finally:
        await orchestrator.shutdown()

    print("\n" + "=" * 60)
    print("ORCHESTRATION REPORT")
    print("=" * 60)
    print(f"Objective: {report.objective}")
    print(f"Tasks:     {len(report.task_results)}")
    for tr in report.task_results:
        print(f"  - {tr.task_id}: {tr.status}")
        if tr.artifacts:
            print(f"    artifacts: {', '.join(tr.artifacts)}")
        if tr.error:
            print(f"    error: {tr.error[:100]}")

    if report.final_output:
        print(f"\nFinal output:\n{report.final_output}")
    print("=" * 60)

    # Event timeline for debugging
    runner = getattr(orchestrator, "_runner", None)
    if runner is not None and runner.state_board is not None:
        print("\n--- EVENT TIMELINE ---", file=sys.stderr)
        print(runner.state_board.format_events(), file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())

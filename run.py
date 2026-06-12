"""Orchestrator demo entrypoint — Enterprise edition with GlobalOrchestrator.

Usage:
    uv run python run.py "Write a Python CLI calculator"
    uv run python run.py "Build a FastAPI app" --token-limit 1M --max-steps 200
    uv run python run.py "Build a FastAPI app" -t 1M -s 200 --teams backend,docs
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


async def main():
    parser = argparse.ArgumentParser(
        description="Run the enterprise orchestrator with a given objective",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Budget options:
  --token-limit  Tokens budget (supports K/M suffix, e.g. 500K, 1M)
  --max-steps    Max director steps (default: 100)
  --time-limit   Time limit in seconds (default: 1800)

Team options:
  --teams        Team specs, semicolon-separated.
                 Example: backend:coder,reviewer;docs:coder

Execution mode:
  --collaborative-mode auto|on|off
                auto uses resident coder/reviewer loops for suitable graphs;
                off forces Director scheduling.

Enterprise features:
  --monitor      Enable active heartbeat monitor (default: on)
  --metrics      Print Prometheus-style metrics after run
  --audit        Print audit log after run

Examples:
  uv run python run.py "Write a hello world script"
  uv run python run.py "Build FastAPI app" -t 1M -s 200 --teams backend:coder,reviewer,tester
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
    parser.add_argument(
        "--collaborative-mode",
        choices=["auto", "on", "off"],
        default="auto",
        help="Resident collaboration mode (default: auto)",
    )
    parser.add_argument(
        "--teams",
        type=str,
        default="",
        help="Team specs, e.g. backend:coder,reviewer;docs:coder",
    )
    parser.add_argument(
        "--monitor",
        choices=["on", "off"],
        default="on",
        help="Enable active heartbeat monitor (default: on)",
    )
    parser.add_argument(
        "--metrics",
        action="store_true",
        help="Print Prometheus-style metrics after run",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="Print audit log after run",
    )
    parser.add_argument(
        "--eval",
        action="store_true",
        help="Run 7-dimension evaluation after orchestration completes",
    )
    parser.add_argument(
        "--skip-judge",
        action="store_true",
        help="Skip Agent-as-Judge (objective metrics only, faster)",
    )
    parser.add_argument(
        "--work-dir",
        default=".",
        help="Working directory for the orchestration (default: current dir)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print judge reasoning details in evaluation report",
    )
    args = parser.parse_args()

    objective = " ".join(args.objective)

    # Resolve target directory from objective (e.g. "在 example 文件夹 ...")
    import re

    dir_match = re.search(
        r'(?:在|under|in)\s*[\'"]?(\w+)[\'"]?\s*(?:文件夹|folder|目录|directory)',
        objective, re.IGNORECASE,
    )
    if dir_match:
        target_dir = dir_match.group(1)
        work_dir = Path(args.work_dir) / target_dir
        work_dir.mkdir(parents=True, exist_ok=True)
    else:
        work_dir = Path(args.work_dir)

    # Ensure src/ is on PYTHONPATH for local imports
    sys.path.insert(0, str(Path(__file__).parent / "src"))

    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

    from openagents_orchestration.enterprise.global_orchestrator import GlobalOrchestrator
    from openagents_orchestration.core.state_board import Budget
    from openagents_orchestration.enterprise.team import TeamSpec

    persist_dir = Path(__file__).parent / ".claude" / "persist"
    persist_dir.mkdir(parents=True, exist_ok=True)

    # Build team specs
    team_specs = None
    if args.teams:
        team_specs = [
            TeamSpec(name=t["name"], agent_types=t["agent_types"])
            for t in _parse_teams(args.teams)
        ]

    orchestrator = GlobalOrchestrator(
        Path(__file__).parent / "agent.json",
        persist_dir=str(persist_dir),
        collaborative_mode=args.collaborative_mode,
        enable_monitor=(args.monitor == "on"),
    )

    budget = Budget(
        token_limit=args.token_limit,
        time_limit_s=args.time_limit,
        max_steps=args.max_steps,
    )

    print(
        f"\n[GlobalOrchestrator] Budget: {budget.token_limit:,} tokens, "
        f"{budget.max_steps} steps, {budget.time_limit_s:.0f}s"
    )
    print(f"[GlobalOrchestrator] Work dir: {work_dir.absolute()}")
    if team_specs:
        print(f"[GlobalOrchestrator] Teams: {[t.name for t in team_specs]}")
    print(f"[GlobalOrchestrator] Monitor: {args.monitor}")

    # Start enterprise monitor (active heartbeat)
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

    # ---- Enterprise Observability ------------------------------------------
    if args.metrics:
        print("\n" + "=" * 60)
        print("METRICS")
        print("=" * 60)
        print(orchestrator.metrics.to_prometheus())

    if args.audit:
        print("\n" + "=" * 60)
        print("AUDIT LOG")
        print("=" * 60)
        for entry in orchestrator.get_audit_log().query():
            print(
                f"  [{entry.event_type}] {entry.actor} -> {entry.target}: "
                f"{entry.action}"
            )

    # ---- Projects summary ----------------------------------------------------
    projects = orchestrator.list_projects()
    if projects:
        print("\n" + "=" * 60)
        print("PROJECTS")
        print("=" * 60)
        for p in projects:
            print(f"  {p.project_id}: {p.objective[:50]}... [{p.status}]")
            teams = p.metadata.get("teams", {})
            if teams:
                for tid, tdata in teams.items():
                    print(f"    Team {tdata.get('name', tid)}: {tdata.get('status', 'unknown')}")

    # ---- 7-Dimension Evaluation --------------------------------------------
    # For eval, we need the runner's state_board. Access via the internal runner.
    runner = getattr(orchestrator, "_runner", None)
    if args.eval and runner is not None and runner.state_board is not None:
        from eval.base import EvalHarness
        from openagents_orchestration.models.task import TaskStatus

        board = runner.state_board
        b = board.budget

        from types import SimpleNamespace

        # 1. Token Efficiency
        token_efficiency = EvalHarness.compute_token_efficiency(
            SimpleNamespace(max_steps=args.max_steps, max_tokens=args.token_limit),
            b.steps_taken,
            b.token_used,
        )

        # 2. Autonomy
        autonomy = EvalHarness.compute_autonomy(board)

        # 3. Recovery Rate
        total_failed = sum(
            1 for t in board.tasks.values() if t.status == TaskStatus.FAILED
        )
        recovered = 0
        for task_id in board.tasks:
            failed_seen = False
            for e in board.events:
                if getattr(e, "task_id", "") == task_id:
                    et = getattr(e, "event_type", "")
                    if "failed" in et:
                        failed_seen = True
                    elif failed_seen and "completed" in et:
                        recovered += 1
                        break
        recovery_rate = recovered / max(total_failed, 1)

        # 4. Orchestration Quality (objective part)
        orchestration_obj = 0.0

        # 5. Collaboration (objective part)
        review_approved = sum(
            1 for e in board.events
            if "reviewer_approved" in str(getattr(e, "message", ""))
        )
        review_spawned = sum(
            1 for e in board.events
            if "spawned_reviewer" in str(getattr(e, "message", ""))
        )
        collaboration_obj = 0.5
        if review_spawned > 0:
            collaboration_obj = review_approved / review_spawned
        else:
            tactical = [
                a for a in board.agents.values()
                if getattr(a, "agent_type", "") not in ("director", "monitor")
            ]
            collaboration_obj = 0.3 if len(tactical) > 1 else 0.8

        # 6. Judge (subjective)
        judge_error = None
        judge_cost = None
        reasoning_map: dict[str, str] = {}
        task_success = 0.0
        orchestration_quality = orchestration_obj
        collaboration_success = collaboration_obj
        output_quality = 0.0

        if not args.skip_judge:
            from eval.judge import ClaudeCodeJudge

            judge = ClaudeCodeJudge()
            try:
                judge_result = await judge.evaluate(
                    task_description=objective,
                    state_board=board,
                    work_dir=work_dir,
                    verify_scores={},
                )
                if judge_result.get("error"):
                    judge_error = judge_result["error"]
                    print(f"\n[JUDGE WARNING] {judge_error}", file=sys.stderr)
                    task_success = 0.0
                    orchestration_quality = orchestration_obj * 0.4
                    collaboration_success = collaboration_obj * 0.5
                    output_quality = 0.0
                else:
                    task_success = judge_result["fulfillment_score"]
                    orchestration_quality = (
                        orchestration_obj * 0.4
                        + judge_result["decomposition_score"] * 0.6
                    )
                    collaboration_success = (
                        collaboration_obj
                        * judge_result["collaboration_feedback_quality"]
                    )
                    output_quality = judge_result["output_quality_score"]
                    judge_cost = judge_result.get("cost_usd")
                    reasoning_map = judge_result.get("reasoning_map", {})
            except Exception as exc:
                judge_error = str(exc)
                print(f"\n[JUDGE WARNING] {judge_error}", file=sys.stderr)
        else:
            task_success = 0.0

        # Print eval report
        print("\n" + "=" * 60)
        print("EVALUATION REPORT (7 Dimensions)")
        print("=" * 60)
        print(f"  Task Success:      {task_success:.2f}")
        print(f"  Token Efficiency:  {token_efficiency:.2f}")
        print(f"  Orchestration:     {orchestration_quality:.2f}")
        print(f"  Collaboration:     {collaboration_success:.2f}")
        print(f"  Recovery Rate:     {recovery_rate:.2f}")
        print(f"  Output Quality:    {output_quality:.2f}")
        print(f"  Autonomy:          {autonomy:.2f}")
        if judge_error:
            print(f"  Judge Error:       {judge_error}")
        if judge_cost:
            print(f"  Judge Cost:        ${judge_cost:.4f}")
        if args.verbose and reasoning_map:
            print("\n  --- Judge Reasoning ---")
            for dim, text in reasoning_map.items():
                preview = text.replace("\n", " ")[:200]
                print(f"  [{dim}] {preview}{'...' if len(text) > 200 else ''}")
        print("=" * 60)

    # Dump full event timeline for debugging
    if runner is not None and runner.state_board is not None:
        print("\n--- EVENT TIMELINE ---", file=sys.stderr)
        print(runner.state_board.format_events(), file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())

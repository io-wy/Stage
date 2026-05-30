"""Resume a previously persisted orchestrator session.

Usage:
    uv run python resume.py <session_id> [--token-multiplier 2.0] [--step-multiplier 2.0]

Examples:
    # List available sessions
    uv run python resume.py --list

    # Resume with doubled budget
    uv run python resume.py session-abc12345 --token-multiplier 2.0 --step-multiplier 2.0

    # Resume with default 1.5x budget boost
    uv run python resume.py session-abc12345
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


async def main():
    parser = argparse.ArgumentParser(description="Resume an orchestrator session")
    parser.add_argument("session_id", nargs="?", help="Session ID to resume")
    parser.add_argument("--list", action="store_true", help="List available sessions")
    parser.add_argument("--token-multiplier", type=float, default=1.5, help="Multiply token_limit by this factor")
    parser.add_argument("--step-multiplier", type=float, default=1.5, help="Multiply max_steps by this factor")
    args = parser.parse_args()

    persist_dir = Path(__file__).parent / ".claude" / "persist"

    # List mode
    if args.list or not args.session_id:
        if not persist_dir.exists():
            print("No persist directory found.")
            return 1

        from openagents_orchestration.persistence import SessionResumer
        resumer = SessionResumer(persist_dir)
        sessions = resumer.list_sessions()
        if not sessions:
            print("No persisted sessions found.")
            return 0

        print(f"\n{'='*60}")
        print("AVAILABLE SESSIONS")
        print(f"{'='*60}")
        for s in sessions:
            status = []
            if s.get("has_snapshot"):
                status.append("snapshot")
            if s.get("has_events"):
                status.append(f"events({s.get('events_size', 0)}b)")
            print(f"  {s['session_id']}  [{', '.join(status) if status else 'no data'}]")
        return 0

    # Resume mode
    sys.path.insert(0, str(Path(__file__).parent / "src"))

    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

    from openagents_orchestration.runner import OrchestratorRunner

    persist_dir.mkdir(parents=True, exist_ok=True)

    runner = OrchestratorRunner(
        Path(__file__).parent / "agent.json",
        persist_dir=str(persist_dir),
    )

    print(f"\n{'='*60}")
    print(f"RESUME SESSION: {args.session_id}")
    print(f"{'='*60}")

    try:
        report = await runner.run("", session_id=args.session_id, resume=True)
    except RuntimeError as exc:
        if "budget exhausted" in str(exc).lower() or "exhausted" in str(exc).lower():
            print(f"\n[!] Budget still exhausted after resume.")
            print(f"    Trying with expanded budget...")

            # Load snapshot manually and expand budget
            from openagents_orchestration.persistence import SessionResumer
            resumer = SessionResumer(persist_dir)
            loaded = resumer.load(args.session_id)

            if loaded.snapshot is None:
                print(f"[!] No snapshot found for {args.session_id}")
                return 1

            from openagents_orchestration.state_board import StateBoard
            board = StateBoard.from_dict(
                loaded.snapshot,
                recorder=runner._recorder,
                snapshotter=runner._snapshotter,
            )

            # Expand budget
            old_token_limit = board.budget.token_limit
            old_max_steps = board.budget.max_steps
            board.budget.token_limit = int(old_token_limit * args.token_multiplier)
            board.budget.max_steps = int(old_max_steps * args.step_multiplier)

            print(f"    Token limit: {old_token_limit} -> {board.budget.token_limit}")
            print(f"    Max steps:   {old_max_steps} -> {board.budget.max_steps}")

            # Replay events
            if loaded.events_after:
                from openagents_orchestration.persistence import EventReplayer
                EventReplayer().replay(board, loaded.events_after)

            runner._state_board = board
            report = await runner._continue_run(board.objective)
        else:
            raise
    finally:
        await runner.close()

    print("\n" + "=" * 60)
    print("RESUME REPORT")
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
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

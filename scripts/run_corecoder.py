#!/usr/bin/env python3
"""Standalone CoreCoder runner — run a single coder agent without Director."""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Ensure src/ is on path when run from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from openagents_orchestration.runtime.runner import OrchestratorRunner


@dataclass
class _MinimalDeps:
    """Minimal deps for standalone coder runs; most coder tools only need cwd."""

    state_board: Any = None
    runner_delegate: Any = None
    runner: Any = None
    matrix_transport: Any = None


def _load_env(repo_root: Path) -> None:
    """Load KEY=VALUE pairs from .env without external dependencies."""
    env_path = repo_root / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"\'')


async def _run_once(
    runner: OrchestratorRunner,
    agent_type: str,
    instruction: str,
    agent_id: str,
    transcript_override: list[dict[str, Any]] | None = None,
) -> Any:
    """Run a single CoreCoder agent and return the RunResult."""
    return await runner._run_single(
        agent_id=agent_id,
        agent_type=agent_type,
        input_text=instruction,
        transcript_override=transcript_override,
    )


async def main(agent_type: str, instruction: str) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    _load_env(repo_root)
    sys.path.insert(0, str(repo_root / "src"))

    config_path = repo_root / "agent.json"
    runner = OrchestratorRunner(config_path)

    # Minimal deps so _run_single can operate without a StateBoard/director.
    runner._current_work_dir = repo_root
    runner._deps = _MinimalDeps()

    agent_id = f"{agent_type}-{uuid.uuid4().hex[:6]}"

    # First run: agent may pause and ask for clarification.
    result = await _run_once(runner, agent_type, instruction, agent_id)

    awaiting = result.metadata.get("awaiting_human_reply") if result.metadata else None
    while awaiting:
        question = awaiting.get("question", "")
        print("\n=== Clarification needed ===")
        print(question)
        print("=============================")
        try:
            reply = input("Your reply: ").strip()
        except EOFError:
            print("\nNo reply provided; exiting.")
            return
        if not reply:
            print("Empty reply; exiting.")
            return

        # Build a transcript that continues from the persisted session and
        # includes the human reply as a new user turn.
        session_id = f"session-{agent_id}"
        transcript = await runner._sessions.load_messages(session_id)
        transcript.append({"role": "user", "content": reply})

        result = await _run_once(
            runner,
            agent_type,
            instruction,
            agent_id,
            transcript_override=transcript,
        )
        awaiting = result.metadata.get("awaiting_human_reply") if result.metadata else None

    print("\n=== Final output ===")
    print(result.final_output)
    print("\n=== Usage ===")
    total_tokens = getattr(result.usage, "total_tokens", 0) if result.usage else 0
    print(f"tokens: {total_tokens}")
    print(f"steps: {result.metadata.get('steps_used', 0)}")
    print(f"tool_calls: {result.metadata.get('tool_calls_used', 0)}")


if __name__ == "__main__":
    agent_type = sys.argv[1] if len(sys.argv) > 1 else "coder"
    instruction = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else "List the files in the current directory and summarize the project."
    asyncio.run(main(agent_type, instruction))

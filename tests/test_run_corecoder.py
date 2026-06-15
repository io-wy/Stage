"""Tests for scripts/run_corecoder.py standalone recovery flow."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@dataclass
class _FakeRunResult:
    final_output: str = ""
    stop_reason: Any = None
    usage: Any = None
    artifacts: list[Any] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class FakeUsage:
    total_tokens = 0


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


@pytest.mark.asyncio
async def test_main_asks_and_resumes(repo_root: Path):
    """Simulate one clarification round-trip in standalone mode."""
    sys_path_before = list(sys.path)
    with (
        patch.dict("os.environ", {}, clear=False),
        patch.object(Path, "resolve", return_value=repo_root),
        patch("scripts.run_corecoder.OrchestratorRunner") as MockRunner,
    ):
        runner = MagicMock()
        MockRunner.return_value = runner

        first_result = _FakeRunResult(
            final_output="[Awaiting human reply] Which module?",
            metadata={
                "steps_used": 1,
                "tool_calls_used": 0,
                "awaiting_human_reply": {
                    "qid": None,
                    "question": "Which module?",
                    "options": "",
                    "from_agent": "coder-test",
                },
            },
        )
        second_result = _FakeRunResult(
            final_output="Done",
            metadata={
                "steps_used": 2,
                "tool_calls_used": 1,
            },
        )
        runner._run_single = AsyncMock(side_effect=[first_result, second_result])
        runner._sessions.load_messages = AsyncMock(return_value=[])

        with patch("builtins.input", return_value="patterns"):
            from scripts import run_corecoder

            await run_corecoder.main("coder", "看看 Stage 有什么问题")

        assert runner._run_single.await_count == 2
        # Second call used a transcript override containing the human reply.
        second_call = runner._run_single.await_args
        transcript = second_call.kwargs.get("transcript_override") or second_call[1].get("transcript_override")
        assert transcript is not None
        assert any(
            msg.get("role") == "user" and msg.get("content") == "patterns"
            for msg in transcript
        )

    sys.path[:] = sys_path_before

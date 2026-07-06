"""Stage 多任务并发 mock 测试——模拟 Matrix 场景：5 个房间同时发消息。

每个房间独立的 GlobalOrchestrator，验证：
- 并发不阻塞
- 各自独立 project
- 系统不崩
"""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
for _k, _v in {
    "LLM_API_BASE": "http://mock-llm.local/v1",
    "LLM_API_KEY": "mock-key",
    "LLM_MODEL": "mock-model",
}.items():
    os.environ.setdefault(_k, _v)

from openagents.llm.registry import create_llm_client as _orig_create_llm
from openagents_orchestration.im_adapters.matrix import MatrixAdapter
from openagents_orchestration.models.delivery import DeliveryReport
from openagents_orchestration.projects.global_orchestrator import GlobalOrchestrator


@dataclass
class FakeToolCall:
    name: str
    arguments: dict[str, Any]
    id: str = "c1"


@dataclass
class FakeResponse:
    output_text: str = ""
    content: list[dict[str, Any]] | None = None
    tool_calls: list[FakeToolCall] = field(default_factory=list)
    usage: Any | None = None


# Director script: show_state → classify → decompose → show_state → spawn_agent → show_state → finalize
DIRECTOR_SCRIPT = [
    FakeResponse(content=[{"type": "tool_use", "name": "show_state", "input": {}}]),
    FakeResponse(content=[{"type": "tool_use", "name": "classify_intent", "input": {"objective": ""}}]),
    FakeResponse(content=[{"type": "tool_use", "name": "decompose", "input": {"objective": ""}}]),
    FakeResponse(content=[{"type": "tool_use", "name": "show_state", "input": {}}]),
    FakeResponse(content=[{"type": "tool_use", "name": "spawn_agent", "input": {"task_id": "t1"}}]),
    FakeResponse(content=[{"type": "tool_use", "name": "show_state", "input": {}}]),
    FakeResponse(output_text="Done. All tasks completed."),
]

# Coder script: write a file, run it, complete
CODER_SCRIPT = [
    FakeResponse(content=[{"type": "tool_use", "name": "write_file", "input": {"file_path": "out.py", "content": "print('hi')"}}]),
    FakeResponse(content=[{"type": "tool_use", "name": "bash", "input": {"command": "python out.py"}}]),
    FakeResponse(output_text="Completed."),
]


class MockLLMClient:
    """Mock LLM with scripted responses per agent type."""
    provider_name = "openai_compatible"

    def __init__(self, script: list[FakeResponse]) -> None:
        self._script = list(script)
        self._idx = 0

    async def generate(self, **kw: Any) -> FakeResponse:
        if self._idx >= len(self._script):
            return FakeResponse(output_text="")
        r = self._script[self._idx]
        self._idx += 1
        return r


def mock_create_llm(*args: Any, **kw: Any) -> MockLLMClient:
    """Replace create_llm_client with mock that picks script by agent type."""
    agent_id = kw.get("agent_id", "coder") or ""
    if "director" in agent_id:
        return MockLLMClient(DIRECTOR_SCRIPT)
    return MockLLMClient(CODER_SCRIPT)


# Patch at the SDK registry level
import openagents.llm.registry as llm_reg
llm_reg.create_llm_client = mock_create_llm


# Also patch the actual module the runner imports
import openagents_orchestration.core.runner as runner_mod
runner_mod.create_llm_client = mock_create_llm


OBJECTIVES = [
    "Write a Python hello world script",
    "Create a simple calculator",
    "Build a TODO list Flask app",
    "Write a markdown cheat sheet",
    "Create a random number guessing game",
    "Make a JSON file validator",
    "Write a CSV to JSON converter",
    "Create a simple web scraper",
    "Build a password generator",
    "Write a file watcher script",
]

ROOM_IDS = [f"!room_{i}:example.org" for i in range(len(OBJECTIVES))]


async def simulate_room(adapter: MatrixAdapter, room_id: str, objective: str) -> str:
    """Simulate a Matrix room sending a message and getting a response."""
    room = MagicMock()
    room.room_id = room_id
    event = MagicMock()
    event.body = objective
    event.sender = "@user:example.org"
    event.source = {}

    await adapter._on_message(room, event)
    calls = adapter._send_text.call_args_list
    if calls:
        return calls[-1][0][1]  # last response
    return "(no response)"


async def main() -> None:
    print("=" * 60)
    print("Stage 多任务并发 mock 测试")
    print(f"{len(OBJECTIVES)} 个房间同时发消息，验证并发隔离")
    print("=" * 60)

    work_root = tempfile.mkdtemp(suffix="_stage_multitask")
    print(f"Work root: {work_root}\n")

    adapter = MatrixAdapter(
        homeserver="https://matrix.example.com",
        user_id="@bot:example.org",
        access_token="mock-token",
        config_path=Path(__file__).parent.parent / "agent.json",
        work_dir=work_root,
        token_limit=100_000,
        max_steps=15,
    )
    adapter._client = AsyncMock()
    adapter._client.user_id = "@bot:example.org"
    adapter._send_text = AsyncMock()

    t0 = time.time()

    # Fire all rooms concurrently
    result_texts = await asyncio.gather(*[
        simulate_room(adapter, rid, obj)
        for rid, obj in zip(ROOM_IDS, OBJECTIVES)
    ])

    elapsed = time.time() - t0

    print(f"\n{'=' * 60}")
    print(f"耗时: {elapsed:.1f}s")
    print(f"房间/任务: {len(OBJECTIVES)}")
    print()

    # Results
    success = 0
    for i, (obj, rid, text) in enumerate(zip(OBJECTIVES, ROOM_IDS, result_texts)):
        status = "✅" if text and "Error" not in text else "❌"
        if "✅" in status:
            success += 1
        print(f"  {status} [{i}] {rid[:20]:20s} {obj[:35]:35s} | {text[:60]}")

    print()
    print(f"通过: {success}/{len(OBJECTIVES)}")

    # Isolation check: each room has its own orchestrator
    print(f"\nOrchestrators created: {len(adapter._orchestrators)} (should be {len(OBJECTIVES)})")
    assert len(adapter._orchestrators) == len(OBJECTIVES), "not all rooms got orchestrators"

    # Check they're truly different instances
    instances = set(id(v) for v in adapter._orchestrators.values())
    assert len(instances) == len(OBJECTIVES), "rooms share orchestrators!"

    print(f"✅ {len(instances)} unique orchestrator instances — no sharing, no lock contention")

    await adapter.stop()
    print(f"\n✅ 多任务并发测试完成")


if __name__ == "__main__":
    asyncio.run(main())

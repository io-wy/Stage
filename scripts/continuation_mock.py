"""continuation_mock.py — 端到端 mock：coder 耗尽 max_steps → 续命 → 做完（零真实 API）。

单测 test_continuation_hook.py 验 hook 护栏逻辑；这里验**真实集成**——续命在真实
after_execute 链里触发、transcript 经 session 续接、task 最终 COMPLETED：

  director spawn coder t1
    → coder 第一轮：一直 write_file，跑满 max_steps(=4) → MAX_STEPS（没干完）
    → ContinuationHooks 在真实 after_execute 接管（护栏内）→ 重 spawn coder-t1（同 agent_id）
    → coder 第二轮：续命 input（带"续命"提示）+ assemble 续接上轮 transcript → complete_task
    → task COMPLETED（续命做完，对 director 透明）

机制：MAX_STEPS 不再当 FAILED（apply_outcome 已解耦）；同 agent_id → 同 session_id →
assemble 自动 load 回上轮历史。

用法：  python scripts/continuation_mock.py
"""

# ruff: noqa: E402
from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
for _k, _v in {
    "LLM_API_BASE": "http://mock-llm.local/v1", "LLM_API_KEY": "mock-key",
    "LLM_MODEL": "mock-model",
}.items():
    os.environ.setdefault(_k, _v)

from openagents_orchestration.core.runner import OrchestratorRunner
from openagents_orchestration.core.state_board import Budget, TaskStatus
from openagents_orchestration.hooks import HookEvent
from openagents_orchestration.models.task import TaskGraph, TaskNode
from openagents_orchestration.tools.director.classify_intent import ClassifyIntentTool
from openagents_orchestration.tools.director.decompose import DecomposeTool

OBJECTIVE = "Build out.py (a big task the coder won't finish in one step budget)"
_PLAN = '{"steps": ["do it"], "confidence": 8}'
_CODER_MAX_STEPS = 4  # 调小，让第一轮快速耗尽触发续命


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


def _blob(messages: list[Any]) -> str:
    parts: list[str] = []
    for m in messages or []:
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            parts.extend(str(b.get("text") or b.get("content") or b) if isinstance(b, dict)
                         else str(b) for b in c)
        elif c:
            parts.append(str(c))
    return " ".join(parts)


class ScriptedLLM:
    """director 固定剧本。"""
    provider_name = "openai_compatible"

    def __init__(self, responses: list[Any]):
        self._r, self._i = list(responses), 0

    async def generate(self, **kw: Any) -> Any:
        if kw.get("tools") is None:
            return FakeResponse(output_text=_PLAN)
        if self._i >= len(self._r):
            return FakeResponse(output_text="")
        r = self._r[self._i]
        self._i += 1
        return r


class ContinuationCoderLLM:
    """第一轮一直 write_file（耗尽 max_steps）；续命轮（input 带"续命"）→ complete_task。"""
    provider_name = "openai_compatible"

    async def generate(self, **kw: Any) -> Any:
        if kw.get("tools") is None:
            return FakeResponse(output_text=_PLAN)
        blob = _blob(kw.get("messages", []))
        if "续命" in blob:  # 续命轮：续接历史后收尾
            return FakeResponse(tool_calls=[FakeToolCall(
                "complete_task", {"summary": "done after continuation", "artifacts": ["out.py"]})])
        # 第一轮：一直写文件，不收尾 → 跑满 max_steps → MAX_STEPS
        return FakeResponse(tool_calls=[FakeToolCall(
            "write_file", {"file_path": "out.py", "content": "# work in progress\n"})])


async def _fake_classify(self, params, context):  # noqa: ANN001
    return {"intent": {"task_type": "feature", "complexity": "complex", "external": [],
                       "priority": "normal", "confidence": 0.9, "reason": "x", "source": "mock"}}


async def _fake_decompose(self, params, context):  # noqa: ANN001
    board = getattr(getattr(context, "deps", None), "state_board", None)
    board.add_tasks(TaskGraph(objective=OBJECTIVE, tasks=[
        TaskNode("t1", "Build out.py", "coder", expected_artifacts=["out.py"]),
    ]))
    return {"tasks_added": 1, "task_ids": ["t1"]}


def _director_llm() -> ScriptedLLM:
    return ScriptedLLM([
        FakeResponse(tool_calls=[FakeToolCall("classify_intent", {"objective": OBJECTIVE})]),
        FakeResponse(tool_calls=[FakeToolCall("decompose", {"objective": OBJECTIVE})]),
        FakeResponse(tool_calls=[FakeToolCall("show_state", {})]),
        # spawn t1：coder 第一轮 MAX_STEPS → 续命 → 第二轮 complete，全在此调用内同步跑完
        FakeResponse(tool_calls=[FakeToolCall("spawn_agent", {"task_id": "t1"})]),
        FakeResponse(tool_calls=[FakeToolCall("show_state", {})]),
        FakeResponse(tool_calls=[FakeToolCall("finalize", {"summary": "built"})]),
        FakeResponse(output_text="Done"),
    ])


def _p(m: str) -> None:
    print(m, file=sys.stderr, flush=True)


def _register(runner: OrchestratorRunner) -> None:
    def on_event(payload: dict) -> dict:
        return payload

    def on_after(payload: dict) -> dict:
        aid = payload.get("agent_id")
        st = getattr(getattr(payload.get("outcome"), "status", None), "value", None)
        if str(aid).startswith("coder"):
            _p(f"  [AGENT] {aid} → {st}")
        return payload

    runner._hook_manager.register(HookEvent.PATTERN_AFTER_EXECUTE, on_after)


async def run() -> None:
    work_dir = Path(tempfile.mkdtemp(prefix="continuation_mock_"))
    runner = OrchestratorRunner(Path(__file__).parent.parent / "agent.json",
                                enable_monitor_resident=False)
    _register(runner)
    _p("═" * 76)
    _p("═══ 续命端到端 mock: coder 第一轮 max_steps 耗尽 → 续命 → 第二轮 complete")
    _p("═" * 76)

    director_llm, coder_llm = _director_llm(), ContinuationCoderLLM()
    orig = OrchestratorRunner._ensure_bundle

    def patched(self, agent_type: str):
        b = orig(self, agent_type)
        base = agent_type.replace("__leaf", "")
        b.llm_client = {"director": director_llm, "coder": coder_llm}.get(base, b.llm_client)
        if base == "coder":  # 调小 coder 步数预算，快速耗尽触发续命
            with contextlib.suppress(Exception):  # __init__ 已读 config，直接改属性
                b.plugins.pattern._max_steps = _CODER_MAX_STEPS
        return b

    with (
        patch("openagents_orchestration.patterns.corecoder._detect_project_type",
              lambda cwd: {"type": "Python", "test_cmd": "python -c 'import out'", "lint_cmd": ""}),
        patch.object(OrchestratorRunner, "_ensure_bundle", patched),
        patch.object(ClassifyIntentTool, "invoke", _fake_classify),
        patch.object(DecomposeTool, "invoke", _fake_decompose),
    ):
        await runner.run(OBJECTIVE,
                         budget=Budget(token_limit=200_000, time_limit_s=300, max_steps=200),
                         work_dir=str(work_dir))

    board = runner.state_board
    task = board.get_task("t1")
    cont_events = [e for e in board.events if e.event_type.startswith("continuation.")]
    _p("═" * 76)
    _p("═══ 结果")
    _p(f"  t1.status            = {task.status.value}")
    _p(f"  t1.continuation_count = {task.continuation_count}")
    _p("  continuation 事件:")
    for e in cont_events:
        _p(f"    {e.event_type}: {e.message}")
    _p(f"  产物: {sorted(p.name for p in work_dir.glob('*.py'))}")
    _p("═" * 76)

    assert task.status == TaskStatus.COMPLETED, f"t1 应续命做完 COMPLETED，实际 {task.status}"
    assert task.continuation_count == 1, f"应续命 1 次，实际 {task.continuation_count}"
    assert any(e.event_type == "continuation.spawned" for e in cont_events), "无 continuation.spawned 事件"
    _p("✅ 续命端到端通过: max_steps 耗尽 → 续命重跑（同 session 续接）→ 第二轮 complete → COMPLETED")
    _p("   MAX_STEPS 不再当 FAILED；续命对 director 透明。")


if __name__ == "__main__":
    asyncio.run(run())

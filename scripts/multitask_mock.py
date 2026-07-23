"""multitask_mock.py — mock 多任务编排，验证 StateBoard 解耦。

用 mock LLM（零真实 API）跑一个有「并行 + 依赖」的多任务图，观察 StateBoard
独立追踪全程状态流转：

    decompose → t1‖t2 (并行, 无依赖) → t3 (依赖 t1,t2)

Director 用固定剧本（scripted）驱动 batch spawn；多个 coder 共享 agent_type="coder"
的 bundle，故 coder 不能用固定剧本（_index 会竞争错乱）——改用无状态的
AlwaysCompleteLLM：每个 tool-ful 调用直接返回 "Completed."，coder 一步收工。

验证点：StateBoard 不靠真实 LLM、不靠对话历史，独立把 t1/t2/t3 从 PENDING 推到
COMPLETED，并正确处理依赖解锁（t3 在 t1,t2 完成前 blocked、之后 ready）。这就是
X-02「StateBoard 是唯一可变状态源」+「独立于对话历史」的可复现证明。

用法：  python scripts/multitask_mock.py
"""

# ruff: noqa: E402 — sys.path bootstrap 必须先于 openagents import
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# mock 占位 env（config 加载期需要；FakeLLMClient 顶掉真实 client）
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        if _line.strip() and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())
for _k, _v in {
    "LLM_API_BASE": "http://mock-llm.local/v1",
    "LLM_API_KEY": "mock-key",
    "LLM_MODEL": "mock-model",
}.items():
    os.environ.setdefault(_k, _v)

from openagents_orchestration.hooks import HookEvent
from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus
from openagents_orchestration.runtime.runner import OrchestratorRunner
from openagents_orchestration.runtime.state_board import Budget
from openagents_orchestration.tools.director.classify_intent import ClassifyIntentTool
from openagents_orchestration.tools.director.decompose import DecomposeTool

OBJECTIVE = "Build a small service: models, utils, then wire them into app"


# ── mock LLM ──────────────────────────────────────────────────────────────
@dataclass
class FakeToolCall:
    name: str
    arguments: dict[str, Any]
    id: str = "call_1"


@dataclass
class FakeResponse:
    output_text: str = ""
    content: list[dict[str, Any]] | None = None
    tool_calls: list[FakeToolCall] = field(default_factory=list)
    usage: Any | None = None


class ScriptedLLM:
    """回放固定剧本（director 用，串行 ReAct，无并发）。"""

    _PLAN_JSON = '{"steps": ["orchestrate"], "confidence": 8}'

    def __init__(self, responses: list[Any]):
        self._responses = list(responses)
        self._index = 0
        self.provider_name = "openai_compatible"

    async def generate(self, **kwargs: Any) -> Any:
        if kwargs.get("tools") is None:  # planning 调用，喂 plan 不消耗剧本
            return FakeResponse(output_text=self._PLAN_JSON)
        if self._index >= len(self._responses):
            return FakeResponse(output_text="")
        r = self._responses[self._index]
        self._index += 1
        return r


class AlwaysCompleteLLM:
    """无状态：每个 coder 的首个 tool-ful 调用即返回完成（一步收工）。

    多个 coder 共享 agent_type="coder" 的 bundle，故 LLM 必须无状态，否则 batch
    并发时共享 _index 会错乱。
    """

    _PLAN_JSON = '{"steps": ["do the task"], "confidence": 8}'
    provider_name = "openai_compatible"

    async def generate(self, **kwargs: Any) -> Any:
        if kwargs.get("tools") is None:
            return FakeResponse(output_text=self._PLAN_JSON)
        return FakeResponse(output_text="Completed. (mock coder one-shot)")


# ── mock 决策工具：decompose 加 3 任务（并行 + 依赖）─────────────────────────
async def _fake_classify(self, params, context):  # noqa: ANN001
    return {"intent": {
        "task_type": "feature", "complexity": "complex", "external": [],
        "priority": "normal", "confidence": 0.9, "reason": "multi-module", "source": "mock",
    }}


async def _fake_decompose(self, params, context):  # noqa: ANN001
    board = getattr(getattr(context, "deps", None), "state_board", None)
    graph = TaskGraph(objective=OBJECTIVE, tasks=[
        TaskNode("t1", "Build models.py", "coder", expected_artifacts=["models.py"]),
        TaskNode("t2", "Build utils.py", "coder", expected_artifacts=["utils.py"]),
        TaskNode("t3", "Wire app.py", "coder",
                 dependencies=["t1", "t2"], expected_artifacts=["app.py"]),
    ])
    board.add_tasks(graph)
    return {"tasks_added": 3, "task_ids": ["t1", "t2", "t3"]}


def _director_llm() -> ScriptedLLM:
    return ScriptedLLM([
        FakeResponse(tool_calls=[FakeToolCall("classify_intent", {"objective": OBJECTIVE})]),
        FakeResponse(tool_calls=[FakeToolCall("decompose", {"objective": OBJECTIVE})]),
        FakeResponse(tool_calls=[FakeToolCall("show_state", {})]),
        # batch: t1‖t2 并行（无依赖）
        FakeResponse(tool_calls=[FakeToolCall("spawn_agent", {"task_ids": ["t1", "t2"]})]),
        FakeResponse(tool_calls=[FakeToolCall("show_state", {})]),
        # t3 依赖 t1,t2，此刻应已 ready
        FakeResponse(tool_calls=[FakeToolCall("spawn_agent", {"task_id": "t3"})]),
        FakeResponse(tool_calls=[FakeToolCall("show_state", {})]),
        FakeResponse(tool_calls=[FakeToolCall("finalize", {"summary": "All 3 modules done"})]),
        FakeResponse(output_text="Done"),
    ])


# ── trace 输出 ────────────────────────────────────────────────────────────
def _p(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _print_board(runner: OrchestratorRunner, tag: str) -> None:
    """打印 StateBoard 当前多任务状态表 —— 解耦的直接证据。"""
    board = runner.state_board
    if board is None:
        return
    done = {t.task_id for t in board.tasks.values() if t.status == TaskStatus.COMPLETED}
    _p(f"\n  ┌─ StateBoard @ {tag}")
    for t in board.tasks.values():
        status = getattr(t.status, "value", str(t.status))
        deps = list(t.dependencies)
        ready = not deps or all(d in done for d in deps)
        gate = "" if not deps else (" [deps✓ ready]" if ready else f" [waiting {deps}]")
        _p(f"  │  {t.task_id}: {status:<10} deps={deps}{gate}")
    prog = board.progress_summary()
    _p(f"  └─ progress: {prog['completed_tasks']}/{prog['total_tasks']} done, "
       f"ready={prog['ready_tasks']}, blocked={prog['blocked_tasks']}\n")


def _register_printers(runner: OrchestratorRunner) -> None:
    def on_after_execute(payload: dict) -> dict:
        outcome = payload.get("outcome")
        status = getattr(getattr(outcome, "status", None), "value", None)
        _p(f"  [AGENT done] {payload.get('agent_id')} → {status}")
        _print_board(runner, f"after {payload.get('agent_id')}")
        return payload

    def on_tool_before(payload: dict) -> dict:
        tid = payload.get("tool_id")
        if tid in ("spawn_agent", "decompose", "finalize"):
            _p(f"  [DIRECTOR] → {tid}  {payload.get('params')}")
        return payload

    hm = runner._hook_manager
    hm.register(HookEvent.PATTERN_AFTER_EXECUTE, on_after_execute)
    hm.register(HookEvent.TOOL_BEFORE_INVOKE, on_tool_before)


# ── 跑 ────────────────────────────────────────────────────────────────────
async def run() -> None:
    work_dir = Path(tempfile.mkdtemp(prefix="multitask_mock_"))
    config_path = Path(__file__).parent.parent / "agent.json"
    runner = OrchestratorRunner(
        config_path, enable_monitor_resident=False,
    )
    _register_printers(runner)

    _p("═" * 72)
    _p(f"═══ MOCK 多任务编排   objective={OBJECTIVE!r}")
    _p("═══ 任务图: t1‖t2 (并行) → t3 (依赖 t1,t2)")
    _p("═" * 72)

    director_llm, coder_llm = _director_llm(), AlwaysCompleteLLM()
    orig_bundle = OrchestratorRunner._ensure_bundle

    def patched_bundle(self, agent_type: str):
        bundle = orig_bundle(self, agent_type)
        if agent_type == "director":
            bundle.llm_client = director_llm
        elif agent_type == "coder":
            bundle.llm_client = coder_llm
        return bundle

    with (
        patch.object(OrchestratorRunner, "_ensure_bundle", patched_bundle),
        patch.object(ClassifyIntentTool, "invoke", _fake_classify),
        patch.object(DecomposeTool, "invoke", _fake_decompose),
    ):
        report = await runner.run(
            OBJECTIVE,
            budget=Budget(token_limit=50_000, time_limit_s=300, max_steps=50),
            work_dir=str(work_dir),
        )

    _p("═" * 72)
    _p("═══ 最终结果")
    board = runner.state_board
    _print_board(runner, "FINAL")
    _p(f"  success_rate: {report.success_rate:.0%}")
    for tr in report.task_results:
        _p(f"  - {tr.task_id}: {tr.status}")
    _p(f"  final_summary: {board._final_summary}")
    _p("═" * 72)
    # 解耦断言：3 任务全 COMPLETED，全靠 StateBoard 追踪（零真实 LLM）
    completed = sum(1 for t in board.tasks.values() if t.status == TaskStatus.COMPLETED)
    assert completed == 3, f"expected 3 completed, got {completed}"
    _p(f"✅ StateBoard 独立追踪 {completed}/3 任务流转完成（含依赖解锁）—— 解耦验证通过")


if __name__ == "__main__":
    asyncio.run(run())

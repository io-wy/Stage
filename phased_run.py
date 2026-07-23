"""phased_run.py — mock 全链路 trace debug 入口。

一键跑通 director→coder 编排链路（mock LLM，零真实 API），按层把每层变量打到
stderr，让你看清数据流：

    RUN → DIRECTOR 每步(STEP/LLM/TOOL) → spawn_agent → CODER(STEP/LLM/TOOL)
        → HOOK(记账员 payload) → BOARD(echo 事件) → 最终 snapshot + REPORT

零改生产代码：全靠 HookManager 注册 printer + mock 注入实现。pattern 层的
STEP/TOOL/LLM hook 与 runner 的 AFTER_EXECUTE 共用同一个 HookManager
（pattern 经 ctx.deps.hooks 取，正是 runner._hook_manager），故注册一处即可全覆盖。

用法：
    python phased_run.py                 # 跑内置 mock 脚本（建 hello.py）
    python phased_run.py --break hook    # 在 PATTERN_AFTER_EXECUTE 停进 pdb，可 `p payload`
    python phased_run.py --break tool    # 在 TOOL_BEFORE_INVOKE 停进 pdb
    python phased_run.py --break step    # 在 PATTERN_BEFORE_STEP 停进 pdb

线程现实：director 层 + 顶层 hook(director 的 PATTERN_AFTER_EXECUTE) 在主线程，
pdb 可用；coder 的 execute 跑在 spawn_agent 工具的独立线程（_make_thread_safe_invoke
里 asyncio.run），该层的 STEP/TOOL/LLM/AFTER_EXECUTE hook 在工具线程触发，断点
受限、打印仍正常（输出可能与主线程交错）。
"""

# ruff: noqa: E402 — sys.path bootstrap 必须先于 openagents 包 import，全文件豁免 E402
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent / "src"))

# mock 跑不打真实 API；但 agents/_base.json 用 ${LLM_API_BASE} 等占位，config 加载期
# 需这些 env 存在。优先读项目 .env，缺失则设 mock 占位（FakeLLMClient 顶掉真实 client）。
_env_file = Path(__file__).parent / ".env"
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
from openagents_orchestration.models.task import TaskGraph, TaskNode
from openagents_orchestration.runtime.runner import OrchestratorRunner
from openagents_orchestration.runtime.state_board import Budget
from openagents_orchestration.tools.director.classify_intent import ClassifyIntentTool
from openagents_orchestration.tools.director.decompose import DecomposeTool

OBJECTIVE = "Create a hello.py file that prints hello"


# ── mock LLM（与 tests/test_director_e2e_mock.py 同形）─────────────────────
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


class FakeLLMClient:
    """回放脚本；tool-less(planning)调用喂固定 plan，不消耗主脚本。"""

    _PLAN_JSON = '{"steps": ["do it"], "confidence": 8}'

    def __init__(self, responses: list[Any]):
        self._responses = list(responses)
        self._index = 0
        self.provider_name = "openai_compatible"

    async def generate(self, **kwargs: Any) -> Any:
        if kwargs.get("tools") is None:
            return FakeResponse(output_text=self._PLAN_JSON)
        if self._index >= len(self._responses):
            return FakeResponse(output_text="")
        r = self._responses[self._index]
        self._index += 1
        return r


# ── trace 输出 ────────────────────────────────────────────────────────────
def _p(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _short(v: Any, n: int = 90) -> str:
    s = repr(v)
    return s if len(s) <= n else s[:n] + "…"


def _register_printers(runner: OrchestratorRunner, break_layer: str | None) -> None:
    """把每层 printer 注册到 runner._hook_manager（与 pattern 共用的那个）。"""

    def on_step(payload: dict) -> dict:
        _p(f"  [STEP ] step={payload.get('step')}/{payload.get('max_steps')}")
        if break_layer == "step":
            breakpoint()
        return payload

    def on_llm(payload: dict) -> dict:
        msgs = payload.get("messages") or []
        tools = payload.get("tools") or []
        sys_c = sum(len(json.dumps(m, default=str)) for m in msgs if m.get("role") == "system")
        hist_c = sum(len(json.dumps(m, default=str)) for m in msgs if m.get("role") != "system")
        tool_c = len(json.dumps(tools, default=str))
        _p(
            f"  [LLM  ] in≈{(sys_c + hist_c + tool_c) // 4} tok "
            f"(system {sys_c // 4} + history {hist_c // 4} + tools {tool_c // 4})  "
            f"msgs={len(msgs)} tools={len(tools)}"
        )
        return payload

    def on_tool_before(payload: dict) -> dict:
        _p(f"  [TOOL>] {payload.get('tool_id')}  params={_short(payload.get('params'))}")
        if break_layer == "tool":
            breakpoint()
        return payload

    def on_tool_after(payload: dict) -> dict:
        _p(f"  [TOOL<] {payload.get('tool_id')}  → {_short(payload.get('result'))}")
        return payload

    def on_after_execute(payload: dict) -> dict:
        outcome = payload.get("outcome")
        status = getattr(outcome, "status", None)
        status = getattr(status, "value", status)
        _p(
            f"  [HOOK ] after_execute  agent={payload.get('agent_id')} "
            f"type={payload.get('agent_type')} task_id={payload.get('task_id')} status={status}"
        )
        if break_layer == "hook":
            breakpoint()
        return payload

    def on_llm_after(payload: dict) -> dict:
        m = payload.get("metrics")
        if m is not None:
            cached = getattr(m, "cached_tokens", 0)
            flag = "  ★CACHE HIT" if cached else ""
            _p(
                f"  [LLM<] in={getattr(m, 'input_tokens', 0)} "
                f"out={getattr(m, 'output_tokens', 0)} cached={cached}{flag}"
            )
        return payload

    hm = runner._hook_manager
    hm.register(HookEvent.PATTERN_BEFORE_STEP, on_step)
    hm.register(HookEvent.PATTERN_BEFORE_LLM, on_llm)
    hm.register(HookEvent.LLM_AFTER_CALL, on_llm_after)
    hm.register(HookEvent.TOOL_BEFORE_INVOKE, on_tool_before)
    hm.register(HookEvent.TOOL_AFTER_INVOKE, on_tool_after)
    hm.register(HookEvent.PATTERN_AFTER_EXECUTE, on_after_execute)


# ── 固定 mock 脚本（可复现）───────────────────────────────────────────────
def _director_llm() -> FakeLLMClient:
    return FakeLLMClient([
        FakeResponse(tool_calls=[FakeToolCall("classify_intent", {"objective": OBJECTIVE})]),
        FakeResponse(tool_calls=[FakeToolCall("decompose", {"objective": OBJECTIVE})]),
        FakeResponse(tool_calls=[FakeToolCall("show_state", {})]),
        FakeResponse(tool_calls=[FakeToolCall("spawn_agent", {"task_id": "t1"})]),
        FakeResponse(tool_calls=[FakeToolCall("show_state", {})]),
        FakeResponse(tool_calls=[FakeToolCall("finalize", {"summary": "Created hello.py"})]),
        FakeResponse(output_text="Done"),
    ])


def _coder_llm() -> FakeLLMClient:
    return FakeLLMClient([
        FakeResponse(tool_calls=[FakeToolCall(
            "write_file", {"file_path": "hello.py", "content": "print('hello')\n"})]),
        FakeResponse(tool_calls=[FakeToolCall("bash", {"command": "python hello.py"})]),
        FakeResponse(output_text="Completed.\n\nFILES_CREATED: hello.py"),
    ])


async def _fake_classify(self, params, context):  # noqa: ANN001
    return {"intent": {
        "task_type": "feature", "complexity": "complex", "external": [],
        "priority": "normal", "confidence": 0.9, "reason": "mock", "source": "mock",
    }}


async def _fake_decompose(self, params, context):  # noqa: ANN001
    board = getattr(getattr(context, "deps", None), "state_board", None)
    graph = TaskGraph(objective=OBJECTIVE, tasks=[
        TaskNode(
            task_id="t1", description="Create hello.py", agent_type="coder",
            input_context="Write hello.py that prints 'hello' when run.",
            expected_artifacts=["hello.py"],
        ),
    ])
    board.add_tasks(graph)
    return {"tasks_added": 1, "task_ids": ["t1"]}


async def run_trace(
    break_layer: str | None, real: bool = False, objective: str | None = None
) -> None:
    # mock 模式跑固定剧本(建 hello.py)，剧本写死故 objective 忽略；real 跑给定 objective。
    run_objective = (objective or OBJECTIVE) if real else OBJECTIVE
    work_dir = Path(tempfile.mkdtemp(prefix="phased_debug_"))

    config_path = Path(__file__).parent / "agent.json"
    runner = OrchestratorRunner(
        config_path, enable_monitor_resident=False,
    )
    _register_printers(runner, break_layer)

    _p("═" * 72)
    _p(f"═══ RUN   mode={'REAL-LLM' if real else 'MOCK'}   objective={run_objective!r}")
    _p(f"═══ work_dir={work_dir}")
    if break_layer:
        _p(f"═══ pdb armed @ layer={break_layer}  (director 层主线程可用; coder 层在工具线程受限)")
    _p("═" * 72)

    budget = Budget(token_limit=300_000 if real else 10_000, time_limit_s=600, max_steps=50)

    if real:
        # 真实 LLM：不掉包 llm_client、不 mock classify/decompose，用 .env 真实 key。
        report = await runner.run(run_objective, budget=budget, work_dir=str(work_dir))
    else:
        # mock：掉包 LLM + mock 决策工具，跑固定剧本。
        director_llm, coder_llm = _director_llm(), _coder_llm()
        orig_bundle = OrchestratorRunner._ensure_bundle

        def patched_bundle(self, agent_id: str):
            bundle = orig_bundle(self, agent_id)
            if agent_id == "director":
                bundle.llm_client = director_llm
            elif agent_id == "coder":
                bundle.llm_client = coder_llm
            return bundle

        with (
            patch(
                "openagents_orchestration.patterns.corecoder._detect_project_type",
                lambda cwd: {"type": "Python", "test_cmd": "python hello.py", "lint_cmd": ""},
            ),
            patch.object(OrchestratorRunner, "_ensure_bundle", patched_bundle),
            patch.object(ClassifyIntentTool, "invoke", _fake_classify),
            patch.object(DecomposeTool, "invoke", _fake_decompose),
        ):
            report = await runner.run(run_objective, budget=budget, work_dir=str(work_dir))

    board = runner.state_board
    _p("═" * 72)
    _p("═══ FINAL board.snapshot()  (截断 2000 字符)")
    _p(json.dumps(board.snapshot(), ensure_ascii=False, indent=2, default=str)[:2000])
    _p("═" * 72)
    _p("═══ REPORT")
    _p(f"  objective: {report.objective}")
    _p(f"  success:   {report.success_rate:.0%}")
    for tr in report.task_results:
        _p(f"  - {tr.task_id}: {tr.status}   artifacts={tr.artifacts}")
    files = [str(p.relative_to(work_dir)) for p in sorted(work_dir.rglob("*")) if p.is_file()]
    _p(f"═══ 产物文件({len(files)}): {files}")
    _p("═" * 72)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="全链路 trace debug — 跑通编排、逐层看变量(mock 固定剧本 / --real 真实 LLM)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "层(--break): step=PATTERN_BEFORE_STEP  tool=TOOL_BEFORE_INVOKE  hook=PATTERN_AFTER_EXECUTE\n"
            "默认 mock(固定剧本建 hello.py); --real 用 .env 真实 LLM 跑给定 objective、看每步真实动作。"
        ),
    )
    parser.add_argument("objective", nargs="*", help="objective(--real 时生效; mock 时用固定剧本)")
    parser.add_argument(
        "--real", action="store_true",
        help="用真实 LLM 跑(读 .env 真实 key)，不 mock；看真实 director 每步动作",
    )
    parser.add_argument(
        "--break", dest="break_layer", choices=["step", "tool", "hook"], default=None,
        help="在指定层 breakpoint() 进 pdb",
    )
    args = parser.parse_args()
    obj = " ".join(args.objective) if args.objective else None
    asyncio.run(run_trace(args.break_layer, real=args.real, objective=obj))
    return 0


if __name__ == "__main__":
    sys.exit(main())

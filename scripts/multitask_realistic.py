"""multitask_realistic.py — 尽可能贴近真实的多任务编排（mock LLM，零真实 API）。

相比 multitask_mock.py（coder 一步空完成、纯验 StateBoard 解耦），这个脚本让
编排「真的发生」，覆盖真实场景的关键机制线：

  1. coder 多步 ReAct：write_file → bash 验证 → complete（真产出 artifact 落盘）
  2. 依赖传递：t3 的 input 注入 t1/t2 的上游产物，app.py 真 import models+utils
  3. coder↔subagent 同步委托：t3 的 coder 写完 app.py 后，真派一个 reviewer
     subagent 审查（纵向委托，subagent 不进 StateBoard，对 director 透明）
  4. batch 并行 + 依赖门控 + after_execute 状态同步（同 mock 版）

mock 设计要点：
- 多个 coder 共享 agent_type="coder" 的 bundle → 不能用固定剧本（_index 竞争）。
  改用「无状态、内容感知」的 SmartCoderLLM：从 messages 解析「该写哪个文件 /
  写了没 / 验证了没 / 该不该派 reviewer」，故 batch 并发安全。
- 叶子化（depth=1）会把 reviewer → reviewer__leaf，patched_bundle 注入按
  base_type（strip __leaf）匹配。

用法：  python scripts/multitask_realistic.py
"""

# ruff: noqa: E402
from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        if _line.strip() and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())
for _k, _v in {
    "LLM_API_BASE": "http://mock-llm.local/v1", "LLM_API_KEY": "mock-key",
    "LLM_MODEL": "mock-model",
}.items():
    os.environ.setdefault(_k, _v)

from openagents_orchestration.hooks import HookEvent
from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus
from openagents_orchestration.runtime.runner import OrchestratorRunner
from openagents_orchestration.runtime.state_board import Budget
from openagents_orchestration.tools.director.classify_intent import ClassifyIntentTool
from openagents_orchestration.tools.director.decompose import DecomposeTool

OBJECTIVE = "Build a tiny app: models.py + utils.py, then wire app.py (reviewed)"

# 真实点：app.py 真 import models+utils，依赖必须先就位
CONTENT = {
    "models.py": "class User:\n    def __init__(self, name):\n        self.name = name\n",
    "utils.py": "def greet(name):\n    return f'hello {name}'\n",
    "app.py": (
        "from models import User\n"
        "from utils import greet\n\n"
        "if __name__ == '__main__':\n"
        "    print(greet(User('io').name))\n"
    ),
}


# ── mock LLM 基件 ─────────────────────────────────────────────────────────
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


_PLAN = '{"steps": ["do the task"], "confidence": 8}'


def _messages_text(messages: list[Any]) -> str:
    """把 messages 全文拼成一个 blob，供无状态 SmartLLM 判断进度。"""
    parts: list[str] = []
    for m in messages or []:
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            for b in c:
                if isinstance(b, dict):
                    parts.append(str(b.get("text") or b.get("content") or b))
                else:
                    parts.append(str(b))
        elif c:
            parts.append(str(c))
    return " ".join(parts)


def _parse_target(blob: str) -> str:
    """从 input 的 'Expected artifacts:' 段解析这个 coder 该写的文件。"""
    m = re.search(r"Expected artifacts:.*?([\w]+\.py)", blob, re.DOTALL)
    if m:
        return m.group(1)
    m = re.search(r"([\w]+\.py)", blob)
    return m.group(1) if m else "out.py"


class ScriptedLLM:
    """固定剧本（director 用；串行 ReAct，无并发）。"""

    provider_name = "openai_compatible"

    def __init__(self, responses: list[Any]):
        self._responses = list(responses)
        self._index = 0

    async def generate(self, **kw: Any) -> Any:
        if kw.get("tools") is None:
            return FakeResponse(output_text=_PLAN)
        if self._index >= len(self._responses):
            return FakeResponse(output_text="")
        r = self._responses[self._index]
        self._index += 1
        return r


class SmartCoderLLM:
    """无状态内容感知（所有 coder 共享，batch 并发安全）。

    进度全靠 messages 痕迹判断：
      "lines to"     ← write_file 写过了
      "VERIFIED-OK"  ← bash 验证过了
      "REVIEW_REQUIRED" in input ← 这个 task 要求 review（t3）
      "REVIEW_OK"    ← reviewer subagent 已返回
    """

    provider_name = "openai_compatible"

    async def generate(self, **kw: Any) -> Any:
        if kw.get("tools") is None:
            return FakeResponse(output_text=_PLAN)
        blob = _messages_text(kw.get("messages", []))
        target = _parse_target(blob)
        wrote = "lines to" in blob
        verified = "VERIFIED-OK" in blob
        needs_review = "REVIEW_REQUIRED" in blob
        # sub_agent 回传现在把 reviewer 的实质结论并入 message(见 sub_agent._clean_output)，
        # coder 能直接看到 REVIEW_OK —— 用它判断 reviewer 已审过(端到端验证回传修复)。
        reviewed = "REVIEW_OK" in blob

        if not wrote:  # step 1: 写文件
            return FakeResponse(tool_calls=[FakeToolCall(
                "write_file", {"file_path": target, "content": CONTENT.get(target, f"# {target}\n")})])
        if not verified:  # step 2: 验证
            return FakeResponse(tool_calls=[FakeToolCall(
                "bash", {"command": f"test -f {target} && echo VERIFIED-OK"})])
        if needs_review and not reviewed:  # step 3 (仅 t3): 派 reviewer subagent
            return FakeResponse(tool_calls=[FakeToolCall(
                "sub_agent", {"agent_type": "reviewer", "instruction": f"Review {target} for correctness."})])
        # 收尾必须调 complete_task 工具（设 __complete_task_summary__ 才停循环）；
        # 返回纯文本会被 verification nudge 挡住 → 死循环。
        return FakeResponse(tool_calls=[FakeToolCall(
            "complete_task", {"summary": f"Implemented and verified {target}", "artifacts": [target]})])


class ReviewerLLM:
    """reviewer subagent：一步给出审查结论。"""

    provider_name = "openai_compatible"

    async def generate(self, **kw: Any) -> Any:
        if kw.get("tools") is None:
            return FakeResponse(output_text=_PLAN)
        return FakeResponse(output_text="REVIEW_OK: imports resolve, logic is correct.")


# ── mock 决策工具 ─────────────────────────────────────────────────────────
async def _fake_classify(self, params, context):  # noqa: ANN001
    return {"intent": {
        "task_type": "feature", "complexity": "complex", "external": [],
        "priority": "normal", "confidence": 0.9, "reason": "multi-module", "source": "mock",
    }}


async def _fake_decompose(self, params, context):  # noqa: ANN001
    board = getattr(getattr(context, "deps", None), "state_board", None)
    graph = TaskGraph(objective=OBJECTIVE, tasks=[
        TaskNode("t1", "Build models.py (a User class)", "coder",
                 expected_artifacts=["models.py"]),
        TaskNode("t2", "Build utils.py (a greet function)", "coder",
                 expected_artifacts=["utils.py"]),
        TaskNode("t3", "Wire app.py importing models+utils", "coder",
                 dependencies=["t1", "t2"], expected_artifacts=["app.py"],
                 input_context="REVIEW_REQUIRED: after writing, spawn a reviewer "
                               "subagent to check the file before completing."),
    ])
    board.add_tasks(graph)
    return {"tasks_added": 3, "task_ids": ["t1", "t2", "t3"]}


def _director_llm() -> ScriptedLLM:
    return ScriptedLLM([
        FakeResponse(tool_calls=[FakeToolCall("classify_intent", {"objective": OBJECTIVE})]),
        FakeResponse(tool_calls=[FakeToolCall("decompose", {"objective": OBJECTIVE})]),
        FakeResponse(tool_calls=[FakeToolCall("show_state", {})]),
        FakeResponse(tool_calls=[FakeToolCall("spawn_agent", {"task_ids": ["t1", "t2"]})]),
        FakeResponse(tool_calls=[FakeToolCall("show_state", {})]),
        FakeResponse(tool_calls=[FakeToolCall("spawn_agent", {"task_id": "t3"})]),
        FakeResponse(tool_calls=[FakeToolCall("show_state", {})]),
        FakeResponse(tool_calls=[FakeToolCall("finalize", {"summary": "3 modules built + app reviewed"})]),
        FakeResponse(output_text="Done"),
    ])


# ── trace 输出 ────────────────────────────────────────────────────────────
def _p(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _print_board(runner: OrchestratorRunner, tag: str) -> None:
    board = runner.state_board
    if board is None:
        return
    done = {t.task_id for t in board.tasks.values() if t.status == TaskStatus.COMPLETED}
    _p(f"\n  ┌─ StateBoard @ {tag}")
    for t in board.tasks.values():
        st = getattr(t.status, "value", str(t.status))
        deps = list(t.dependencies)
        ready = not deps or all(d in done for d in deps)
        gate = "" if not deps else (" [deps✓]" if ready else f" [waiting {deps}]")
        _p(f"  │  {t.task_id}: {st:<10} deps={deps}{gate}")
    _p(f"  │  agents(StateBoard 可见): {sorted(board.agents)}")
    prog = board.progress_summary()
    _p(f"  └─ {prog['completed_tasks']}/{prog['total_tasks']} done\n")


def _register_printers(runner: OrchestratorRunner) -> None:
    """完整六层 trace：每层把关键变量打到 stderr（标 agent_id）。

    注意线程现实：director 在主线程；coder/reviewer 的 execute 跑在 spawn_agent /
    sub_agent 工具的独立线程，其 STEP/LLM/TOOL hook 在工具线程触发，输出可能与
    主线程交错（但都在）。
    """
    def _short(v: Any, n: int = 80) -> str:
        s = repr(v)
        return s if len(s) <= n else s[:n] + "…"

    def on_step(payload: dict) -> dict:
        _p(f"      [STEP ] [{payload.get('agent_id', '?')}] "
           f"step={payload.get('step')}/{payload.get('max_steps')}")
        return payload

    def on_llm_before(payload: dict) -> dict:
        msgs = payload.get("messages") or []
        tools = payload.get("tools") or []
        _p(f"      [LLM> ] [{payload.get('agent_id', '?')}] msgs={len(msgs)} tools={len(tools)}")
        return payload

    def on_llm_after(payload: dict) -> dict:
        m = payload.get("metrics")
        if m is not None:
            _p(f"      [LLM< ] [{payload.get('agent_id', '?')}] "
               f"in={getattr(m, 'input_tokens', 0)} out={getattr(m, 'output_tokens', 0)} "
               f"cached={getattr(m, 'cached_tokens', 0)}")
        return payload

    def on_tool_before(payload: dict) -> dict:
        _p(f"    · [{payload.get('agent_id', '?')}] TOOL> {payload.get('tool_id')}"
           f"({_short(payload.get('params'))})")
        return payload

    def on_tool_after(payload: dict) -> dict:
        _p(f"    · [{payload.get('agent_id', '?')}] TOOL< {payload.get('tool_id')} "
           f"→ {_short(payload.get('result'))}")
        return payload

    def on_after_execute(payload: dict) -> dict:
        aid = payload.get("agent_id")
        status = getattr(getattr(payload.get("outcome"), "status", None), "value", None)
        kind = "SUBAGENT(私有)" if str(aid).startswith("sub-") else "AGENT"
        extra = ""
        if str(aid).startswith("sub-"):
            out = str(getattr(payload.get("outcome"), "output", ""))
            extra = f"  output={out[:90]!r}"
        _p(f"  [HOOK ] {kind} done: {aid} → {status}{extra}")
        if not str(aid).startswith("sub-"):
            _print_board(runner, f"after {aid}")
        return payload

    hm = runner._hook_manager
    hm.register(HookEvent.PATTERN_BEFORE_STEP, on_step)
    hm.register(HookEvent.PATTERN_BEFORE_LLM, on_llm_before)
    hm.register(HookEvent.LLM_AFTER_CALL, on_llm_after)
    hm.register(HookEvent.TOOL_BEFORE_INVOKE, on_tool_before)
    hm.register(HookEvent.TOOL_AFTER_INVOKE, on_tool_after)
    hm.register(HookEvent.PATTERN_AFTER_EXECUTE, on_after_execute)


# ── 跑 ────────────────────────────────────────────────────────────────────
async def run() -> None:
    work_dir = Path(tempfile.mkdtemp(prefix="multitask_real_"))
    config_path = Path(__file__).parent.parent / "agent.json"
    runner = OrchestratorRunner(
        config_path, enable_monitor_resident=False)
    _register_printers(runner)

    _p("═" * 72)
    _p(f"═══ REALISTIC 多任务   objective={OBJECTIVE!r}")
    _p("═══ t1(models)‖t2(utils) 并行 → t3(app, 依赖+派reviewer subagent)")
    _p(f"═══ work_dir={work_dir}")
    _p("═" * 72)

    director_llm = _director_llm()
    coder_llm = SmartCoderLLM()
    reviewer_llm = ReviewerLLM()
    orig_bundle = OrchestratorRunner._ensure_bundle

    def patched_bundle(self, agent_type: str):
        bundle = orig_bundle(self, agent_type)
        base = agent_type.replace("__leaf", "")  # 叶子化把 reviewer→reviewer__leaf
        if base == "director":
            bundle.llm_client = director_llm
        elif base == "coder":
            bundle.llm_client = coder_llm
        elif base == "reviewer":
            bundle.llm_client = reviewer_llm
        return bundle

    with (
        patch("openagents_orchestration.patterns.corecoder._detect_project_type",
              lambda cwd: {"type": "Python", "test_cmd": "python -c 'import app'", "lint_cmd": ""}),
        patch.object(OrchestratorRunner, "_ensure_bundle", patched_bundle),
        patch.object(ClassifyIntentTool, "invoke", _fake_classify),
        patch.object(DecomposeTool, "invoke", _fake_decompose),
    ):
        report = await runner.run(
            OBJECTIVE,
            budget=Budget(token_limit=80_000, time_limit_s=300, max_steps=60),
            work_dir=str(work_dir),
        )

    board = runner.state_board
    _p("═" * 72)
    _p("═══ 最终结果")
    _print_board(runner, "FINAL")
    _p(f"  success_rate: {report.success_rate:.0%}")
    files = sorted(str(p.relative_to(work_dir)) for p in work_dir.rglob("*.py") if p.is_file())
    _p(f"  产出文件: {files}")
    for f in files:  # 证明 app.py 真依赖 models+utils
        head = (work_dir / f).read_text().splitlines()[:2]
        _p(f"    {f}: {head}")
    _p("═" * 72)

    completed = sum(1 for t in board.tasks.values() if t.status == TaskStatus.COMPLETED)
    assert completed == 3, f"expected 3 completed, got {completed}"
    assert (work_dir / "app.py").exists(), "app.py not written"
    _p("✅ 3/3 任务完成 · coder 真写文件 · t3 真派 reviewer subagent · 依赖产物落盘")


if __name__ == "__main__":
    asyncio.run(run())

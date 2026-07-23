"""full_mock.py — 全方位综合 mock：把编排加固的所有机制串起来跑（零真实 API）。

覆盖这次会话做的全部关键机制，端到端验证（尤其 verify hook 之前只有单测）：

  t1(models)‖t2(utils)  —— batch 并行（无依赖）
       ↓
  t3(app, deps[t1,t2], verify=True)  —— 依赖门控 + coder 多步 + 派 reviewer subagent
       ↓                                  + 完成后 verifier 核验 → PASS
  t4(config, deps[t3], verify=True)  —— verifier 核验 → FAIL → FIX_NEEDED（防假完成）

机制清单：
  ✓ 多任务 decompose + batch 并行 + 依赖门控(is_ready)
  ✓ coder 多步 ReAct(write_file → bash → complete_task)
  ✓ subagent 同步委托(叶子化 depth=1) + 回传透传(sub_agent._clean_output)
  ✓ verify hook(director spawn verify=True → needs_verify → verifier 判完成度)
  ✓ verify PASS 保持 COMPLETED / verify FAIL 标 FIX_NEEDED
  ✓ 依赖产物传递 + 六层 trace + StateBoard 状态流转

4 种 mock LLM：director(scripted) / coder(smart) / reviewer / verifier。

用法：  python scripts/full_mock.py
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
for _k, _v in {
    "LLM_API_BASE": "http://mock-llm.local/v1", "LLM_API_KEY": "mock-key",
    "LLM_MODEL": "mock-model",
}.items():
    os.environ.setdefault(_k, _v)

from openagents_orchestration.hooks import HookEvent
from openagents_orchestration.models.task import TaskGraph, TaskNode
from openagents_orchestration.runtime.runner import OrchestratorRunner
from openagents_orchestration.runtime.state_board import Budget, TaskStatus
from openagents_orchestration.tools.director.classify_intent import ClassifyIntentTool
from openagents_orchestration.tools.director.decompose import DecomposeTool

OBJECTIVE = "Build a small service: models + utils → app (reviewed+verified) → config"

CONTENT = {
    "models.py": "class User:\n    def __init__(self, name):\n        self.name = name\n",
    "utils.py": "def greet(name):\n    return f'hello {name}'\n",
    "app.py": ("from models import User\nfrom utils import greet\n\n"
               "if __name__ == '__main__':\n    print(greet(User('io').name))\n"),
    "config.py": "DEBUG = True\n",
}


# ── mock LLM 基件 ─────────────────────────────────────────────────────────
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


_PLAN = '{"steps": ["do it"], "confidence": 8}'


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


def _target(blob: str) -> str:
    m = re.search(r"Expected artifacts:.*?([\w]+\.py)", blob, re.DOTALL)
    if m:
        return m.group(1)
    m = re.search(r"([\w]+\.py)", blob)
    return m.group(1) if m else "out.py"


class ScriptedLLM:
    """director 固定剧本（串行 ReAct）。"""
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


class SmartCoderLLM:
    """无状态内容感知（所有 coder 共享）：write → bash → (t3) sub_agent → complete_task。"""
    provider_name = "openai_compatible"

    async def generate(self, **kw: Any) -> Any:
        if kw.get("tools") is None:
            return FakeResponse(output_text=_PLAN)
        blob = _blob(kw.get("messages", []))
        target = _target(blob)
        wrote = "lines to" in blob
        verified = "VERIFIED-OK" in blob
        needs_review = "REVIEW_REQUIRED" in blob
        reviewed = "REVIEW_OK" in blob
        if not wrote:
            return FakeResponse(tool_calls=[FakeToolCall(
                "write_file", {"file_path": target, "content": CONTENT.get(target, f"# {target}\n")})])
        if not verified:
            return FakeResponse(tool_calls=[FakeToolCall(
                "bash", {"command": f"test -f {target} && echo VERIFIED-OK"})])
        if needs_review and not reviewed:
            return FakeResponse(tool_calls=[FakeToolCall(
                "sub_agent", {"agent_type": "reviewer", "instruction": f"Review {target}"})])
        return FakeResponse(tool_calls=[FakeToolCall(
            "complete_task", {"summary": f"Implemented {target}", "artifacts": [target]})])


class ReviewerLLM:
    """reviewer subagent：一步给审查结论。"""
    provider_name = "openai_compatible"

    async def generate(self, **kw: Any) -> Any:
        if kw.get("tools") is None:
            return FakeResponse(output_text=_PLAN)
        return FakeResponse(output_text="REVIEW_OK: imports resolve, logic correct.")


class VerifierLLM:
    """verifier：按目标文件判完成度。app.py→PASS；config.py→FAIL(模拟假完成被抓)。"""
    provider_name = "openai_compatible"

    async def generate(self, **kw: Any) -> Any:
        if kw.get("tools") is None:
            return FakeResponse(output_text=_PLAN)
        blob = _blob(kw.get("messages", []))
        if "config.py" in blob:
            return FakeResponse(output_text="VERDICT: FAIL\nREASON: config.py missing required keys")
        return FakeResponse(output_text="VERDICT: PASS\nREASON: all expected files present and correct")


# ── mock 决策工具：decompose 4 任务 -----------------------------------------
async def _fake_classify(self, params, context):  # noqa: ANN001
    return {"intent": {"task_type": "feature", "complexity": "complex", "external": [],
                       "priority": "normal", "confidence": 0.9, "reason": "x", "source": "mock"}}


async def _fake_decompose(self, params, context):  # noqa: ANN001
    board = getattr(getattr(context, "deps", None), "state_board", None)
    board.add_tasks(TaskGraph(objective=OBJECTIVE, tasks=[
        TaskNode("t1", "Build models.py", "coder", expected_artifacts=["models.py"]),
        TaskNode("t2", "Build utils.py", "coder", expected_artifacts=["utils.py"]),
        TaskNode("t3", "Wire app.py importing models+utils", "coder",
                 dependencies=["t1", "t2"], expected_artifacts=["app.py"],
                 input_context="REVIEW_REQUIRED: spawn a reviewer subagent after writing."),
        TaskNode("t4", "Write config.py", "coder",
                 dependencies=["t3"], expected_artifacts=["config.py"]),
    ]))
    return {"tasks_added": 4, "task_ids": ["t1", "t2", "t3", "t4"]}


def _director_llm() -> ScriptedLLM:
    return ScriptedLLM([
        FakeResponse(tool_calls=[FakeToolCall("classify_intent", {"objective": OBJECTIVE})]),
        FakeResponse(tool_calls=[FakeToolCall("decompose", {"objective": OBJECTIVE})]),
        FakeResponse(tool_calls=[FakeToolCall("show_state", {})]),
        FakeResponse(tool_calls=[FakeToolCall("spawn_agent", {"task_ids": ["t1", "t2"]})]),
        FakeResponse(tool_calls=[FakeToolCall("show_state", {})]),
        # t3: 关键节点(被 t4 依赖) → director 开 verify
        FakeResponse(tool_calls=[FakeToolCall("spawn_agent", {"task_id": "t3", "verify": True})]),
        FakeResponse(tool_calls=[FakeToolCall("show_state", {})]),
        # t4: 也开 verify → verifier 会判 FAIL(模拟假完成)
        FakeResponse(tool_calls=[FakeToolCall("spawn_agent", {"task_id": "t4", "verify": True})]),
        FakeResponse(tool_calls=[FakeToolCall("show_state", {})]),
        FakeResponse(tool_calls=[FakeToolCall("finalize", {"summary": "service built"})]),
        FakeResponse(output_text="Done"),
    ])


# ── trace ──────────────────────────────────────────────────────────────────
def _p(m: str) -> None:
    print(m, file=sys.stderr, flush=True)


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
        vflag = " {verify}" if getattr(t, "needs_verify", False) else ""
        _p(f"  │  {t.task_id}: {st:<10} deps={deps}{gate}{vflag}")
    _p(f"  │  agents: {sorted(board.agents)}")
    prog = board.progress_summary()
    _p(f"  └─ {prog['completed_tasks']}/{prog['total_tasks']} done\n")


def _register(runner: OrchestratorRunner) -> None:
    def on_tool(payload: dict) -> dict:
        tid = payload.get("tool_id")
        if tid in ("write_file", "bash", "sub_agent", "spawn_agent", "decompose", "finalize"):
            p = payload.get("params") or {}
            brief = p.get("file_path") or p.get("command") or p.get("agent_type") \
                or p.get("task_ids") or p.get("task_id") or ""
            v = " verify=True" if p.get("verify") else ""
            _p(f"    · [{payload.get('agent_id', '?')}] {tid}({brief}){v}")
        return payload

    def on_after(payload: dict) -> dict:
        aid = payload.get("agent_id")
        st = getattr(getattr(payload.get("outcome"), "status", None), "value", None)
        kind = ("VERIFIER" if str(aid).startswith("verifier-")
                else "SUBAGENT" if str(aid).startswith("sub-") else "AGENT")
        _p(f"  [HOOK] {kind} done: {aid} → {st}")
        if kind == "AGENT":
            _print_board(runner, f"after {aid}")
        return payload

    # verify 事件 printer
    def on_event(payload: dict) -> dict:
        return payload

    hm = runner._hook_manager
    hm.register(HookEvent.TOOL_BEFORE_INVOKE, on_tool)
    hm.register(HookEvent.PATTERN_AFTER_EXECUTE, on_after)


# ── 跑 ──────────────────────────────────────────────────────────────────────
async def run() -> None:
    work_dir = Path(tempfile.mkdtemp(prefix="full_mock_"))
    runner = OrchestratorRunner(Path(__file__).parent.parent / "agent.json",
                                enable_monitor_resident=False)
    _register(runner)
    _p("═" * 76)
    _p("═══ 全方位 mock: 并行 + 依赖 + coder多步 + subagent + verify(PASS/FAIL)")
    _p("═══ t1‖t2 → t3(verify+reviewer→PASS) → t4(verify→FAIL→FIX_NEEDED)")
    _p("═" * 76)

    director_llm = _director_llm()
    coder_llm, reviewer_llm, verifier_llm = SmartCoderLLM(), ReviewerLLM(), VerifierLLM()
    orig = OrchestratorRunner._ensure_bundle

    def patched(self, agent_type: str):
        b = orig(self, agent_type)
        base = agent_type.replace("__leaf", "")  # 叶子化把 reviewer→reviewer__leaf
        b.llm_client = {"director": director_llm, "coder": coder_llm,
                        "reviewer": reviewer_llm, "verifier": verifier_llm}.get(base, b.llm_client)
        return b

    with (
        patch("openagents_orchestration.patterns.corecoder._detect_project_type",
              lambda cwd: {"type": "Python", "test_cmd": "python -c 'import app'", "lint_cmd": ""}),
        patch.object(OrchestratorRunner, "_ensure_bundle", patched),
        patch.object(ClassifyIntentTool, "invoke", _fake_classify),
        patch.object(DecomposeTool, "invoke", _fake_decompose),
    ):
        await runner.run(OBJECTIVE,
                         budget=Budget(token_limit=120_000, time_limit_s=300, max_steps=80),
                         work_dir=str(work_dir))

    board = runner.state_board
    _p("═" * 76)
    _p("═══ 最终结果")
    _print_board(runner, "FINAL")
    verify_events = [e for e in board.events if e.event_type.startswith("verify.")]
    _p("  verify 事件:")
    for e in verify_events:
        _p(f"    {e.event_type}: {e.message}")
    files = sorted(p.name for p in work_dir.glob("*.py"))
    _p(f"  产物: {files}")
    _p("═" * 76)

    # 断言全方位机制
    t = {tid: board.get_task(tid).status for tid in ("t1", "t2", "t3", "t4")}
    assert t["t1"] == TaskStatus.COMPLETED and t["t2"] == TaskStatus.COMPLETED, f"t1/t2: {t}"
    assert t["t3"] == TaskStatus.COMPLETED, f"t3 (verify PASS) should stay COMPLETED: {t['t3']}"
    assert t["t4"] == TaskStatus.FIX_NEEDED, f"t4 (verify FAIL) should be FIX_NEEDED: {t['t4']}"
    assert any(e.event_type == "verify.pass" for e in verify_events), "no verify.pass event"
    assert any(e.event_type == "verify.fail" for e in verify_events), "no verify.fail event"
    _p("✅ 全方位通过: 并行+依赖+coder多步+subagent委托 都跑通;")
    _p("   verify hook 端到端: t3 PASS 保持 COMPLETED, t4 FAIL 抓到假完成→FIX_NEEDED")


if __name__ == "__main__":
    asyncio.run(run())

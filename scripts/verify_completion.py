"""verify_completion.py — 实测 coder 的任务完成判定路径。

验证目标（回答「complete_task 是否必须 / 能否像 Claude Code 纯文本收尾」）：
单 coder 写文件后，跑**匹配 test_cmd 的真实验证命令**（exit 0）清掉 verification
闸，然后**纯文本收尾**（不调 complete_task）。若 task 仍能 COMPLETED，证明：

  纯文本直觉收尾可行；complete_task 不是必须，只是「绕过未清闸」的逃生通道。

机制（corecoder.py 实测得来）：
- 闸设置(_maybe_set_pending_verification): permission_mode 非 acceptEdits/auto 时，
  write_file 后把文件加进 pending_verification.files
- 闸清除(_try_clear_verification): bash 命令 startswith test_cmd 且 exit_code==0 → 清
- 闸挡(execute :475): pending.files 非空时，纯文本收尾被挡 → 必须先验证

用法：  python scripts/verify_completion.py
"""

# ruff: noqa: E402
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

OBJECTIVE = "Create hello.py"
TEST_CMD = "python -c"  # 清闸前缀(bash command startswith 它即匹配);nudge 只含这个前缀


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


_PLAN = '{"steps": ["write + verify"], "confidence": 8}'


def _blob(messages: list[Any]) -> str:
    parts = []
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
    provider_name = "openai_compatible"

    def __init__(self, responses):
        self._r, self._i = list(responses), 0

    async def generate(self, **kw):
        if kw.get("tools") is None:
            return FakeResponse(output_text=_PLAN)
        if self._i >= len(self._r):
            return FakeResponse(output_text="")
        r = self._r[self._i]
        self._i += 1
        return r


class CoderLLM:
    """write_file → bash(真 test_cmd) → 纯文本收尾（关键：不调 complete_task）。"""

    provider_name = "openai_compatible"

    async def generate(self, **kw):
        if kw.get("tools") is None:
            return FakeResponse(output_text=_PLAN)
        blob = _blob(kw.get("messages", []))
        wrote = "lines to" in blob
        # bash 的 stdout 经 _format_tool_result 的 message 字段进 blob；FakeResponse 的
        # tool_call command 不进 blob(assistant_content 空)，故靠 bash 输出 token 判断。
        verified = "99887766" in blob  # bash 真跑的输出 token；nudge 的 test_cmd("python -c")不含它
        if not wrote:
            return FakeResponse(tool_calls=[FakeToolCall(
                "write_file", {"file_path": "hello.py", "content": "print('hello')\n"})])
        if not verified:  # 跑匹配 test_cmd 的验证命令 → 清闸；输出 token 供判断
            return FakeResponse(tool_calls=[FakeToolCall(
                "bash", {"command": 'python -c "print(99887766)"'})])
        # 闸已清，纯文本直觉收尾 —— 不调 complete_task
        return FakeResponse(output_text="Done — hello.py written and verified.")


async def _fake_classify(self, params, context):
    return {"intent": {"task_type": "feature", "complexity": "simple", "external": [],
                       "priority": "normal", "confidence": 0.9, "reason": "x", "source": "mock"}}


async def _fake_decompose(self, params, context):
    board = getattr(getattr(context, "deps", None), "state_board", None)
    board.add_tasks(TaskGraph(objective=OBJECTIVE, tasks=[
        TaskNode("t1", "Create hello.py", "coder", expected_artifacts=["hello.py"])]))
    return {"tasks_added": 1, "task_ids": ["t1"]}


def _p(m): print(m, file=sys.stderr, flush=True)


_used_complete_task = {"hit": False}


def _register(runner):
    def on_tool(payload):
        tid = payload.get("tool_id")
        aid = payload.get("agent_id", "?")
        if tid == "complete_task":
            _used_complete_task["hit"] = True
        if tid in ("write_file", "bash", "complete_task", "spawn_agent"):
            p = payload.get("params") or {}
            _p(f"    · [{aid}] {tid}({p.get('file_path') or p.get('command') or p.get('task_id') or ''})")
        return payload
    runner._hook_manager.register(HookEvent.TOOL_BEFORE_INVOKE, on_tool)


async def run():
    work_dir = Path(tempfile.mkdtemp(prefix="verify_comp_"))
    runner = OrchestratorRunner(Path(__file__).parent.parent / "agent.json",
                                enable_monitor_resident=False)
    _register(runner)
    _p("═" * 64)
    _p(f"═══ 验证完成判定: coder write→bash({TEST_CMD})→纯文本收尾")
    _p("═══ permission_mode 默认下, 闸设了; 看真 test_cmd 能否清闸+纯文本完成")
    _p("═" * 64)

    director_llm = ScriptedLLM([
        FakeResponse(tool_calls=[FakeToolCall("classify_intent", {"objective": OBJECTIVE})]),
        FakeResponse(tool_calls=[FakeToolCall("decompose", {"objective": OBJECTIVE})]),
        FakeResponse(tool_calls=[FakeToolCall("spawn_agent", {"task_id": "t1"})]),
        FakeResponse(tool_calls=[FakeToolCall("finalize", {"summary": "hello.py done"})]),
        FakeResponse(output_text="Done"),
    ])
    coder_llm = CoderLLM()
    orig = OrchestratorRunner._ensure_bundle

    def patched(self, agent_type):
        b = orig(self, agent_type)
        if agent_type == "director":
            b.llm_client = director_llm
        elif agent_type.replace("__leaf", "") == "coder":
            b.llm_client = coder_llm
        return b

    with (
        patch("openagents_orchestration.patterns.corecoder._detect_project_type",
              lambda cwd: {"type": "Python", "test_cmd": TEST_CMD, "lint_cmd": ""}),
        patch.object(OrchestratorRunner, "_ensure_bundle", patched),
        patch.object(ClassifyIntentTool, "invoke", _fake_classify),
        patch.object(DecomposeTool, "invoke", _fake_decompose),
    ):
        await runner.run(OBJECTIVE, budget=Budget(token_limit=40_000, time_limit_s=120, max_steps=20),
                         work_dir=str(work_dir))

    board = runner.state_board
    t1 = board.get_task("t1")
    _p("═" * 64)
    _p(f"  t1.status      = {t1.status.value}")
    _p(f"  调用 complete_task? {_used_complete_task['hit']}")
    _p(f"  hello.py 落盘?   {(work_dir / 'hello.py').exists()}")
    _p("═" * 64)
    assert t1.status == TaskStatus.COMPLETED, f"t1 not completed: {t1.status}"
    assert not _used_complete_task["hit"], "coder 用了 complete_task —— 纯文本路径没走通"
    _p("✅ 验证通过: coder 跑真 test_cmd 清闸后, 纯文本收尾 COMPLETED, 全程没调 complete_task")
    _p("   → complete_task 非必须; Stage 支持 Claude Code 式纯文本直觉收尾")


if __name__ == "__main__":
    asyncio.run(run())

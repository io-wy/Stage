"""sub_agent 有界递归 spawn 的边界测试（深度=1 + 叶子去工具）。

固化的语义（io-wy 定）：
- coder 经 director 的 spawn_agent 派出，ctx.state 无 __sub_agent_depth__ → depth 0，
  不算 subagent，是 task 执行者（计数根）。
- coder 经 sub_agent 工具派出的才算 subagent（depth 1），且：
  ① depth 计数封顶 MAX=1（subagent 再调 sub_agent 被拒）
  ② 叶子化：子 agent 被 strip 掉 sub_agent 工具，物理上无法再递归（双保险）
- run_agent/_run_single 接受 state 参数，透传 depth（修 standalone TypeError + 封顶失效）。

全程不打真实 LLM（X-09）——用轻量 fake_runner + 纯编译 + 签名内省。
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from openagents_orchestration.models.pattern import (
    PatternOutcome,
    PatternOutcomeStatus,
)
from openagents_orchestration.runtime.agent_loader import _load_json, compile_one_spec
from openagents_orchestration.runtime.runner import OrchestratorRunner
from openagents_orchestration.tools.corecoder.sub_agent import (
    _MAX_SUB_AGENT_DEPTH,
    SubAgentTool,
)

_REPO = Path(__file__).parent.parent


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("LLM_API_BASE", "http://test/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")


class FakeRunner:
    """最小 runner 替身：记录每次 run_agent 的入参，不打真实 LLM。"""

    def __init__(self, agents: dict | None = None):
        self._config_path = Path("agent.json")
        self._agents_by_id = agents or {}
        self._bundles: dict = {}
        self.spawn_calls: list[dict] = []

    async def run_agent(self, agent_type, input_text, agent_id=None, state=None):
        self.spawn_calls.append(
            {"agent_type": agent_type, "state": state, "agent_id": agent_id}
        )
        return PatternOutcome(
            output=f"ran {agent_type}",
            status=PatternOutcomeStatus.COMPLETED,
        )


def _ctx(runner, state=None):
    return type(
        "Ctx",
        (),
        {
            "deps": type("D", (), {"runner": runner})(),
            "state": state or {},
            "agent_id": "coder-t1",
        },
    )()


def _coder_def():
    """真实编译 coder 角色（带 sub_agent 工具），不打 LLM。"""
    base = _load_json(_REPO / "agents" / "_base.json")
    return compile_one_spec(_load_json(_REPO / "agents" / "coder.json"), base=base)


# -- 语义 1：MAX=1 ----------------------------------------------------------


def test_max_depth_is_one():
    assert _MAX_SUB_AGENT_DEPTH == 1


# -- 语义 2：coder(depth 0) 能派一层 subagent，并透传 depth=1 ----------------


async def test_coder_spawns_one_level_and_propagates_depth():
    """coder 无 depth state → 视为 depth 0 → 允许派一层，子 agent 收到 depth=1。"""
    runner = FakeRunner()
    result = await SubAgentTool().invoke(
        {"agent_type": "coder", "instruction": "do X"}, _ctx(runner, state={})
    )
    assert result["status"] == "completed"
    assert len(runner.spawn_calls) == 1
    assert runner.spawn_calls[0]["state"] == {"__sub_agent_depth__": 1}


# -- 语义 3：subagent(depth 1) 不能再递归 -----------------------------------


async def test_subagent_at_depth_1_cannot_recurse():
    """已是 depth 1 的 subagent 再调 sub_agent → 被 depth guard 拒绝，不派出。"""
    runner = FakeRunner()
    tool = SubAgentTool()
    with pytest.raises(Exception, match="depth limit|depth"):
        await tool.invoke(
            {"agent_type": "coder", "instruction": "deeper"},
            _ctx(runner, state={"__sub_agent_depth__": 1}),
        )
    assert runner.spawn_calls == []


# -- 语义 4：叶子化——子 agent 被 strip 掉 sub_agent 工具（双保险）----------


def test_leafify_strips_sub_agent_tool():
    coder_def = _coder_def()
    # 前提：coder 本身带 sub_agent
    assert any(SubAgentTool._tool_ref_id(t) == "sub_agent" for t in coder_def.tools)

    runner = FakeRunner(agents={"coder": coder_def})
    leaf_id = SubAgentTool()._leafify("coder", runner)

    assert leaf_id == "coder__leaf"
    leaf_def = runner._agents_by_id["coder__leaf"]
    # 叶子无 sub_agent；其余工具保留
    assert all(SubAgentTool._tool_ref_id(t) != "sub_agent" for t in leaf_def.tools)
    assert len(leaf_def.tools) == len(coder_def.tools) - 1
    # 原 coder def 不被污染
    assert any(SubAgentTool._tool_ref_id(t) == "sub_agent" for t in coder_def.tools)


def test_leafify_unknown_role_falls_back():
    """未注册角色 → fallback 原 type（depth 计数仍兜底），不抛错。"""
    runner = FakeRunner()
    assert SubAgentTool()._leafify("ghost", runner) == "ghost"


async def test_coder_spawn_uses_leaf_type():
    """端到端：coder spawn 真实 coder → 实际跑的是 coder__leaf + depth 透传。"""
    runner = FakeRunner(agents={"coder": _coder_def()})
    await SubAgentTool().invoke(
        {"agent_type": "coder", "instruction": "X"}, _ctx(runner, state={})
    )
    call = runner.spawn_calls[0]
    assert call["agent_type"] == "coder__leaf"
    assert call["state"] == {"__sub_agent_depth__": 1}


# -- 语义 5：run_agent / _run_single 接受 state（修 TypeError + 透传）--------


def test_run_agent_and_run_single_accept_state():
    assert "state" in inspect.signature(OrchestratorRunner.run_agent).parameters
    assert "state" in inspect.signature(OrchestratorRunner._run_single).parameters


# -- 回传：subagent 的 output 透传进 message（修「coder 拿不到 subagent 产出」）--


async def test_subagent_output_surfaced_in_message():
    """sub_agent 回传的 message 应含 subagent 的实质 output。

    coder 经 _format_tool_result 只看 message 字段，所以 output 必须并入 message，
    否则 coder 派 subagent 只拿到"完成了"信号、丢失实质结论。
    """
    runner = FakeRunner(agents={"coder": _coder_def()})
    result = await SubAgentTool().invoke(
        {"agent_type": "coder", "instruction": "do X"}, _ctx(runner, state={})
    )
    # FakeRunner.run_agent 返回 output="ran coder__leaf"（叶子化后的 type）
    assert "ran coder__leaf" in result["message"]


def test_clean_output_unwraps_nested_patternoutcome():
    """PIT-001：output 是嵌套 PatternOutcome repr 时，抠出内层文本。"""
    raw = (
        "PatternOutcome(output='REVIEW_OK: imports resolve, logic is correct.', "
        "status=<PatternOutcomeStatus.COMPLETED: 'completed'>)"
    )
    assert SubAgentTool._clean_output(raw) == "REVIEW_OK: imports resolve, logic is correct."
    # 普通文本原样返回
    assert SubAgentTool._clean_output("plain summary") == "plain summary"

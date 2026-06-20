"""Tests for sub_agent 的 agent_spec 参数——工具自己编译 inline 角色。

runner 不再提供 register_agent_spec；sub_agent 直接调用
compile_one_spec 并把产物写入 runner._agents_by_id。
不打真实 LLM（X-09）。
"""

from __future__ import annotations

import pytest

from openagents_orchestration.core.agent_loader import AgentSpecError
from openagents_orchestration.tools.corecoder.sub_agent import SubAgentTool


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("LLM_API_BASE", "http://test/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")


@pytest.fixture
def fake_runner():
    """A minimal runner stand-in that sub_agent can write temporary agents to."""

    class FakeRunner:
        def __init__(self):
            self._config_path = __import__("pathlib").Path("agent.json")
            self._agents_by_id = {}
            self._bundles = {}

        async def run_agent(self, agent_type, input_text, agent_id=None, state=None):
            self._last_spawned = (agent_type, input_text)
            return f"ran {agent_type}"

    return FakeRunner()


def test_sub_agent_schema_has_agent_spec():
    schema = SubAgentTool().schema()
    assert "agent_spec" in schema["properties"]
    # agent_type 不再强制必填（可用 agent_spec 替代）
    assert schema["required"] == ["instruction"]


async def test_sub_agent_inline_spec_compiles_and_spawns(fake_runner):
    """提供 agent_spec → 工具自己编译、写入 runner，并以其 id spawn。"""
    ctx = type(
        "Ctx",
        (),
        {"deps": type("D", (), {"runner": fake_runner})(), "state": {}},
    )()
    tool = SubAgentTool()
    result = await tool.invoke(
        {
            "agent_spec": {
                "id": "oneoff",
                "extends": "_base.json",
                "tools": ["+grep"],
            },
            "instruction": "audit X",
        },
        ctx,
    )
    assert "oneoff" in fake_runner._agents_by_id
    assert fake_runner._last_spawned[0] == "oneoff"
    assert result["status"] == "completed"


async def test_sub_agent_inline_spec_clears_bundle(fake_runner):
    fake_runner._bundles["oneoff"] = object()
    ctx = type(
        "Ctx",
        (),
        {"deps": type("D", (), {"runner": fake_runner})(), "state": {}},
    )()
    await SubAgentTool().invoke(
        {
            "agent_spec": {"id": "oneoff", "extends": "_base.json"},
            "instruction": "do X",
        },
        ctx,
    )
    assert "oneoff" not in fake_runner._bundles


async def test_sub_agent_inline_spec_rejects_unknown_tool(fake_runner):
    ctx = type(
        "Ctx",
        (),
        {"deps": type("D", (), {"runner": fake_runner})(), "state": {}},
    )()
    with pytest.raises(Exception, match="未知工具|unknown tool|非法"):
        await SubAgentTool().invoke(
            {
                "agent_spec": {
                    "id": "bad",
                    "extends": "_base.json",
                    "tools": ["+no_such_tool"],
                },
                "instruction": "do X",
            },
            ctx,
        )


async def test_sub_agent_requires_type_or_spec():
    class FakeRunner:
        async def run_agent(self, **k):
            return ""

    ctx = type(
        "Ctx", (), {"deps": type("D", (), {"runner": FakeRunner()})(), "state": {}}
    )()
    tool = SubAgentTool()
    with pytest.raises(Exception, match="agent_type or agent_spec"):
        await tool.invoke({"instruction": "do X"}, ctx)

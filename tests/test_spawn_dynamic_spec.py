"""Tests for 动态角色注册（spawn 现写 json）——runner.register_agent_spec +
sub_agent 的 agent_spec 参数。不打真实 LLM（X-09）。
"""

from __future__ import annotations

import pytest

from openagents_orchestration.core.runner import OrchestratorRunner
from openagents_orchestration.tools.corecoder.sub_agent import SubAgentTool


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("LLM_API_BASE", "http://test/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")


@pytest.fixture
def runner():
    # 从仓库根的 agent.json + agents/ 加载（真实布局）
    return OrchestratorRunner("agent.json")


# -- runner.register_agent_spec -------------------------------------------

def test_register_inline_spec(runner):
    aid = runner.register_agent_spec(
        {
            "id": "tmp-auditor",
            "extends": "_base.json",
            "prompts": ["prompts.roles.reviewer:ROLE"],
            "tools": ["+grep", "+glob"],
            "pattern": {"config": {"max_steps": 8}},
        }
    )
    assert aid == "tmp-auditor"
    d = runner._agents_by_id["tmp-auditor"]
    assert {t.id for t in d.tools} >= {"read_file", "grep", "glob"}
    assert d.pattern.config["max_steps"] == 8
    assert d.pattern.config["prompts"] == ["prompts.roles.reviewer:ROLE"]


def test_register_overwrites_and_clears_bundle(runner):
    runner.register_agent_spec({"id": "dup", "extends": "_base.json"})
    runner._bundles["dup"] = object()  # 假装有缓存
    runner.register_agent_spec({"id": "dup", "extends": "_base.json", "tools": ["+grep"]})
    assert "dup" not in runner._bundles  # 旧 bundle 缓存被清


def test_register_rejects_unknown_tool(runner):
    with pytest.raises(Exception, match="非法|未知|Config"):
        runner.register_agent_spec({"id": "bad", "tools": ["+no_such_tool"]})


def test_register_rejects_missing_id(runner):
    from openagents.errors.exceptions import ConfigError

    with pytest.raises(ConfigError):
        runner.register_agent_spec({"tools": ["+grep"]})


# -- sub_agent agent_spec 参数 --------------------------------------------

def test_sub_agent_schema_has_agent_spec():
    schema = SubAgentTool().schema()
    assert "agent_spec" in schema["properties"]
    # agent_type 不再强制必填（可用 agent_spec 替代）
    assert schema["required"] == ["instruction"]


async def test_sub_agent_inline_spec_registers_and_spawns():
    """提供 agent_spec → 工具注册临时角色并以其 id spawn。"""
    registered = {}

    class FakeRunner:
        def register_agent_spec(self, spec):
            registered["spec"] = spec
            return spec["id"]

        async def run_agent(self, agent_type, input_text, agent_id=None, state=None):
            registered["spawned_type"] = agent_type
            return f"ran {agent_type}"

    ctx = type("Ctx", (), {"deps": type("D", (), {"runner": FakeRunner()})(), "state": {}})()
    tool = SubAgentTool()
    result = await tool.invoke(
        {
            "agent_spec": {"id": "oneoff", "extends": "_base.json", "tools": ["+grep"]},
            "instruction": "audit X",
        },
        ctx,
    )
    assert registered["spec"]["id"] == "oneoff"
    assert registered["spawned_type"] == "oneoff"
    assert result["status"] == "completed"


async def test_sub_agent_requires_type_or_spec():
    """既无 agent_type 又无 agent_spec → 报错。"""
    class FakeRunner:
        async def run_agent(self, **k):
            return ""

    ctx = type("Ctx", (), {"deps": type("D", (), {"runner": FakeRunner()})(), "state": {}})()
    tool = SubAgentTool()
    with pytest.raises(Exception, match="agent_type or agent_spec"):
        await tool.invoke({"instruction": "do X"}, ctx)

"""Tests for core.agent_loader — Stage 的「一文件一 agent」编译层。

覆盖：extends 深合并 / 工具增量(+/-) / prompts+hooks 下沉 pattern.config /
未知工具拦截 / 坏文件容错 / 产物 pydantic 合法 / 动态 compile_one_spec。
不打真实 LLM（X-09）；环境变量在 conftest/此处通过 monkeypatch 提供。
"""

from __future__ import annotations

import json

import pytest

from openagents_orchestration.core.agent_loader import (
    TOOL_REGISTRY,
    AgentSpecError,
    compile_one_spec,
    load_agent_specs,
    resolve_hook,
)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    # agents/_base.json 的 llm 用 ${LLM_API_BASE}/${LLM_MODEL}，编译时需展开。
    monkeypatch.setenv("LLM_API_BASE", "http://test/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")


def _base() -> dict:
    return {
        "name": "Base",
        "memory": {"type": "window_buffer", "config": {"window_size": 20}},
        "pattern": {
            "impl": "openagents_orchestration.patterns.corecoder.CoreCoderPattern",
            "config": {"max_steps": 15},
        },
        "llm": {
            "provider": "openai_compatible",
            "api_base": "http://test/v1",
            "model": "test-model",
        },
        "tools": ["read_file", "bash", "think"],
    }


# -- extends / 深合并 ------------------------------------------------------

def test_extends_merges_base_and_override():
    spec = {"id": "coder", "pattern": {"config": {"max_steps": 30}}}
    out = compile_one_spec(spec, base=_base())
    assert out.id == "coder"
    # base 的 memory/llm 继承下来
    assert out.memory.type == "window_buffer"
    # override 的 max_steps 生效，base 的 impl 保留
    assert out.pattern.config["max_steps"] == 30
    assert "CoreCoderPattern" in out.pattern.impl


def test_name_defaults_to_id_when_absent():
    base = _base()
    del base["name"]  # base 与 spec 都无 name
    out = compile_one_spec({"id": "x"}, base=base)
    assert out.name == "x"  # 回退到 id


# -- 工具增量 --------------------------------------------------------------

def test_tool_delta_add():
    out = compile_one_spec(
        {"id": "c", "tools": ["+grep", "+glob"]}, base=_base()
    )
    ids = {t.id for t in out.tools}
    assert ids == {"read_file", "bash", "think", "grep", "glob"}


def test_tool_delta_remove():
    out = compile_one_spec({"id": "c", "tools": ["-bash"]}, base=_base())
    ids = {t.id for t in out.tools}
    assert ids == {"read_file", "think"}


def test_tool_full_replace_without_prefix():
    # 不带 +/- 前缀 → 视为显式全量列表，替换 base
    out = compile_one_spec(
        {"id": "c", "tools": ["grep", "glob"]}, base=_base()
    )
    ids = {t.id for t in out.tools}
    assert ids == {"grep", "glob"}


def test_tool_impl_resolved_from_registry():
    out = compile_one_spec({"id": "c", "tools": ["+grep"]}, base=_base())
    grep = next(t for t in out.tools if t.id == "grep")
    assert grep.impl == TOOL_REGISTRY["grep"]


def test_unknown_tool_rejected():
    with pytest.raises(AgentSpecError, match="未知工具"):
        compile_one_spec({"id": "c", "tools": ["+no_such_tool"]}, base=_base())


# -- prompts / hooks 下沉 --------------------------------------------------

def test_prompts_sink_into_pattern_config():
    out = compile_one_spec(
        {"id": "c", "prompts": ["mod:A", "mod:B"]}, base=_base()
    )
    assert out.pattern.config["prompts"] == ["mod:A", "mod:B"]


def test_hooks_sink_into_pattern_config():
    out = compile_one_spec(
        {"id": "c", "hooks": {"session.start": ["load_skills_into_context"]}},
        base=_base(),
    )
    assert out.pattern.config["hooks"] == {
        "session.start": ["load_skills_into_context"]
    }


# -- 必填 / 容错 -----------------------------------------------------------

def test_missing_id_raises():
    with pytest.raises(AgentSpecError, match="id"):
        compile_one_spec({"tools": ["+grep"]}, base=_base())


def test_bad_json_file_reports_name(tmp_path):
    (tmp_path / "_base.json").write_text(json.dumps(_base()), encoding="utf-8")
    (tmp_path / "broken.json").write_text("{not valid json", encoding="utf-8")
    with pytest.raises(AgentSpecError, match="broken.json"):
        load_agent_specs(tmp_path)


def test_empty_dir_raises(tmp_path):
    (tmp_path / "_base.json").write_text(json.dumps(_base()), encoding="utf-8")
    with pytest.raises(AgentSpecError, match="没有发现任何角色"):
        load_agent_specs(tmp_path)


# -- resolve_hook ----------------------------------------------------------

def test_resolve_hook_known():
    fn = resolve_hook("load_skills_into_context")
    assert callable(fn)


def test_resolve_hook_unknown_raises():
    with pytest.raises(AgentSpecError, match="未知 hook"):
        resolve_hook("no_such_hook")


# -- resolve_hook ----------------------------------------------------------

def test_resolve_hook_known():
    fn = resolve_hook("load_skills_into_context")
    assert callable(fn)


def test_resolve_hook_unknown_raises():
    with pytest.raises(AgentSpecError, match="未知 hook"):
        resolve_hook("no_such_hook")


# -- 真实 agents/ 目录：编译产物 == 旧 agent.json 等价（迁移保险）---------

def test_real_agents_compile_and_match_legacy_tools():
    """戏台 agents/ 编译出 7 角色，工具集与设计一致，全部 pydantic 合法。"""
    specs = load_agent_specs("agents")
    by_id = {s.id: s for s in specs}
    assert set(by_id) == {
        "director", "coder", "reviewer", "researcher",
        "github_agent", "monitor", "team_leader", "verifier",
    }
    # 抽查关键角色工具集（防 base+增量回归）
    assert {t.id for t in by_id["coder"].tools} >= {
        "read_file", "write_file", "edit_file", "apply_patch",
        "sub_agent", "complete_task",
    }
    # director 去掉了 send_message，加了调度工具
    director_tools = {t.id for t in by_id["director"].tools}
    assert "send_message" not in director_tools
    assert {"spawn_agent", "show_state", "finalize"} <= director_tools
    # team_leader 去掉 ask_human
    assert "ask_human" not in {t.id for t in by_id["team_leader"].tools}
    # 每角色都带 prompts 下沉；hooks 现在不在 base.json 中声明，runner 直接硬编码 skill 注入
    for s in specs:
        assert s.pattern.config.get("prompts")

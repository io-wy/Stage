"""Tests for CoreCoderPattern 的声明式 prompt 引用机制（_resolve_prompts + compose）。

覆盖：点路径解析 / 多片段按序拼接 / 缓存 / 坏引用降级 / 空回退 _PRINCIPLES /
director 双重注入修复（_PRINCIPLES_IS_BASE 语义）。不打真实 LLM（X-09）。
"""

from __future__ import annotations

from types import SimpleNamespace

from openagents_orchestration.patterns.corecoder import CoreCoderPattern
from openagents_orchestration.patterns.director import DirectorPattern
from openagents_orchestration.patterns.team_leader import TeamLeaderPattern


def _ctx():
    return SimpleNamespace(
        system_prompt_fragments=[], deps=None, scratch={}, state={}
    )


# -- _resolve_prompts ------------------------------------------------------

def test_resolve_single_ref():
    p = CoreCoderPattern(config={"prompts": ["prompts.roles.coder:ROLE"]})
    out = p._resolve_prompts()
    assert "Your role: Coder" in out


def test_resolve_multiple_refs_in_order():
    p = CoreCoderPattern(
        config={"prompts": ["prompts.roles.coder:ROLE", "prompts.constraints:CODER"]}
    )
    out = p._resolve_prompts()
    role_i = out.index("Your role: Coder")
    constraint_i = out.index("File modification rule")
    assert role_i < constraint_i  # 按声明顺序拼接


def test_resolve_is_cached():
    p = CoreCoderPattern(config={"prompts": ["prompts.roles.coder:ROLE"]})
    first = p._resolve_prompts()
    assert p._resolve_prompts() is p._resolved_prompt_cache
    assert p._resolve_prompts() == first


def test_resolve_dotted_path_form():
    # 支持 module.path.SYMBOL（无冒号）形式
    p = CoreCoderPattern(config={"prompts": ["prompts.roles.coder.ROLE"]})
    assert "Your role: Coder" in p._resolve_prompts()


def test_bad_ref_skipped_not_fatal():
    p = CoreCoderPattern(
        config={"prompts": ["prompts.roles.coder:ROLE", "no.such.module:X"]}
    )
    out = p._resolve_prompts()
    assert "Your role: Coder" in out  # 好的保留，坏的跳过


def test_empty_prompts_returns_empty():
    p = CoreCoderPattern(config={})
    assert p._resolve_prompts() == ""


# -- compose_system_prompt 接线 -------------------------------------------

def test_compose_includes_role_and_core_for_corecoder():
    p = CoreCoderPattern(config={"prompts": ["prompts.roles.coder:ROLE"]})
    p.context = _ctx()
    sp = p.compose_system_prompt("")
    assert "Your role: Coder" in sp  # 角色层
    assert "You are CoreCoder" in sp  # CORE 底座（_PRINCIPLES_IS_BASE=True）


def test_compose_empty_falls_back_to_principles():
    p = CoreCoderPattern(config={})
    p.context = _ctx()
    sp = p.compose_system_prompt("")
    assert "You are CoreCoder" in sp  # 无声明 → 回退 CORE，向后兼容


def test_corecoder_principles_is_base():
    assert CoreCoderPattern._PRINCIPLES_IS_BASE is True


# -- director / team_leader 双重注入修复 ----------------------------------

def test_director_principles_not_base():
    assert DirectorPattern._PRINCIPLES_IS_BASE is False


def test_director_no_double_injection():
    p = DirectorPattern(config={"prompts": ["prompts.roles.director:PRINCIPLES"]})
    p.context = _ctx()
    sp = p.compose_system_prompt("")
    # PRINCIPLES 声明 + _PRINCIPLES 类属性同源 → 只能出现一次
    assert sp.count("You are the Director") == 1


def test_team_leader_composes_director_plus_rules():
    p = TeamLeaderPattern(
        config={
            "prompts": [
                "prompts.roles.director:PRINCIPLES",
                "prompts.roles.team_leader:RULES",
            ]
        }
    )
    p.context = _ctx()
    sp = p.compose_system_prompt("")
    assert sp.count("You are the Director") == 1
    assert "Team Leader Rules" in sp


def test_team_leader_inherits_not_base():
    assert TeamLeaderPattern._PRINCIPLES_IS_BASE is False

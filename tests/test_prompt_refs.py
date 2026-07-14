"""Tests for CoreCoderPattern 的声明式 prompt 引用机制（_resolve_prompts + compose）。

设计原则（为什么不锚在真实 prompt 文案上）：
    这些测试验证的是**组装逻辑**——点路径解析、按序拼接、缓存、坏引用降级、
    空回退、以及 director/team_leader 的同源去重（_PRINCIPLES_IS_BASE 语义）。
    这些都是稳定契约；而 prompt 文案（中文化、润色、换措辞）是易变数据。
    把逻辑测试锚在文案字符串上 = brittle test：文案一改，逻辑明明正确测试
    照样挂，且报出「组合坏了」的假信号。

    因此断言一律锚在两类稳定标识上：
    1. 测试自造的哨兵片段（SENTINEL_* marker），经 _register_prompt_module 注入；
    2. 被测类的属性引用（如 ``CoreCoderPattern._PRINCIPLES.strip()``），而非其字面内容。
    文案任意改动都不影响这些测试；只有组装逻辑真坏，测试才挂。

不触真实 LLM / 文件系统。
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from openagents_orchestration.patterns.corecoder import CoreCoderPattern
from openagents_orchestration.patterns.director import DirectorPattern
from openagents_orchestration.patterns.team_leader import TeamLeaderPattern

# 稳定哨兵片段：断言锚在这些 marker 上，与真实 prompt 文案完全解耦。
ROLE = "<sentinel-role>"
RULE = "<sentinel-rule>"
DIRECTOR = "<sentinel-director>"
TEAM_RULES = "<sentinel-team-rules>"


def _register_prompt_module(monkeypatch: pytest.MonkeyPatch, name: str, **symbols: str) -> str:
    """注册一个临时 prompt 模块到 ``sys.modules``，返回模块名。

    供 ``_resolve_prompts`` 经 ``"name:SYMBOL"`` / ``"name.SYMBOL"`` 引用解析。
    ``monkeypatch.setitem`` 保证测试结束后 ``sys.modules`` 自动还原，无需手动清理。
    """
    mod = types.ModuleType(name)
    for attr, value in symbols.items():
        setattr(mod, attr, value)
    monkeypatch.setitem(sys.modules, name, mod)
    return name


def _ctx():
    return SimpleNamespace(system_prompt_fragments=[], deps=None, scratch={}, state={})


# -- _resolve_prompts：点路径解析 / 拼接顺序 / 缓存 / 降级 -----------------


def test_resolve_single_ref(monkeypatch):
    _register_prompt_module(monkeypatch, "fake_prompts_single", ROLE=ROLE)
    p = CoreCoderPattern(config={"prompts": ["fake_prompts_single:ROLE"]})
    assert p._resolve_prompts() == ROLE


def test_resolve_multiple_refs_in_order(monkeypatch):
    _register_prompt_module(monkeypatch, "fake_prompts_multi", ROLE=ROLE, RULE=RULE)
    p = CoreCoderPattern(
        config={"prompts": ["fake_prompts_multi:ROLE", "fake_prompts_multi:RULE"]}
    )
    out = p._resolve_prompts()
    assert out.index(ROLE) < out.index(RULE)  # 按声明顺序拼接


def test_resolve_is_cached(monkeypatch):
    _register_prompt_module(monkeypatch, "fake_prompts_cache", ROLE=ROLE)
    p = CoreCoderPattern(config={"prompts": ["fake_prompts_cache:ROLE"]})
    first = p._resolve_prompts()
    assert p._resolve_prompts() is p._resolved_prompt_cache
    assert p._resolve_prompts() == first


def test_resolve_dotted_path_form(monkeypatch):
    # 支持 module.path.SYMBOL（无冒号）形式
    _register_prompt_module(monkeypatch, "fake_prompts_dotted", ROLE=ROLE)
    p = CoreCoderPattern(config={"prompts": ["fake_prompts_dotted.ROLE"]})
    assert p._resolve_prompts() == ROLE


def test_bad_ref_skipped_not_fatal(monkeypatch):
    _register_prompt_module(monkeypatch, "fake_prompts_good", ROLE=ROLE)
    p = CoreCoderPattern(
        config={"prompts": ["fake_prompts_good:ROLE", "no.such.module:X"]}
    )
    assert p._resolve_prompts() == ROLE  # 好的保留，坏的跳过


def test_empty_prompts_returns_empty():
    p = CoreCoderPattern(config={})
    assert p._resolve_prompts() == ""


# -- compose_system_prompt 接线：角色层 + CORE 底座 ------------------------


def test_compose_includes_role_and_core_for_corecoder(monkeypatch):
    _register_prompt_module(monkeypatch, "fake_prompts_compose", ROLE=ROLE)
    p = CoreCoderPattern(config={"prompts": ["fake_prompts_compose:ROLE"]})
    p.context = _ctx()
    sp = p.compose_system_prompt("")
    assert ROLE in sp  # 角色层
    # CORE 底座：锚在类属性引用而非文案字面量（_PRINCIPLES_IS_BASE=True → 总追加）
    assert CoreCoderPattern._PRINCIPLES.strip() in sp


def test_compose_empty_falls_back_to_principles():
    p = CoreCoderPattern(config={})
    p.context = _ctx()
    sp = p.compose_system_prompt("")
    assert CoreCoderPattern._PRINCIPLES.strip() in sp  # 无声明 → 回退 CORE，向后兼容


def test_corecoder_principles_is_base():
    assert CoreCoderPattern._PRINCIPLES_IS_BASE is True


# -- director / team_leader 同源去重（_PRINCIPLES_IS_BASE=False） ----------


def test_director_principles_not_base():
    assert DirectorPattern._PRINCIPLES_IS_BASE is False


def test_director_no_double_injection(monkeypatch):
    # _PRINCIPLES 与声明式 prompt 同源 → _PRINCIPLES_IS_BASE=False 且角色层非空
    # 时不再追加 _PRINCIPLES，故同一片段只能出现一次。
    monkeypatch.setattr(DirectorPattern, "_PRINCIPLES", DIRECTOR)
    _register_prompt_module(monkeypatch, "fake_director", PRINCIPLES=DIRECTOR)
    p = DirectorPattern(config={"prompts": ["fake_director:PRINCIPLES"]})
    p.context = _ctx()
    sp = p.compose_system_prompt("")
    assert sp.count(DIRECTOR) == 1


def test_team_leader_composes_director_plus_rules(monkeypatch):
    # director 片段同源去重（count==1），team_leader 的 RULES 片段须出现。
    monkeypatch.setattr(TeamLeaderPattern, "_PRINCIPLES", DIRECTOR)
    _register_prompt_module(monkeypatch, "fake_team", PRINCIPLES=DIRECTOR, RULES=TEAM_RULES)
    p = TeamLeaderPattern(
        config={"prompts": ["fake_team:PRINCIPLES", "fake_team:RULES"]}
    )
    p.context = _ctx()
    sp = p.compose_system_prompt("")
    assert sp.count(DIRECTOR) == 1  # director 部分同源去重
    assert TEAM_RULES in sp  # team_leader RULES 部分出现


def test_team_leader_inherits_not_base():
    assert TeamLeaderPattern._PRINCIPLES_IS_BASE is False

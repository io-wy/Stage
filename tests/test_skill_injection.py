"""Tests for hooks.skill_loader — L1 skill catalog injection at session start.

``load_skills_into_context`` is a ``session.start`` hook handler: it takes the
HookManager event payload (a dict) and injects the skill catalog into the
agent's context. No adapter/factory — the handler consumes the payload directly.
"""

from __future__ import annotations

from types import SimpleNamespace

from openagents_orchestration.hooks import (
    load_skills_into_context,
    should_load_skills,
)
from openagents_orchestration.skills_registry import SkillRegistry


def _registry_with_one_skill(tmp_path) -> SkillRegistry:
    d = tmp_path / "skills" / "demo-pipeline"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: demo-pipeline\ndescription: Demo skill.\n---\n\n# Demo\n",
        encoding="utf-8",
    )
    return SkillRegistry(skills_dir=tmp_path / "skills")


def _ctx():
    return SimpleNamespace(system_prompt_fragments=[])


def _payload(ctx, agent_type, tool_names, registry):
    return {
        "context": ctx,
        "agent_type": agent_type,
        "tool_names": tool_names,
        "registry": registry,
    }


# -- should_load_skills ----------------------------------------------------

def test_should_load_for_agent_with_read_skill():
    assert should_load_skills("coder", ["read_file", "read_skill"]) is True


def test_should_load_for_director_without_read_skill():
    assert should_load_skills("director", ["show_state", "spawn_agent"]) is True


def test_should_not_load_for_plain_agent():
    assert should_load_skills("reviewer", ["read_file", "grep"]) is False


# -- load_skills_into_context (payload-driven hook handler) ----------------

def test_injects_catalog_for_eligible_agent(tmp_path):
    reg = _registry_with_one_skill(tmp_path)
    ctx = _ctx()
    load_skills_into_context(_payload(ctx, "coder", ["read_skill"], reg))
    assert len(ctx.system_prompt_fragments) == 1
    frag = ctx.system_prompt_fragments[0]
    assert "demo-pipeline" in frag and "Demo skill." in frag


def test_injects_for_director(tmp_path):
    reg = _registry_with_one_skill(tmp_path)
    ctx = _ctx()
    load_skills_into_context(_payload(ctx, "director", ["spawn_agent"], reg))
    assert "demo-pipeline" in ctx.system_prompt_fragments[0]


def test_no_injection_for_plain_agent(tmp_path):
    reg = _registry_with_one_skill(tmp_path)
    ctx = _ctx()
    load_skills_into_context(_payload(ctx, "reviewer", ["read_file"], reg))
    assert ctx.system_prompt_fragments == []


def test_no_injection_when_no_skills(tmp_path):
    reg = SkillRegistry(skills_dir=tmp_path / "empty")
    ctx = _ctx()
    load_skills_into_context(_payload(ctx, "coder", ["read_skill"], reg))
    assert ctx.system_prompt_fragments == []


def test_accepts_dict_keys_as_tool_names(tmp_path):
    # runner passes list(bundle.plugins.tools.keys())
    reg = _registry_with_one_skill(tmp_path)
    ctx = _ctx()
    tools = {"read_skill": object(), "read_file": object()}
    load_skills_into_context(_payload(ctx, "coder", list(tools.keys()), reg))
    assert len(ctx.system_prompt_fragments) == 1


def test_returns_payload_unchanged(tmp_path):
    # handler must return the payload so HookManager.run can chain it through
    reg = _registry_with_one_skill(tmp_path)
    ctx = _ctx()
    payload = _payload(ctx, "coder", ["read_skill"], reg)
    assert load_skills_into_context(payload) is payload


def test_missing_keys_are_safe():
    # defensive: a payload without context/registry must not raise
    assert load_skills_into_context({}) == {}
    assert load_skills_into_context(
        {"agent_type": "coder", "tool_names": ["read_skill"]}
    ) == {"agent_type": "coder", "tool_names": ["read_skill"]}

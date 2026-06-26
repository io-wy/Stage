"""Tests for the HookManager pipeline and its runner wiring.

Verifies the manager contract corecoder relies on (passthrough, mutate, chain,
block) and the session.start wiring that loads the skill catalog.
"""

from __future__ import annotations

from types import SimpleNamespace

from openagents_orchestration.hooks import (
    HookEvent,
    HookManager,
    load_skills_into_context,
)
from openagents_orchestration.skills_registry import SkillRegistry

# -- HookManager core ------------------------------------------------------

def test_hook_event_constants_are_strings():
    assert HookEvent.PATTERN_BEFORE_LLM == "pattern.before_llm"
    assert HookEvent.TOOL_AFTER_INVOKE == "tool.after_invoke"
    assert HookEvent.ARTIFACT_CLAIMED == "artifact.claimed"


def test_has_handlers_returns_false_when_empty():
    hm = HookManager()
    assert hm.has_handlers("missing") is False


def test_has_handlers_returns_true_when_registered():
    hm = HookManager()
    hm.register("e", lambda p: p)
    assert hm.has_handlers("e") is True


def test_is_blocked_false_by_default():
    hm = HookManager()
    blocked, reason = hm.is_blocked("tool.before_invoke", {"tool_id": "bash"})
    assert blocked is False
    assert reason == ""


def test_is_blocked_true_when_handler_blocks():
    hm = HookManager()
    hm.register("tool.before_invoke", lambda p: {**p, "blocked": True, "reason": "denied"})
    blocked, reason = hm.is_blocked("tool.before_invoke", {"tool_id": "bash"})
    assert blocked is True
    assert reason == "denied"


def test_run_with_no_handlers_passes_payload_through():
    hm = HookManager()
    payload = {"a": 1}
    assert hm.run("tool.before_invoke", payload) is payload


def test_handler_can_mutate_payload():
    hm = HookManager()
    hm.register("tool.before_invoke", lambda p: {**p, "params": {"x": 2}})
    out = hm.run("tool.before_invoke", {"tool_id": "bash", "params": {}})
    assert out["params"] == {"x": 2}


def test_handlers_chain_in_registration_order():
    hm = HookManager()
    hm.register("e", lambda p: {**p, "seq": [*p.get("seq", []), "a"]})
    hm.register("e", lambda p: {**p, "seq": [*p.get("seq", []), "b"]})
    assert hm.run("e", {})["seq"] == ["a", "b"]


def test_handler_returning_none_keeps_prior_payload():
    hm = HookManager()
    hm.register("e", lambda p: None)
    assert hm.run("e", {"k": "v"}) == {"k": "v"}


def test_block_signal_from_before_invoke_handler():
    # corecoder reads payload["blocked"] to abort a tool call
    hm = HookManager()
    hm.register(
        "tool.before_invoke", lambda p: {**p, "blocked": True, "reason": "no"}
    )
    out = hm.run("tool.before_invoke", {"tool_id": "bash", "params": {}})
    assert out["blocked"] is True and out["reason"] == "no"


def test_unregister_removes_handler():
    hm = HookManager()

    def h(p):
        return {**p, "hit": True}

    hm.register("e", h)
    hm.unregister("e", h)
    assert "hit" not in hm.run("e", {})


# -- session.start wiring (skill loader registered as a real handler) ------

def test_session_start_handler_injects_catalog(tmp_path):
    d = tmp_path / "skills" / "demo-pipeline"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: demo-pipeline\ndescription: Demo.\n---\n# Demo\n",
        encoding="utf-8",
    )
    reg = SkillRegistry(skills_dir=tmp_path / "skills")
    hm = HookManager()
    hm.register("session.start", load_skills_into_context)

    ctx = SimpleNamespace(system_prompt_fragments=[])
    hm.run(
        "session.start",
        {
            "context": ctx,
            "agent_type": "coder",
            "tool_names": ["read_skill"],
            "registry": reg,
        },
    )
    assert any("demo-pipeline" in f for f in ctx.system_prompt_fragments)


def test_runner_deps_exposes_hooks_field():
    # corecoder._get_hooks reads ctx.deps.hooks; the field must exist (default None)
    from openagents_orchestration.core.runner import RunnerDeps

    fields = RunnerDeps.__dataclass_fields__
    assert "hooks" in fields

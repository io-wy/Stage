"""Tests for prompt-cache optimizations (system-prompt splitting + tool sorting)."""

from __future__ import annotations

import pytest

from openagents_orchestration.patterns.corecoder import (
    CoreCoderPattern,
    _split_system_prompt,
    _SYSTEM_PROMPT_BOUNDARY,
)
from openagents_orchestration.patterns.director import DirectorPattern


class TestSplitSystemPrompt:
    def test_no_boundary_returns_single_message(self):
        msgs = _split_system_prompt("static content only")
        assert len(msgs) == 1
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "static content only"

    def test_boundary_splits_into_two(self):
        prompt = f"static\n\n{_SYSTEM_PROMPT_BOUNDARY}\n\ndynamic"
        msgs = _split_system_prompt(prompt)
        assert len(msgs) == 2
        assert msgs[0]["content"] == "static"
        assert msgs[1]["content"] == "dynamic"

    def test_empty_dynamic_omitted(self):
        prompt = f"static only\n\n{_SYSTEM_PROMPT_BOUNDARY}\n\n"
        msgs = _split_system_prompt(prompt)
        assert len(msgs) == 1
        assert msgs[0]["content"] == "static only"

    def test_empty_static_omitted(self):
        prompt = f"\n\n{_SYSTEM_PROMPT_BOUNDARY}\n\ndynamic only"
        msgs = _split_system_prompt(prompt)
        assert len(msgs) == 1
        assert msgs[0]["content"] == "dynamic only"


class TestCoreCoderSystemPromptBoundary:
    def test_static_only_no_boundary(self):
        pattern = CoreCoderPattern()
        prompt = pattern.compose_system_prompt("Base prompt")
        assert _SYSTEM_PROMPT_BOUNDARY not in prompt

    def test_with_context_includes_boundary(self, monkeypatch):
        """When system_prompt_fragments are present, boundary is inserted."""
        pattern = CoreCoderPattern()

        # Mock runtime context helpers so we don't need a full SDK context
        from openagents_orchestration import prompts as prompts_mod
        monkeypatch.setattr(prompts_mod, "gather_runtime_context", lambda ctx: {})
        monkeypatch.setattr(prompts_mod, "build_runtime_fragment", lambda **kw: "runtime")

        class FakeCtx:
            system_prompt_fragments = ["fragment1"]
            scratch = {}
            tools = {}

        pattern.context = FakeCtx()
        prompt = pattern.compose_system_prompt("Base")
        assert _SYSTEM_PROMPT_BOUNDARY in prompt
        static, dynamic = prompt.split(_SYSTEM_PROMPT_BOUNDARY, 1)
        assert "Base" in static
        assert "fragment1" in dynamic


class TestDirectorSystemPromptBoundary:
    def test_static_only_no_boundary(self):
        pattern = DirectorPattern()
        prompt = pattern.compose_system_prompt("Director base")
        assert _SYSTEM_PROMPT_BOUNDARY not in prompt

    def test_with_fragments_includes_boundary(self, monkeypatch):
        pattern = DirectorPattern()

        from openagents_orchestration import prompts as prompts_mod
        monkeypatch.setattr(prompts_mod, "gather_runtime_context", lambda ctx: {})
        monkeypatch.setattr(prompts_mod, "build_runtime_fragment", lambda **kw: "runtime")

        class FakeCtx:
            system_prompt_fragments = ["runtime info"]
            scratch = {}
            tools = {}

        pattern.context = FakeCtx()
        prompt = pattern.compose_system_prompt("Base")
        assert _SYSTEM_PROMPT_BOUNDARY in prompt
        static, dynamic = prompt.split(_SYSTEM_PROMPT_BOUNDARY, 1)
        assert "Director" in static
        assert "runtime info" in dynamic


class TestDirectorShouldContinueStep:
    @pytest.mark.asyncio
    async def test_returns_true_when_nothing_special(self):
        """Normal state — keep looping."""
        pattern = DirectorPattern()

        class FakeBudget:
            exhausted = False

        class FakeBoard:
            _final_summary = ""
            budget = FakeBudget()

            def has_actionable(self):
                return True

        class FakeDeps:
            state_board = FakeBoard()

        class FakeCtx:
            deps = FakeDeps()

        pattern.context = FakeCtx()
        assert await pattern._should_continue_step(5) is True

    @pytest.mark.asyncio
    async def test_returns_false_when_finalized(self):
        pattern = DirectorPattern()

        class FakeBoard:
            _final_summary = "done"

        class FakeDeps:
            state_board = FakeBoard()

        class FakeCtx:
            deps = FakeDeps()

        pattern.context = FakeCtx()
        assert await pattern._should_continue_step(5) is False

    @pytest.mark.asyncio
    async def test_returns_false_when_no_actionable_tasks(self):
        pattern = DirectorPattern()

        class FakeBudget:
            exhausted = False

        class FakeBoard:
            _final_summary = ""
            budget = FakeBudget()

            def has_actionable(self):
                return False

        class FakeDeps:
            state_board = FakeBoard()

        class FakeCtx:
            deps = FakeDeps()

        pattern.context = FakeCtx()
        assert await pattern._should_continue_step(5) is False

    @pytest.mark.asyncio
    async def test_returns_false_when_budget_exhausted(self):
        pattern = DirectorPattern()

        class FakeBudget:
            exhausted = True

        class FakeBoard:
            _final_summary = ""
            budget = FakeBudget()

            def has_actionable(self):
                return True

        class FakeDeps:
            state_board = FakeBoard()

        class FakeCtx:
            deps = FakeDeps()

        pattern.context = FakeCtx()
        assert await pattern._should_continue_step(5) is False

    @pytest.mark.asyncio
    async def test_returns_true_when_no_deps_or_board(self):
        """Defensive — if setup is incomplete, keep going."""
        pattern = DirectorPattern()
        assert await pattern._should_continue_step(5) is True

        class FakeCtx:
            deps = None

        pattern.context = FakeCtx()
        assert await pattern._should_continue_step(5) is True


class TestToolSchemaSorting:
    def test_build_tool_schemas_sorted_by_tool_id(self, monkeypatch):
        """Tool schemas must be ordered deterministically for stable cache fingerprints."""
        pattern = CoreCoderPattern()

        class FakeTool:
            description = "a tool"

            def schema(self):
                return {"type": "object"}

        class FakeCtx:
            llm_client = None
            tools = {
                "z_tool": FakeTool(),
                "a_tool": FakeTool(),
                "m_tool": FakeTool(),
            }

        monkeypatch.setattr(type(pattern), "context", property(lambda self: FakeCtx()))
        schemas = pattern._build_tool_schemas()
        names = [s["name"] for s in schemas]
        assert names == ["a_tool", "m_tool", "z_tool"]

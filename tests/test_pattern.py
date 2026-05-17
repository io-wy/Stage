"""Tests for DirectorPattern."""

from __future__ import annotations

import pytest

from openagents_orchestration.patterns.director import DIRECTOR_PRINCIPLES, DirectorPattern


class TestDirectorPattern:
    def test_is_corecoder_subclass(self):
        from openagents_orchestration.patterns.corecoder import CoreCoderPattern
        assert issubclass(DirectorPattern, CoreCoderPattern)

    def test_compose_system_prompt_contains_director_principles(self):
        pattern = DirectorPattern()
        # Need to set up a minimal context for compose_system_prompt
        prompt = pattern.compose_system_prompt("")
        assert "Director" in prompt
        assert "Observe first" in prompt
        assert "Plan in batches" in prompt
        assert "spawn_agent" in prompt

    def test_compose_system_prompt_contains_base_prompt(self):
        pattern = DirectorPattern()
        prompt = pattern.compose_system_prompt("Custom base prompt here")
        assert "Custom base prompt here" in prompt
        assert "Director" in prompt

    def test_director_principles_not_coder_principles(self):
        """Ensure Director does NOT contain CoreCoder's coding-specific principles."""
        prompt = DirectorPattern().compose_system_prompt("")
        # CoreCoder principles mention "read before write" etc.
        # Director should not have these
        assert "Read before you write" not in prompt
        assert "Edit by exact replacement" not in prompt

    def test_director_principles_content(self):
        """Verify key director instructions are present."""
        principles = DIRECTOR_PRINCIPLES
        assert "show_state" in principles
        assert "read_file" in principles
        assert "spawn_agent" in principles
        assert "replan" in principles
        assert "finalize" in principles
        assert "ask_human" in principles
        assert "Delegate, don't do" in principles
        assert "Know when to stop" in principles
        assert "coder" in principles
        assert "reviewer" in principles
        assert "tester" in principles

"""Compatibility shim — re-exports from the centralized prompts package.

Prompts live in src/prompts/ for easy editing and version control.
This module exists so existing imports like::

    from openagents_orchestration.prompts import CORE_PRINCIPLES

continue to work without change.
"""

from __future__ import annotations

from prompts.agent_constraints import CODER_CONSTRAINT, REVIEWER_CONSTRAINT

# Re-export everything the old single-file module provided
from prompts.core import CORE_PRINCIPLES
from prompts.corrections import (
    REPLAN_PROMPT_TEMPLATE,
    build_hallucination_correction,
    build_replan_prompt,
)
from prompts.decompose import build_agents_info, build_decompose_prompt
from prompts.director import DIRECTOR_PRINCIPLES
from prompts.dynamic import build_runtime_fragment, gather_runtime_context

__all__ = [
    "CORE_PRINCIPLES",
    "DIRECTOR_PRINCIPLES",
    "CODER_CONSTRAINT",
    "REVIEWER_CONSTRAINT",
    "REPLAN_PROMPT_TEMPLATE",
    "build_hallucination_correction",
    "build_replan_prompt",
    "build_runtime_fragment",
    "gather_runtime_context",
    "build_decompose_prompt",
    "build_agents_info",
]

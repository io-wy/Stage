"""System prompt composition for the orchestrator.

All prompts are centralized here for easy tuning and version control.
Each submodule groups prompts by role or lifecycle phase.
"""

from __future__ import annotations

from prompts.agent_constraints import (
    CODER_CONSTRAINT,
    REVIEWER_CONSTRAINT,
)
from prompts.core import CORE_PRINCIPLES
from prompts.corrections import (
    REPLAN_PROMPT_TEMPLATE,
    build_hallucination_correction,
    build_replan_prompt,
)
from prompts.director import DIRECTOR_PRINCIPLES
from prompts.dynamic import (
    build_runtime_fragment,
    gather_runtime_context,
)

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
]

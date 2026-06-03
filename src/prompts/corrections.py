"""Retry / correction prompts used when an agent fails or hallucinates.

These are injected dynamically when the orchestrator detects a problem
(e.g. placeholder still present, task failed, etc.).
"""

from __future__ import annotations

import json
from typing import Any


def build_hallucination_correction(art_path: str, content: str) -> str:
    """Build the correction prompt when a coder hallucinates completion."""
    return (
        f"PREVIOUS ATTEMPT FAILED. The file {art_path} still contains "
        f"TODO/placeholder/pass. You MUST use write_file or edit_file "
        f"to replace it with actual implementation.\n\n"
        f"Current content:\n```\n{content}\n```\n\n"
        f"Now write the complete implementation."
    )


REPLAN_PROMPT_TEMPLATE = """\
The following task failed and needs to be broken into smaller sub-tasks.

Original task: {description}
Failure reason: {reason}
Input context: {input_context}

Current plan:
{plan_json}

Please output a JSON array of replacement sub-tasks:
[{{"task_id": "t_new_1", "description": "...",
"input_context": "detailed instructions",
"agent_type": "coder", "expected_artifacts": ["file.py"]}}]

Rules:
1. Each sub-task should be small enough to complete in ~5 minutes
2. agent_type must be one of: coder, reviewer, tester, researcher
3. Include expected output files
4. task_id must be unique (use t_new_1, t_new_2, ...)
5. Output strict JSON only, no markdown fences
"""


def build_replan_prompt(
    description: str,
    reason: str,
    input_context: str,
    plan: list[dict[str, Any]],
) -> str:
    """Render the replan prompt from template."""
    return REPLAN_PROMPT_TEMPLATE.format(
        description=description,
        reason=reason,
        input_context=input_context,
        plan_json=json.dumps(plan, indent=2),
    )

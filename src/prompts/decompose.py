"""Task decomposition prompt for the initial planning phase.

The Director uses this prompt to ask its LLM to break the objective into a
structured task graph before any agents are spawned.
"""

from __future__ import annotations

from typing import Any


def build_agents_info(agents_by_id: dict[str, Any]) -> str:
    """Build the 'available agent types' section for the decompose prompt."""
    lines = []
    for aid, _agent in agents_by_id.items():
        if aid == "director":
            continue
        lines.append(f"- {aid}: tactical agent")
    return "\n".join(lines) or "- coder: writes code"


def build_decompose_prompt(objective: str, agents_info: str) -> str:
    """Build the full decomposition prompt sent to the Director's LLM."""
    return (
        f"Decompose the following objective into a structured task graph.\n\n"
        f"Objective: {objective}\n\n"
        f"Available agent types:\n{agents_info}\n\n"
        f"Rules:\n"
        f"1. Each task has a unique task_id (t1, t2, ...)\n"
        f"2. List dependencies explicitly\n"
        f"3. EACH TASK SHOULD HAVE AT MOST 3-5 expected_artifacts (files). "
        f"If a task needs more than 5 files, SPLIT IT into smaller subtasks.\n"
        f"4. For project scaffold/init tasks, split into granular subtasks: "
        f"   - t1: project structure + config files (pyproject.toml, requirements.txt)\n"
        f"   - t2: core module files (config, database, security)\n"
        f"   - t3: models and schemas\n"
        f"   - t4: API routes and services\n"
        f"5. Keep the graph shallow (2-4 layers)\n"
        f"6. input_context: detailed instructions for the agent\n"
        f"7. coder agents have a step budget of ~30 steps. "
        f"A task creating 10+ files will fail.\n\n"
        f'Output strict JSON: {{"tasks": [...]}}\n'
        f'Each task: {{"task_id": "t1", "description": "...", '
        f'"input_context": "...", "agent_type": "coder", '
        f'"dependencies": [], "expected_artifacts": ["file.py"]}}'
    )

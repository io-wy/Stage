"""Task decomposition prompt for the initial planning phase.

The Director uses this prompt to ask its LLM to break the objective into a
structured task graph before any agents are spawned.
"""

from __future__ import annotations

from typing import Any

_DEFAULT_AGENT_DESCRIPTIONS: dict[str, str] = {
    "coder": "writes code and tests, owns the full edit-verify loop",
    "reviewer": "reviews code for bugs/style/security and runs tests",
    "researcher": "searches the web, reads docs, gathers external knowledge",
    "monitor": "watches orchestration health, detects anomalies, sends alerts",
    "github_agent": "operates on GitHub PRs, issues, CI, and repository state",
    "team_leader": "manages a subgraph of tasks and delegates to workers",
}


def build_agents_info(agents_by_id: dict[str, Any]) -> str:
    """Build the 'available agent types' section for the decompose prompt.

    Uses the agent's ``name`` and ``tools`` when available, falling back to
    sensible defaults so the Director picks the right role for each task.
    """
    lines = []
    for aid, agent in agents_by_id.items():
        if aid == "director":
            continue
        name = getattr(agent, "name", None) or aid
        description = _DEFAULT_AGENT_DESCRIPTIONS.get(aid)
        if description is None:
            tools = getattr(agent, "tools", None) or []
            tool_hint = ", ".join(tools[:5]) if tools else "general purpose"
            description = f"tactical agent; tools: {tool_hint}"
        lines.append(f"- {aid} ({name}): {description}")
    return "\n".join(lines) or "- coder: writes code and tests"


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
        f"7. Match agent_type to the available roles above. "
        f"A task creating 10+ files or mixing many concerns will fail — split it.\n\n"
        f'Output strict JSON: {{"tasks": [...]}}\n'
        f'Each task: {{"task_id": "t1", "description": "...", '
        f'"input_context": "...", "agent_type": "coder", '
        f'"dependencies": [], "expected_artifacts": ["file.py"]}}'
    )

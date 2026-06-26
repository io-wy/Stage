"""Resident agent prompt templates — extracted from OrchestratorRunner.

Separating prompt templates from orchestration logic keeps runner.py focused
on control flow and makes prompt engineering independently reviewable.
"""

from __future__ import annotations

from collections.abc import Callable

PromptFactory = Callable[..., str]


def producer_initial_prompt(
    task_id: str,
    description: str,
    expected_artifacts: list[str],
    checker_id: str,
    *,
    producer_label: str = "producer",
    checker_label: str = "checker",
) -> str:
    """Prompt for a generic producer starting work on a collaborative task."""
    artifacts_str = ", ".join(expected_artifacts) if expected_artifacts else "(none specified)"
    return (
        f"You are assigned to task {task_id}.\n\n"
        f"Description: {description}\n"
        f"Expected artifacts: {artifacts_str}\n\n"
        "CRITICAL RULES:\n"
        "- Produce the required deliverables for this task.\n"
        "- Use the tools available to your role; do not invent completed work.\n"
        "- Run any appropriate validation after changes or before handoff.\n"
        "- If validation fails, READ the error output and FIX the issue. Keep iterating until validation passes.\n"
        f"- ONLY when the deliverables are ready, use send_message with EXACTLY to_agent='{checker_id}' and message starting with:\n"
        f"   'TASK_REVIEW_READY[{task_id}]: tests passed = N'\n"
        f"- NEVER send completion messages to 'director'. Always send to '{checker_id}'.\n"
        "- Do NOT check your mailbox until you have completed the current production work.\n\n"
        f"Do not declare the task complete until the {checker_label} approves."
    )


def checker_initial_prompt(
    task_id: str,
    description: str,
    producer_id: str,
    thread_id: str,
    iteration_history: str,
    *,
    producer_label: str = "producer",
    checker_label: str = "checker",
) -> str:
    """Prompt for a generic checker inspecting a producer's work."""
    return (
        f"Please review the output for task {task_id}.\n\n"
        f"Description: {description}\n\n"
        f"CRITICAL: You must use `send_message` to communicate directly with the {producer_label}.\n"
        "Do NOT just return a report.\n\n"
        "You must verify ACTUAL deliverables before approving.\n"
        f"If the {producer_label} only sent analysis, documentation, or excuses without the requested deliverables, REJECT with fix_needed.\n\n"
        f"If you APPROVE, send_message with EXACTLY to_agent='{producer_id}' and message starting with:\n"
        f"  'TASK_APPROVED[{task_id}]: LGTM'\n\n"
        f"If you find issues, send_message with EXACTLY to_agent='{producer_id}' and message starting with:\n"
        f"  'TASK_FIX_NEEDED[{task_id}]: ' followed by specific fix instructions.\n\n"
        "Instructions:\n"
        f"1. Inspect the deliverables produced by the {producer_label}\n"
        "2. Check for correctness, completeness, and edge cases\n"
        "3. Run any necessary verification available to your role\n"
        f"4. Use send_message(to_agent='{producer_id}', message='TASK_APPROVED[...]: ...') to approve\n"
        f"5. Use send_message(to_agent='{producer_id}', message='TASK_FIX_NEEDED[...]: ...') for issues\n\n"
        f"Thread: {thread_id}\n"
        f"Iteration history: {iteration_history}"
    )


def producer_fix_prompt(
    task_id: str,
    description: str,
    feedback: str,
    iteration_history_json: str,
    *,
    checker_label: str = "checker",
) -> str:
    """Prompt to send to a producer when a checker requests fixes."""
    return (
        f"{checker_label.title()} feedback:\n{feedback}\n\n"
        "Please fix the issues, re-run appropriate validation, and send TASK_REVIEW_READY again when ready.\n\n"
        f"Recent iteration history: {iteration_history_json}"
    )


def coder_initial_prompt(
    task_id: str,
    description: str,
    expected_artifacts: list[str],
    reviewer_id: str,
) -> str:
    """Prompt for a resident Coder starting work on a task."""
    artifacts_str = ", ".join(expected_artifacts) if expected_artifacts else "(none specified)"
    raw = (
        f"You are assigned to task {task_id}.\n\n"
        f"Description: {description}\n"
        f"Expected artifacts: {artifacts_str}\n\n"
        "CRITICAL RULES:\n"
        "- If the target directory does not exist, CREATE it first using `bash` (mkdir -p).\n"
        "- Do NOT look for existing code elsewhere. Implement from scratch in the specified work directory.\n"
        "- Do NOT write READMEs, design docs, or analysis reports as the main deliverable. Code and tests are the deliverables.\n"
        "- You MUST create the source file(s) AND the pytest test file(s) required by the task.\n"
        "- Run tests with `pytest` after EVERY code change.\n"
        "- If tests fail, READ the error output and FIX the code. Keep iterating until tests pass.\n"
        "- ONLY when ALL tests pass AND tests ran (N > 0), use send_message with EXACTLY to_agent='{reviewer_id}' and message starting with:\n"
        f"   'TASK_REVIEW_READY[{task_id}]: tests passed = N'\n"
        "- NEVER send completion messages to 'director'. Always send to '{reviewer_id}'.\n"
        "- Do NOT check your mailbox until you have completed the current implementation work.\n\n"
        "Do not declare the task complete until tests pass and the reviewer approves."
    )
    return raw.replace("{reviewer_id}", reviewer_id)


def reviewer_initial_prompt(
    task_id: str,
    description: str,
    coder_id: str,
    thread_id: str,
    iteration_history: str,
) -> str:
    """Prompt for a resident Reviewer inspecting a coder's work."""
    return (
        f"Please review the implementation of task {task_id}.\n\n"
        f"Description: {description}\n\n"
        "CRITICAL: You must use `send_message` to communicate directly with the coder.\n"
        "Do NOT just return a report.\n\n"
        "You must verify ACTUAL code files and passing tests before approving.\n"
        "If the coder only sent analysis, documentation, or excuses without source code, REJECT with fix_needed.\n\n"
        f"If you APPROVE, send_message with EXACTLY to_agent='{coder_id}' and message starting with:\n"
        f"  'TASK_APPROVED[{task_id}]: LGTM'\n\n"
        f"If you find issues, send_message with EXACTLY to_agent='{coder_id}' and message starting with:\n"
        f"  'TASK_FIX_NEEDED[{task_id}]: ' followed by specific file/line and fix instructions.\n\n"
        "Instructions:\n"
        "1. Read the code files produced by the coder\n"
        "2. Check for correctness, style, and edge cases\n"
        "3. Run tests to verify (pytest ...)\n"
        f"4. Use send_message(to_agent='{coder_id}', message='TASK_APPROVED[...]: ...') to approve\n"
        f"5. Use send_message(to_agent='{coder_id}', message='TASK_FIX_NEEDED[...]: ...') for issues"
    )


def coder_fix_prompt(
    task_id: str,
    description: str,
    feedback: str,
    iteration_history_json: str,
) -> str:
    """Prompt to send to a resident Coder when a reviewer requests fixes."""
    return (
        f"Reviewer feedback:\n{feedback}\n\n"
        "Please fix the issues and run tests again."
    )


PROMPT_REGISTRY: dict[tuple[str, str], PromptFactory] = {
    ("producer_checker", "producer"): producer_initial_prompt,
    ("producer_checker", "checker"): checker_initial_prompt,
    ("producer_checker", "producer_fix"): producer_fix_prompt,
    ("coder_reviewer", "producer"): coder_initial_prompt,
    ("coder_reviewer", "checker"): reviewer_initial_prompt,
    ("coder_reviewer", "producer_fix"): coder_fix_prompt,
}


def get_prompt(pattern: str, role: str) -> PromptFactory:
    """Look up a prompt factory for a collaboration pattern role."""
    factory = PROMPT_REGISTRY.get((pattern, role))
    if factory is not None:
        return factory
    fallback = PROMPT_REGISTRY.get(("producer_checker", role))
    if fallback is not None:
        return fallback
    raise KeyError(f"No prompt registered for pattern={pattern!r}, role={role!r}")

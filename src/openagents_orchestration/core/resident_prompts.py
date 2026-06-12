"""Resident agent prompt templates — extracted from OrchestratorRunner.

Separating prompt templates from orchestration logic keeps runner.py focused
on control flow and makes prompt engineering independently reviewable.
"""

from __future__ import annotations


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
        "- Run tests with `pytest` after EVERY code change.\n"
        "- If tests fail, READ the error output and FIX the code. Keep iterating until tests pass.\n"
        "- ONLY when ALL tests pass, use send_message with EXACTLY to_agent='{reviewer_id}' and message starting with:\n"
        f"   'TASK_REVIEW_READY[{task_id}]: tests passed = N'\n"
        "- NEVER send completion messages to 'director'. Always send to '{reviewer_id}'.\n\n"
        "Do not declare the task complete until tests pass and the reviewer approves."
    )
    # Non-f-string placeholders: {reviewer_id} appears in regular strings above
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

"""Structured helpers for collaborative task-transition messages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class CollaborationSignal(StrEnum):
    REVIEW_READY = "review_ready"
    APPROVED = "approved"
    FIX_NEEDED = "fix_needed"


_SIGNAL_PREFIXES = {
    "TASK_REVIEW_READY": CollaborationSignal.REVIEW_READY,
    "TASK_APPROVED": CollaborationSignal.APPROVED,
    "TASK_FIX_NEEDED": CollaborationSignal.FIX_NEEDED,
}
# Anchor to start-of-string to prevent accidental matching of signal keywords
# in agent code output or free-form text.  Collaboration signals MUST be
# the first thing in the message body.
_SIGNAL_RE = re.compile(r"^\s*(TASK_REVIEW_READY|TASK_APPROVED|TASK_FIX_NEEDED)(?:\[([^\]]+)\])?:?\s*(.*)", re.DOTALL)
_PASSED_RE = re.compile(r"(?:tests\s+passed\s*=\s*(\d+)|(\d+)\s+passed)", re.IGNORECASE)


@dataclass(frozen=True)
class CollaborationMessage:
    signal: CollaborationSignal
    task_id: str
    body: str
    tests_passed: int = 0


def parse_collaboration_message(content: str, *, default_task_id: str = "") -> CollaborationMessage | None:
    """Parse a task collaboration transition marker from message content."""
    match = _SIGNAL_RE.search((content or "").strip())
    if match is None:
        return None

    raw_signal, explicit_task_id, body = match.groups()
    body = body.strip()
    passed_match = _PASSED_RE.search(body)
    return CollaborationMessage(
        signal=_SIGNAL_PREFIXES[raw_signal],
        task_id=(explicit_task_id or default_task_id).strip(),
        body=body,
        tests_passed=int(next(g for g in passed_match.groups() if g)) if passed_match else 0,
    )


def task_id_from_resident_id(agent_id: str) -> str:
    """Return the task id encoded in a resident id of form ``agent_type-task_id``."""
    if "-" not in agent_id:
        return ""
    return agent_id.split("-", 1)[1]

"""FailureHooks — graded failure handling for tool and agent failures.

This module implements the five-level failure grading used inside
``CoreCoderPattern`` (see docs/docs-proj/2026-06-21-pattern-centric-orchestration.md
§7.1). The grading is intentionally a hook so that callers can override or
extend the decision without changing Pattern code.
"""

from __future__ import annotations

from typing import Any

from openagents_orchestration.models.pattern import FailureDecision, FailureGrade


class FailureHooks:
    """Default failure-grading handler invoked by ``tool.failure`` events."""

    def tool_failure(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Grade a tool failure and return a ``FailureDecision``.

        Payload keys:
        - ``tool_id``: str
        - ``error``: str
        - ``exception``: BaseException | None
        - ``consecutive_failures``: int
        - ``step``: int
        """
        tool_id = payload.get("tool_id")
        error = str(payload.get("error", ""))
        exception = payload.get("exception")
        consecutive = int(payload.get("consecutive_failures", 0))

        grade = self._classify(error, exception)

        decision: FailureDecision
        if grade == FailureGrade.TRANSIENT:
            decision = FailureDecision(
                action="retry",
                reason=f"transient failure on {tool_id}: {error}",
                delay=1.5 * (consecutive + 1),
                tool_id=tool_id,
                max_retries=3,
            )
        elif grade == FailureGrade.RECOVERABLE:
            decision = FailureDecision(
                action="escalate",
                reason=f"recoverable failure on {tool_id}: {error}",
                tool_id=tool_id,
            )
        elif grade == FailureGrade.TOOL_FATAL:
            decision = FailureDecision(
                action="disable_tool",
                reason=f"tool fatal failure on {tool_id}: {error}",
                tool_id=tool_id,
            )
        elif grade == FailureGrade.AGENT_FATAL:
            decision = FailureDecision(
                action="abort_agent",
                reason=f"agent fatal failure on {tool_id}: {error}",
                tool_id=tool_id,
            )
        else:  # ORCHESTRATION_FATAL
            decision = FailureDecision(
                action="abort_run",
                reason=f"orchestration fatal failure on {tool_id}: {error}",
                tool_id=tool_id,
            )

        payload["grade"] = grade
        payload["decision"] = decision
        return payload

    @staticmethod
    def _classify(error: str, exception: BaseException | None) -> FailureGrade:
        """Classify an error string/exception into a failure grade."""
        error_lower = error.lower()
        transient_markers = ("429", "timeout", "connection reset", "temporarily unavailable", "rate limit")
        if any(m in error_lower for m in transient_markers):
            return FailureGrade.TRANSIENT

        if exception is not None:
            exc_name = type(exception).__name__
            if exc_name in ("ModelRetryError", "TimeoutError"):
                return FailureGrade.TRANSIENT
            if exc_name in ("PermissionError", "PermanentToolError"):
                return FailureGrade.TOOL_FATAL
            if exc_name in ("ConfigError",):
                return FailureGrade.AGENT_FATAL

        recoverable_markers = ("file not found", "old_string mismatch", "bad params", "not found")
        if any(m in error_lower for m in recoverable_markers):
            return FailureGrade.RECOVERABLE

        return FailureGrade.RECOVERABLE

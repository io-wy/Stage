"""Pattern runtime outcome models.

This module defines the minimal, explicit return value of ``Pattern.execute()``
and the failure-grading primitives used by CoreCoderPattern to decide how to
react to tool and agent failures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

try:
    from openagents.interfaces.runtime import RunUsage
except Exception:  # pragma: no cover - compatibility with older SDK shapes
    RunUsage = Any  # type: ignore[misc,assignment]


class PatternOutcomeStatus(StrEnum):
    """Terminal status returned by a Pattern execution."""

    COMPLETED = "completed"
    MAX_STEPS = "max_steps"
    FAILED = "failed"
    AWAITING_HUMAN = "awaiting_human"
    TERMINATED = "terminated"


class FailureGrade(StrEnum):
    """Five-level failure classification used inside CoreCoderPattern.

    See docs/docs-proj/2026-06-21-pattern-centric-orchestration.md §7.1.
    """

    TRANSIENT = "transient"
    RECOVERABLE = "recoverable"
    TOOL_FATAL = "tool_fatal"
    AGENT_FATAL = "agent_fatal"
    ORCHESTRATION_FATAL = "orchestration_fatal"


@dataclass
class PatternError:
    """Structured error carried by ``PatternOutcome``.

    The ``grade`` tells the caller (Runner / Director) whether the failure is
    local and retryable, or whether it should escalate to a higher-level
    decision.
    """

    message: str
    grade: FailureGrade = FailureGrade.RECOVERABLE
    exception: BaseException | None = None
    tool_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "grade": self.grade.value,
            "tool_id": self.tool_id,
            "details": dict(self.details),
        }


@dataclass
class FailureDecision:
    """Decision produced by CoreCoderPattern's failure classifier."""

    action: str  # retry | escalate | disable_tool | abort_agent | abort_run
    reason: str
    delay: float | None = None
    tool_id: str | None = None
    max_retries: int = 3


@dataclass
class PatternOutcome:
    """Minimal return value of ``Pattern.execute()``.

    Artifacts and verification status are intentionally **not** carried here;
    they flow through dedicated hooks (``artifact.claimed``,
    ``artifact.verified``) so that ``StateBoard`` is updated as soon as the
    agent creates or verifies a file, rather than waiting until the agent
    finishes.

    ``Runner`` uses ``PatternOutcome`` to decide the high-level result of an
    agent run; ``StateBoard.apply_outcome()`` is invoked via the
    ``pattern.after_execute`` hook.
    """

    output: str = ""
    status: PatternOutcomeStatus = PatternOutcomeStatus.COMPLETED
    usage: Any = None  # RunUsage when available
    error: PatternError | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            PatternOutcomeStatus.COMPLETED,
            PatternOutcomeStatus.FAILED,
            PatternOutcomeStatus.MAX_STEPS,
            PatternOutcomeStatus.TERMINATED,
        }

    @property
    def is_success(self) -> bool:
        return self.status == PatternOutcomeStatus.COMPLETED and self.error is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "output": self.output,
            "status": self.status.value,
            "usage": _usage_to_dict(self.usage),
            "error": self.error.to_dict() if self.error else None,
            "metadata": dict(self.metadata),
        }


def _usage_to_dict(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    if hasattr(usage, "to_dict"):
        return usage.to_dict()
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    return {
        "total_tokens": getattr(usage, "total_tokens", None),
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
    }

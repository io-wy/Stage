"""Hook pipeline for CoreCoder + StateBoard + session lifecycle.

Hooks are the primary glue between Pattern and StateBoard. Pattern code emits
events; registered handlers translate those events into StateBoard mutations.
This keeps StateBoard a passive single source of truth while letting Patterns
stay orchestration-agnostic.

Core events:

**Pattern lifecycle**
- ``pattern.before_setup``: before pattern.setup() runs.
- ``pattern.after_setup``: after pattern.setup() runs.
- ``pattern.before_llm``: inspect/modify messages before the LLM call.
- ``pattern.after_llm``: inspect/modify the LLM response.
- ``pattern.before_tool``: before a tool call is dispatched.
- ``pattern.after_tool``: after a tool result is produced.
- ``pattern.before_step``: at the start of each ReAct step.
- ``pattern.after_step``: at the end of each ReAct step.
- ``pattern.completed``: pattern finished successfully.
- ``pattern.failed``: pattern finished with an error.
- ``pattern.after_execute``: final outcome is available; used to apply
  ``PatternOutcome`` to ``StateBoard``.

**Tool interception**
- ``tool.before_invoke``: intercept/modify tool params or block execution.
- ``tool.after_invoke``: inspect/modify tool result.
- ``tool.failure``: a tool call failed and produced a graded error.

**LLM lifecycle**
- ``llm.before_call``: inspect/modify messages/model before generation.
- ``llm.after_call``: usage/metrics after generation.

**StateBoard mutations**
- ``artifact.claimed``: agent claims it produced files.
- ``artifact.verified``: agent verified (or failed to verify) a file.
- ``stateboard.task.update``: task fields changed.
- ``stateboard.agent.update``: agent fields changed.
- ``stateboard.event.log``: event logged.
- ``state.transition``: task/agent state transitioned.

**Agent lifecycle**
- ``agent.registered``: agent registered with the StateBoard.
- ``agent.completed``: agent finished successfully.
- ``agent.failed``: agent finished with an error.

**Director advice**
- ``director.advise``: request strategic hints from the advisory layer.

**Budget**
- ``budget.exhausted``: token/step/time budget exhausted.

**Session lifecycle**
- ``session.start``: session begins; loaders can inject context.

Hooks are callables registered on ``HookManager``. They receive an event dict
and can return ``None`` (no change) or a modified payload dict. Returning
``{"blocked": True, "reason": "..."}`` from ``tool.before_invoke`` or
``pattern.before_llm`` prevents the action from running.
"""

from __future__ import annotations

# ruff: noqa: E402 — 末尾的子模块 import 故意放 HookManager/HookEvent 定义之后，避免循环依赖
import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from openagents_orchestration.hooks.skill_loader import (
    load_skills_into_context,
    should_load_skills,
)

HookHandler = Callable[[dict[str, Any]], dict[str, Any] | None]


class HookEvent:
    """Canonical hook event names."""

    # Pattern lifecycle
    PATTERN_BEFORE_SETUP = "pattern.before_setup"
    PATTERN_AFTER_SETUP = "pattern.after_setup"
    PATTERN_BEFORE_LLM = "pattern.before_llm"
    PATTERN_AFTER_LLM = "pattern.after_llm"
    PATTERN_BEFORE_TOOL = "pattern.before_tool"
    PATTERN_AFTER_TOOL = "pattern.after_tool"
    PATTERN_BEFORE_STEP = "pattern.before_step"
    PATTERN_AFTER_STEP = "pattern.after_step"
    PATTERN_COMPLETED = "pattern.completed"
    PATTERN_FAILED = "pattern.failed"
    PATTERN_AFTER_EXECUTE = "pattern.after_execute"

    # Tool interception
    TOOL_BEFORE_INVOKE = "tool.before_invoke"
    TOOL_AFTER_INVOKE = "tool.after_invoke"
    TOOL_FAILURE = "tool.failure"

    # LLM lifecycle
    LLM_BEFORE_CALL = "llm.before_call"
    LLM_AFTER_CALL = "llm.after_call"

    # StateBoard mutations
    ARTIFACT_CLAIMED = "artifact.claimed"
    ARTIFACT_VERIFIED = "artifact.verified"
    STATEBOARD_TASK_UPDATE = "stateboard.task.update"
    STATEBOARD_AGENT_UPDATE = "stateboard.agent.update"
    STATEBOARD_EVENT_LOG = "stateboard.event.log"
    STATE_TRANSITION = "state.transition"

    # Agent lifecycle
    AGENT_REGISTERED = "agent.registered"
    AGENT_COMPLETED = "agent.completed"
    AGENT_FAILED = "agent.failed"

    # Director advice
    DIRECTOR_ADVISE = "director.advise"

    # Budget
    BUDGET_EXHAUSTED = "budget.exhausted"

    # Session lifecycle
    SESSION_START = "session.start"


@dataclass
class HookManager:
    """Simple registry for hook handlers grouped by event name."""

    handlers: dict[str, list[HookHandler]] = field(default_factory=dict)

    def register(self, event: str, handler: HookHandler) -> None:
        """Register a handler for an event."""
        self.handlers.setdefault(event, []).append(handler)

    def unregister(self, event: str, handler: HookHandler) -> None:
        """Remove a handler from an event."""
        if event in self.handlers:
            self.handlers[event] = [
                h for h in self.handlers[event] if h is not handler
            ]

    def has_handlers(self, event: str) -> bool:
        """Return True if any handler is registered for ``event``."""
        return bool(self.handlers.get(event))

    def run(self, event: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Run all handlers for an event, passing the payload through.

        Handlers may mutate the payload dict or return a new dict. The final
        dict is returned.
        """
        current = payload
        for handler in self.handlers.get(event, []):
            result = handler(current)
            if isinstance(result, dict):
                current = result
        return current

    async def arun(self, event: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Async variant of ``run``: await coroutine handlers, call sync ones directly.

        Lets async handlers (e.g. verify, which spawns a verifier agent) register on
        the same events; existing sync handlers keep working unchanged.
        """
        current = payload
        for handler in self.handlers.get(event, []):
            result = handler(current)
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, dict):
                current = result
        return current

    def is_blocked(self, event: str, payload: dict[str, Any]) -> tuple[bool, str]:
        """Run handlers and check whether the action should be blocked.

        Returns ``(blocked, reason)``. A handler can block by returning
        ``{"blocked": True, "reason": "..."}`` or by mutating the payload to
        set ``blocked=True``.
        """
        result = self.run(event, payload)
        if not isinstance(result, dict):
            return False, ""
        blocked = bool(result.get("blocked"))
        reason = str(result.get("reason", ""))
        return blocked, reason


from openagents_orchestration.hooks.continuation import ContinuationHooks
from openagents_orchestration.hooks.failure import FailureHooks
from openagents_orchestration.hooks.state_sync import StateSyncHooks
from openagents_orchestration.hooks.strategy import StrategyAdvisor, StrategyHooks
from openagents_orchestration.hooks.verify import VerifyHooks

__all__ = [
    "HookEvent",
    "HookManager",
    "HookHandler",
    "StateSyncHooks",
    "StrategyHooks",
    "StrategyAdvisor",
    "ContinuationHooks",
    "FailureHooks",
    "VerifyHooks",
    "load_skills_into_context",
    "should_load_skills",
]

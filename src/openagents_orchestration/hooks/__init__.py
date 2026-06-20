"""Hook pipeline for CoreCoder + session-lifecycle loaders.

Claude Code has 27 hook events; we start with the three most impactful:

- ``tool.before_invoke``: intercept/modify tool params or block execution.
- ``tool.after_invoke``: inspect/modify tool result.
- ``pattern.before_llm``: inspect/modify messages before the LLM call.

Hooks are callables registered on ``HookManager``. They receive an event dict
and can return ``None`` (no change) or a modified payload dict. Returning
``{"blocked": True, "reason": "..."}`` from ``tool.before_invoke`` prevents the
tool from running.

This package also hosts **session-lifecycle loaders** (see ``skill_loader``)
that are *not yet* wired through ``HookManager``: today the runner calls them
directly at session start. Step two will register them as ``session.start``
handlers so the trigger goes through ``HookManager`` instead.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from openagents_orchestration.hooks.skill_loader import (
    load_skills_into_context,
    should_load_skills,
)

HookHandler = Callable[[dict[str, Any]], dict[str, Any] | None]


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


__all__ = [
    "HookManager",
    "HookHandler",
    "load_skills_into_context",
    "should_load_skills",
]

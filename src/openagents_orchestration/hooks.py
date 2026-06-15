"""Minimal hook pipeline for CoreCoder.

Claude Code has 27 hook events; we start with the three most impactful:

- `tool.before_invoke`: intercept/modify tool params or block execution.
- `tool.after_invoke`: inspect/modify tool result.
- `pattern.before_llm`: inspect/modify messages before the LLM call.

Hooks are callables registered on `HookManager`. They receive an event dict
and can return `None` (no change) or a modified payload dict. Returning
`{"blocked": True, "reason": "..."}` from `tool.before_invoke` prevents the
tool from running.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

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
            self.handlers[event] = [h for h in self.handlers[event] if h is not handler]

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

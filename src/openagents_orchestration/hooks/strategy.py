"""StrategyHooks — strategic hints for the Director.

Per the pattern-centric architecture, strategic advice is intentionally
separated from ``StateBoard``. These hooks provide a stable interface so that
``DirectorPattern`` can request hints without reaching into the StateBoard's
advisory methods.

NOTE: io-wy has deprioritized the advisory logic for now; this module is a
thin placeholder that returns empty hints so that ``director.advise`` hook
callers have something to run.
"""

from __future__ import annotations

from typing import Any


class StrategyAdvisor:
    """Placeholder strategic advisor.

    The original design expected this to encapsulate ``suggest_fallback``,
    ``strategy_signals``, and ``suggest_tools`` logic moved out of
    ``StateBoard``. Until that logic is needed, it returns an empty hint dict.
    """

    def __init__(self, board: Any | None = None) -> None:
        self.board = board

    def suggest(self) -> dict[str, Any]:
        """Return strategic hints for the Director."""
        return {}


class StrategyHooks:
    """Hook handlers that provide strategic advice to the Director."""

    def __init__(self, board: Any | None = None) -> None:
        self.board = board

    def director_advise(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return strategic hints; payload["board"] takes precedence."""
        board = payload.get("board", self.board)
        advisor = StrategyAdvisor(board)
        payload["hints"] = advisor.suggest()
        return payload

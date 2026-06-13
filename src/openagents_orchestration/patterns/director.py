"""DirectorPattern — extends CoreCoderPattern with orchestrator-specific prompts."""

from __future__ import annotations

import os

from openagents_orchestration.patterns.corecoder import CoreCoderPattern
from prompts.director import DIRECTOR_PRINCIPLES, DIRECTOR_PRINCIPLES_COMPACT


def _select_director_principles() -> str:
    """Select Director system prompt variant.

    - Default: full principles for complex multi-agent scenarios.
    - ``XITAI_DIRECTOR_PROMPT=compact``: compressed variant for providers with
      smaller request-size limits.
    """
    variant = os.environ.get("XITAI_DIRECTOR_PROMPT", "full").lower().strip()
    if variant == "compact":
        return DIRECTOR_PRINCIPLES_COMPACT
    return DIRECTOR_PRINCIPLES


class DirectorPattern(CoreCoderPattern):
    """CoreCoderPattern with Director-specific system prompt and lifecycle hook."""

    _PRINCIPLES = _select_director_principles()

    async def _should_continue_step(self, step: int) -> bool:
        """Director stops looping when the objective is achieved or budget is gone."""
        ctx = self.context
        if ctx is None or ctx.deps is None:
            return True
        board = getattr(ctx.deps, "state_board", None)
        if board is None:
            return True
        # finalize called — objective is done
        if board._final_summary:
            return False
        # Global budget exhausted
        if board.budget.exhausted:
            return False
        # All tasks are terminal but finalize not called yet — give Director
        # a few extra rounds to call finalize before forcing auto-finalize.
        if board.all_terminal():
            terminal_step = ctx.state.get("__terminal_since_step__")
            if terminal_step is None:
                ctx.state["__terminal_since_step__"] = step
                return True
            return step - terminal_step < 4
        # Reset terminal tracker when tasks are still in progress
        ctx.state.pop("__terminal_since_step__", None)
        # Nothing left to do
        return board.has_actionable()

    async def _should_accept_text_response(self, text: str) -> bool:
        """Director must call a tool on every turn.

        Only accept text when finalize has already been called.
        """
        import sys
        ctx = self.context
        if ctx is None or ctx.deps is None:
            return True
        board = getattr(ctx.deps, "state_board", None)
        if board is None:
            return True
        accepted = bool(board._final_summary)
        print(f"[HOOK] _should_accept_text_response: accepted={accepted}, text_preview={text[:80]!r}", file=sys.stderr, flush=True)
        return accepted

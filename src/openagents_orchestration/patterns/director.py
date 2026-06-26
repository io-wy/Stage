"""DirectorPattern — orchestration brain built on CoreCoderPattern.

The Director is a ReAct agent with a Director-specific toolset and prompt.
Inside its loop it can:

- observe via ``show_state``, ``read_file``, ``classify_intent``, etc.
- act via ``spawn_agent``, ``replan``, ``ask_human``, ``finalize``
- perform work directly via ``write_file``, ``edit_file``, ``bash``, etc.

There is no hard-coded orchestration state machine here. The Director's
"brain" is the LLM + its system prompt + its tools.
"""

from __future__ import annotations

from typing import Any

from openagents_orchestration.patterns.corecoder import CoreCoderPattern
from prompts.roles.director import select_director_principles


class DirectorPattern(CoreCoderPattern):
    """CoreCoderPattern configured for project-level orchestration.

    Prompt source priority: declarative ``prompts`` from ``agents/director.json``
    (compiled into ``pattern.config["prompts"]`` and resolved by
    ``CoreCoderPattern._resolve_prompts``). The ``_PRINCIPLES`` class attribute is
    kept as a fallback when no declarative prompts are provided. Both originate
    from ``prompts.roles.director`` so they cannot diverge.
    """

    _PRINCIPLES = select_director_principles()
    _PRINCIPLES_IS_BASE = False

    async def execute(self) -> Any:
        """Run the Director ReAct loop.

        The Director decides inside the loop when to classify intent, decompose
        the objective, spawn agents, perform work directly, or finalize. The
        Runner only provides the StateBoard and toolset; all scheduling decisions
        live in the LLM/tool loop.
        """
        return await super().execute()

    async def _should_continue_step(self, step: int) -> bool:
        """Director stops looping when the objective is achieved or budget is gone."""
        ctx = self.context
        if ctx is None or ctx.deps is None:
            return True
        board = getattr(ctx.deps, "state_board", None)
        if board is None:
            return True
        if board._final_summary:
            return False
        if board.budget.exhausted:
            return False
        if board.all_terminal():
            terminal_step = ctx.state.get("__terminal_since_step__")
            if terminal_step is None:
                ctx.state["__terminal_since_step__"] = step
                return True
            return step - terminal_step < 4
        ctx.state.pop("__terminal_since_step__", None)
        return board.has_actionable()

    async def _should_accept_text_response(self, text: str) -> bool:
        """Director must call a tool on every turn; only accept text after finalize."""
        ctx = self.context
        if ctx is None or ctx.deps is None:
            return True
        board = getattr(ctx.deps, "state_board", None)
        if board is None:
            return True
        return bool(board._final_summary)

"""DirectorPattern — extends CoreCoderPattern with orchestrator-specific prompts."""

from __future__ import annotations

from openagents_orchestration.patterns.corecoder import CoreCoderPattern
from prompts.roles.director import select_director_principles


class DirectorPattern(CoreCoderPattern):
    """CoreCoderPattern with Director-specific system prompt and lifecycle hook.

    Prompt 来源优先级：``agents/director.json`` 的声明式 ``prompts``（编译进
    ``pattern.config["prompts"]``，由 ``CoreCoderPattern._resolve_prompts`` 解析）。
    ``_PRINCIPLES`` 类属性保留为**兜底**——prompts 未声明时仍有 Director 指引。
    两者同源（都来自 ``prompts.roles.director``），不会分叉。
    """

    _PRINCIPLES = select_director_principles()
    # PRINCIPLES 是完整 Director 指引，已由 agents/director.json 的声明式 prompts
    # 注入；类属性仅作兜底，故非「底座」——避免与角色 prompt 双重注入。
    _PRINCIPLES_IS_BASE = False

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

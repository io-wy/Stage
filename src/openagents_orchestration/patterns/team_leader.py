"""TeamLeaderPattern — extends DirectorPattern with team-scoped orchestration.

A TeamLeader manages a subset of tasks (a subgraph) within a Project.
It delegates to Workers, monitors their progress, and reports back to the
GlobalDirector.  It does NOT escalate to humans directly; human issues are
bubbled up to the GlobalDirector.
"""

from __future__ import annotations

from openagents_orchestration.patterns.director import DirectorPattern
from prompts.roles.director import select_director_principles
from prompts.roles.team_leader import RULES as _TEAM_LEADER_RULES


class TeamLeaderPattern(DirectorPattern):
    """DirectorPattern scoped to a single Team's subgraph.

    Differences from Global Director:
    - max_steps defaults to 30 (vs 100)
    - Cannot call ``ask_human``; escalations go to GlobalDirector via send_message
    - show_state returns the *team* snapshot, not the global one

    Prompt 同 DirectorPattern：声明式 ``prompts`` 优先（director PRINCIPLES +
    team_leader RULES 拼装），``_PRINCIPLES`` 类属性为兜底，两者同源。
    """

    _PRINCIPLES = select_director_principles() + "\n\n" + _TEAM_LEADER_RULES

    async def _should_continue_step(self, step: int) -> bool:
        """Stop when the team subgraph is complete or budget is gone."""
        ctx = self.context
        if ctx is None or ctx.deps is None:
            return True
        board = getattr(ctx.deps, "state_board", None)
        if board is None:
            return True
        # Team done when all tasks in the subgraph are terminal
        if hasattr(board, "all_terminal") and board.all_terminal():
            return step < 4  # give a few extra rounds to report back
        if board.budget.exhausted:
            return False
        return board.has_actionable()

    async def _should_accept_text_response(self, text: str) -> bool:
        """TeamLeader must call a tool on every turn (same as Director)."""
        ctx = self.context
        if ctx is None or ctx.deps is None:
            return True
        board = getattr(ctx.deps, "state_board", None)
        if board is None:
            return True
        return bool(getattr(board, "_final_summary", None))

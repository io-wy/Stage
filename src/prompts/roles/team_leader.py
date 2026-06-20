"""Team Leader 角色 prompt 积木 —— 团队层调度的附加规则。

team_leader 的 system prompt = director PRINCIPLES + 本模块 RULES（积木拼装的
示范：``agents/team_leader.json`` 的 prompts 引用两个符号按序拼接）。RULES 是
TeamLeader 区别于 GlobalDirector 的部分，由 ``TeamLeaderPattern`` 与声明式
prompts 共同引用（单一信源）。
"""

from __future__ import annotations

# agents/team_leader.json -> "prompts.roles.team_leader:RULES"
RULES = """\
## Team Leader Rules
1. You manage ONLY the tasks in your assigned subgraph.
2. Delegate work to Workers (coder, reviewer, researcher).
3. NEVER call ask_human — escalate to GlobalDirector via send_message.
4. Report completion or blockers to GlobalDirector via send_message.
5. Workers communicate via conversation threads, not directly with you.
"""

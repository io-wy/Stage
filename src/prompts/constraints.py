"""可组合的硬约束 prompt 积木 —— 供任意角色的 prompts 列表引用。

单一信源：正文仍在 ``prompts.agent_constraints``（同时被 ``spawn_agent`` 在 spawn
时注入 user message 用）。本模块只做**别名暴露**，让声明式 prompts 用更短的引用名
``prompts.constraints:CODER`` / ``:REVIEWER``。改约束正文只改 agent_constraints。
"""

from __future__ import annotations

from prompts.agent_constraints import CODER_CONSTRAINT, REVIEWER_CONSTRAINT

CODER = CODER_CONSTRAINT
REVIEWER = REVIEWER_CONSTRAINT

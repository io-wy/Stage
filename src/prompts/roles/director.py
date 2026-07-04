"""Director 角色 prompt 积木 —— 供 agents/director.json 的 prompts 引用。

正文复用 ``prompts.director`` 的 ``DIRECTOR_PRINCIPLES`` / ``DIRECTOR_PRINCIPLES_COMPACT``（单一信源）。
compact 变体为默认（节省 token），``XITAI_DIRECTOR_PROMPT=full`` 可切回完整版。
"""

from __future__ import annotations

import os

from prompts.director import DIRECTOR_PRINCIPLES, DIRECTOR_PRINCIPLES_COMPACT


def select_director_principles() -> str:
    """选择 Director system prompt 变体。

    - 默认：compact（小 request-size / 低 token 消耗）
    - ``XITAI_DIRECTOR_PROMPT=full``：完整版（复杂多 agent 场景的完整指引）
    """
    variant = os.environ.get("XITAI_DIRECTOR_PROMPT", "compact").lower().strip()
    if variant == "full":
        return DIRECTOR_PRINCIPLES
    return DIRECTOR_PRINCIPLES_COMPACT


# agents/director.json -> "prompts.roles.director:PRINCIPLES"
PRINCIPLES = select_director_principles()

"""Director 角色 prompt 积木 —— 供 agents/director.json 的 prompts 引用。

正文复用 ``prompts.director`` 的 ``DIRECTOR_PRINCIPLES`` / ``_COMPACT``（单一信源）。
compact 变体的选择逻辑（``XITAI_DIRECTOR_PROMPT=compact``）下沉到本 prompt 层，
``DirectorPattern._PRINCIPLES`` 改为引用 ``PRINCIPLES``，保证「声明式 prompts」与
「类属性兜底」同源一致。
"""

from __future__ import annotations

import os

from prompts.director import DIRECTOR_PRINCIPLES, DIRECTOR_PRINCIPLES_COMPACT


def select_director_principles() -> str:
    """选择 Director system prompt 变体。

    - 默认：full（复杂多 agent 场景的完整指引）
    - ``XITAI_DIRECTOR_PROMPT=compact``：压缩版（小 request-size 限制的 provider）
    """
    variant = os.environ.get("XITAI_DIRECTOR_PROMPT", "full").lower().strip()
    if variant == "compact":
        return DIRECTOR_PRINCIPLES_COMPACT
    return DIRECTOR_PRINCIPLES


# agents/director.json -> "prompts.roles.director:PRINCIPLES"
PRINCIPLES = select_director_principles()

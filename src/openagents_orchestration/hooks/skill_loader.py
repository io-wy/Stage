"""Session-start skill loading (L1 progressive disclosure).

戏子 session 启动时，runner 通过 ``HookManager`` 触发 ``session.start`` 事件，本模块的
``load_skills_into_context`` 作为该事件的 handler，把可用 skill 清单（name+description）
注入戏子的 system prompt——让戏子"知道有哪些 skill"，再用 ``read_skill`` 读全文（L2）
照着做——方法论 skill 是 read-and-follow，没有 L3 执行层。

注入对象：配了 ``read_skill`` 工具的戏子，以及 director（分派任务时需要知道可推荐哪些
skill，即使它自己不调）。注入手法照搬 ``memory.CoreCoderMemory.inject``：往
``context.system_prompt_fragments`` append 一个 markdown 片段，由
``compose_system_prompt`` 折进 dynamic system prompt。

handler **直接消费 HookManager 的事件 payload**（一个 dict），无适配层——runner emit 时
往 payload 塞 ``context`` / ``agent_type`` / ``tool_names`` / ``registry`` 四样。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def should_load_skills(agent_type: str, tool_names: Iterable[str]) -> bool:
    """Whether this agent should receive the skill catalog.

    Agents that can call ``read_skill`` need it; the director needs it to
    recommend skills when delegating, even though it doesn't read them directly.
    """
    return agent_type == "director" or "read_skill" in set(tool_names)


def load_skills_into_context(payload: dict[str, Any]) -> dict[str, Any]:
    """``session.start`` hook handler: inject the skill catalog.

    Reads everything from the HookManager event payload:

    - ``context``: the agent's RunContext (catalog appended to its
      ``system_prompt_fragments``)
    - ``agent_type``: str, e.g. ``"coder"`` / ``"director"``
    - ``tool_names``: the agent's tool names (catalog injected only if it has
      ``read_skill``, or the agent is the director)
    - ``registry``: the :class:`SkillRegistry` to render the catalog from

    Returns the payload unchanged so it chains cleanly through
    ``HookManager.run``. No-op for ineligible agents, missing context/registry,
    or when no skills are discovered.
    """
    context = payload.get("context")
    registry = payload.get("registry")
    agent_type = payload.get("agent_type", "")
    tool_names = payload.get("tool_names", [])
    if (
        context is None
        or registry is None
        or not should_load_skills(agent_type, tool_names)
    ):
        return payload
    catalog = registry.render_catalog()
    if catalog:
        context.system_prompt_fragments.append(catalog)
    return payload

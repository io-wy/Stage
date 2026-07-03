"""agent_id utilities — derive task id from agent id.

Standalone helper (formerly colocated in store/artifact_store.py). Maps an
agent_id like ``coder-t1`` back to its task id ``t1`` for whitelisted role
prefixes; leaves one-off / dynamic role ids unchanged.
"""

from __future__ import annotations

_ROLE_PREFIXES = (
    "coder-",
    "reviewer-",
    "researcher-",
    "github_agent-",
    "monitor-",
    "director-",
    "team_leader-",
)


def infer_task_id(agent_id: str) -> str:
    """Extract task id from agent_id (e.g. ``coder-t1`` -> ``t1``).

    Only whitelisted role prefixes are stripped. A one-off / dynamic role id
    (outside the whitelist) is returned unchanged, so callers must pass task_id
    explicitly for such agents.
    """
    for prefix in _ROLE_PREFIXES:
        if agent_id.startswith(prefix):
            return agent_id[len(prefix):]
    return agent_id

"""Mailbox — pluggable per-agent message queues.

Each agent owns an isolated Mailbox.  Backends:
- InMemoryMailbox  (default, zero-dep)
- RedisMailbox     (persistent, distributed, optional redis dependency)
"""

from __future__ import annotations

from openagents_orchestration.mailbox.base import Mailbox
from openagents_orchestration.mailbox.memory import InMemoryMailbox

__all__ = ["Mailbox", "InMemoryMailbox"]

# RedisMailbox is only available when the optional ``redis`` package is installed.
try:
    from openagents_orchestration.mailbox.redis import RedisMailbox

    __all__.append("RedisMailbox")
except ImportError:  # pragma: no cover
    RedisMailbox = None  # type: ignore[misc,assignment]

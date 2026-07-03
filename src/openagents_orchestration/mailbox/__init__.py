"""Mailbox — per-agent message queues.

Each agent owns an isolated in-memory Mailbox.
"""

from __future__ import annotations

from openagents_orchestration.mailbox.base import Mailbox
from openagents_orchestration.mailbox.memory import InMemoryMailbox

__all__ = ["Mailbox", "InMemoryMailbox"]

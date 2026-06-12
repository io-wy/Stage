"""ChannelPolicy — communication access control between agents.

Defines who can send messages to whom. Patterns support exact ids, wildcards,
and special role markers like ``*_leader``.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any


class ChannelPolicyError(Exception):
    """Raised when a message violates the channel policy."""


@dataclass
class ChannelPolicy:
    """Access control rules for agent-to-agent messaging.

    Rules format::

        {
            sender_pattern: [recipient_pattern, ...],
            ...
        }

    Supported patterns:
    - Exact id: ``"director"``
    - Wildcard prefix/suffix: ``"coder-*"``, ``"*-leader"``
    - Role marker: ``"*_leader"`` matches any string ending in "_leader"
    - Universal: ``"*"`` matches any sender or recipient
    """

    rules: dict[str, list[str]] = field(default_factory=dict)

    def allows(self, sender: str, recipient: str) -> bool:
        """Return True if sender is allowed to message recipient.

        Rules are checked in insertion order.  A ``!`` prefix on a recipient
        pattern negates it: ``{"director": {"*", "!blocked-agent"}}`` means
        "director can send to anyone EXCEPT blocked-agent".  Deny rules take
        precedence over allow rules regardless of order within the same
        sender pattern.
        """
        for sender_pattern, recipient_patterns in self.rules.items():
            if not self._match(sender_pattern, sender):
                continue
            # Collect denies first so they always take precedence
            denies: list[str] = [
                p[1:] for p in recipient_patterns if p.startswith("!")
            ]
            allows: list[str] = [
                p for p in recipient_patterns if not p.startswith("!")
            ]
            # Check denies first
            for d in denies:
                if d == "*" or self._match(d, recipient):
                    return False
            # Then allows
            for a in allows:
                if a == "*" or self._match(a, recipient):
                    return True
        return False

    def assert_allowed(self, sender: str, recipient: str) -> None:
        """Raise ChannelPolicyError if messaging is not allowed."""
        if not self.allows(sender, recipient):
            raise ChannelPolicyError(
                f"ChannelPolicy blocked message from '{sender}' to '{recipient}'"
            )

    @staticmethod
    def _match(pattern: str, value: str) -> bool:
        """Match a value against a policy pattern.

        Special handling for ``*_leader`` role marker before fnmatch.
        """
        if pattern == "*":
            return True
        if pattern == "*_leader":
            return value.endswith("_leader")
        if pattern.endswith("-*"):
            return fnmatch.fnmatch(value, pattern)
        if pattern.startswith("*-"):
            return fnmatch.fnmatch(value, pattern)
        return pattern == value

    def to_dict(self) -> dict[str, Any]:
        return {"rules": dict(self.rules)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChannelPolicy:
        return cls(rules=dict(data.get("rules", {})))


# Default policies
DEFAULT_GLOBAL_POLICY = ChannelPolicy({
    "director": {"*"},
    "*_leader": {"director", "*_leader"},
    "coder-*": {"reviewer-*", "*_leader", "director"},
    "reviewer-*": {"coder-*", "*_leader", "director"},
    "tester-*": {"coder-*", "reviewer-*", "*_leader", "director"},
    "monitor-*": {"director", "*_leader"},
})

DEFAULT_TEAM_POLICY = ChannelPolicy({
    "team_leader": {"*"},
    "coder-*": {"reviewer-*", "team_leader"},
    "reviewer-*": {"coder-*", "team_leader"},
    "tester-*": {"coder-*", "reviewer-*", "team_leader"},
})

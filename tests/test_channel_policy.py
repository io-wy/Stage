"""Tests for ChannelPolicy."""

from __future__ import annotations

import pytest

from openagents_orchestration.transport.channel_policy import (
    DEFAULT_GLOBAL_POLICY,
    DEFAULT_TEAM_POLICY,
    ChannelPolicy,
    ChannelPolicyError,
)


class TestChannelPolicy:
    def test_exact_match(self):
        policy = ChannelPolicy({"director": {"coder-1"}})
        assert policy.allows("director", "coder-1") is True
        assert policy.allows("director", "coder-2") is False

    def test_wildcard_sender(self):
        policy = ChannelPolicy({"*": {"director"}})
        assert policy.allows("coder-1", "director") is True
        assert policy.allows("reviewer-1", "director") is True

    def test_wildcard_recipient(self):
        policy = ChannelPolicy({"director": {"*"}})
        assert policy.allows("director", "anyone") is True

    def test_prefix_wildcard(self):
        policy = ChannelPolicy({"coder-*": {"reviewer-*"}})
        assert policy.allows("coder-1", "reviewer-1") is True
        assert policy.allows("coder-1", "director") is False

    def test_role_marker_leader(self):
        policy = ChannelPolicy({"*_leader": {"director"}})
        assert policy.allows("backend_leader", "director") is True
        assert policy.allows("backend-leader", "director") is False

    def test_assert_allowed_raises(self):
        policy = ChannelPolicy({"director": {"coder-1"}})
        policy.assert_allowed("director", "coder-1")
        with pytest.raises(ChannelPolicyError):
            policy.assert_allowed("director", "coder-2")

    def test_default_global_policy(self):
        assert DEFAULT_GLOBAL_POLICY.allows("director", "coder-1") is True
        assert DEFAULT_GLOBAL_POLICY.allows("coder-1", "reviewer-1") is True
        assert DEFAULT_GLOBAL_POLICY.allows("coder-1", "director") is True
        assert DEFAULT_GLOBAL_POLICY.allows("reviewer-1", "coder-1") is True
        assert DEFAULT_GLOBAL_POLICY.allows("coder-1", "coder-2") is False

    def test_default_team_policy(self):
        assert DEFAULT_TEAM_POLICY.allows("team_leader", "coder-1") is True
        assert DEFAULT_TEAM_POLICY.allows("coder-1", "reviewer-1") is True
        assert DEFAULT_TEAM_POLICY.allows("coder-1", "team_leader") is True
        assert DEFAULT_TEAM_POLICY.allows("coder-1", "director") is False

    def test_deny_rule_blocks_when_matched(self):
        """A negated pattern (!prefix) blocks even if allow matches."""
        policy = ChannelPolicy({"director": {"*", "!coder-1"}})
        assert policy.allows("director", "coder-2") is True
        assert policy.allows("director", "coder-1") is False

    def test_deny_whole_wildcard(self):
        """!* blocks all recipients for the matched sender."""
        policy = ChannelPolicy({"director": {"!*"}})
        assert policy.allows("director", "anyone") is False

    def test_deny_does_not_affect_other_senders(self):
        """Deny rules only apply within their sender pattern scope."""
        policy = ChannelPolicy({
            "director": {"*", "!echo-agent"},
            "coder-*": {"reviewer-*"},
        })
        # coder-1 can send to reviewer-1 (different sender scope)
        assert policy.allows("coder-1", "reviewer-1") is True
        # echo-agent is not in coder's denylist - it only applies to director
        assert policy.allows("coder-1", "echo-agent") is False  # not in coder's allow

    def test_deny_takes_precedence_over_allow(self):
        """Even if a later rule also matches, deny always wins."""
        policy = ChannelPolicy({
            "director": {"*", "!secret-service"},
            "*": {"*"},  # universal allow — but deny still wins for director
        })
        assert policy.allows("director", "secret-service") is False
        assert policy.allows("director", "other") is True

    def test_roundtrip_serialization(self):
        policy = ChannelPolicy({"director": {"*"}, "coder-*": {"reviewer-*"}})
        d = policy.to_dict()
        restored = ChannelPolicy.from_dict(d)
        assert restored.allows("director", "anyone") is True
        assert restored.allows("coder-1", "reviewer-1") is True
        assert restored.allows("coder-1", "director") is False

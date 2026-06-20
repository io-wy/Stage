"""Adversarial + contract tests for ChannelPolicy and the default policies.

Module under test: ``src/openagents_orchestration/transport/channel_policy.py``
(deleted ``test_channel_policy.py``). ChannelPolicy is the access-control layer
StateBoard consults before every mail delivery, so its matching and
serialization semantics are security-relevant. ``test_gap_*`` pin foot-guns.
"""

from __future__ import annotations

import json

import pytest

from openagents_orchestration.transport.channel_policy import (
    DEFAULT_GLOBAL_POLICY,
    ChannelPolicy,
    ChannelPolicyError,
)

# ── contract that works ───────────────────────────────────────────────────────


def test_exact_match_allow_and_default_deny():
    p = ChannelPolicy({"a": ["b"]})
    assert p.allows("a", "b") is True
    assert p.allows("a", "c") is False  # recipient not in allow list
    assert p.allows("x", "b") is False  # sender pattern unmatched


def test_empty_policy_denies_everything():
    p = ChannelPolicy()
    assert p.allows("anyone", "anyone") is False
    with pytest.raises(ChannelPolicyError):
        p.assert_allowed("a", "b")


def test_wildcard_dash_patterns_match():
    p = ChannelPolicy({"coder-*": ["reviewer-*"]})
    assert p.allows("coder-1", "reviewer-2") is True
    assert p.allows("coder-1", "tester-2") is False


def test_role_marker_leader_matches_underscore_suffix():
    p = ChannelPolicy({"*_leader": ["director"]})
    assert p.allows("team_leader", "director") is True
    assert p.allows("backend_leader", "director") is True
    assert p.allows("coder-1", "director") is False


def test_deny_precedence_over_allow():
    p = ChannelPolicy({"director": ["*", "!secret-agent"]})
    assert p.allows("director", "anyone") is True
    assert p.allows("director", "secret-agent") is False  # deny wins over '*'


def test_default_global_policy_shape():
    assert DEFAULT_GLOBAL_POLICY.allows("director", "coder-1") is True
    assert DEFAULT_GLOBAL_POLICY.allows("coder-1", "reviewer-1") is True
    # coder may not message a monitor directly:
    assert DEFAULT_GLOBAL_POLICY.allows("coder-1", "monitor-1") is False


# ── GAP: to_dict() of the default policies is not JSON-serializable ───────────


def test_gap_to_dict_emits_nonjson_sets_for_default_policies():
    """The dataclass annotates ``rules`` as ``dict[str, list[str]]``, but every
    DEFAULT policy is built from SET literals (``{"*"}``). ``to_dict`` does
    ``dict(self.rules)`` and preserves the sets, so the canonical 'serialize the
    policy' path yields a structure ``json.dumps`` cannot encode — persistence /
    audit export of a default policy crashes."""
    d = DEFAULT_GLOBAL_POLICY.to_dict()
    assert isinstance(d["rules"]["director"], set)  # GAP: set, not list
    with pytest.raises(TypeError):
        json.dumps(d)  # GAP: sets are not JSON-serializable


# ── GAP: wildcard matching is gated on a dash ─────────────────────────────────


def test_gap_wildcard_requires_a_dash_to_match():
    """``_match`` only routes to fnmatch when the pattern ends with ``-*`` or
    starts with ``*-``. A natural glob like ``coder*`` (no dash) falls through to
    an EXACT compare, so it silently fails to match ``coder-1`` — anyone writing
    an intuitive pattern gets a deny-all rule with no warning."""
    dash = ChannelPolicy({"coder-*": ["*"]})
    nodash = ChannelPolicy({"coder*": ["*"]})
    assert dash.allows("coder-1", "x") is True
    assert nodash.allows("coder-1", "x") is False  # GAP: 'coder*' treated literally


def test_gap_role_marker_requires_underscore_not_dash():
    """The ``*_leader`` role marker is matched via ``endswith('_leader')``, so a
    dash-style id like ``team-leader`` is NOT recognized as a leader — the marker
    silently depends on underscore-vs-dash naming."""
    p = ChannelPolicy({"*_leader": ["director"]})
    assert p.allows("team_leader", "director") is True
    assert p.allows("team-leader", "director") is False  # GAP: dash form unmatched

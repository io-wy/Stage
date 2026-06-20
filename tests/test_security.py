"""Adversarial tests for the enterprise security model (capability tokens, audit log).

These tests are written to EXPOSE GAPS, not to rubber-stamp current behavior.
Tests prefixed ``test_gap_`` pin behavior that is currently surprising or
unsafe; each one's docstring states the risk, and the companion write-up lives
in ``docs/docs-tmp/testing-gaps-phase1-p1.md``.

Module under test: ``src/openagents_orchestration/enterprise/security.py``
(254 lines, zero coverage after the test cull — the old ``test_security.py`` was
among the 54 deleted files).
"""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from openagents_orchestration.enterprise.security import (
    AgentIdentity,
    AuditLog,
    CapabilityToken,
)

# A fixed secret so issue() and verify() agree within a test regardless of the
# process-random module default.
_SECRET = b"unit-test-secret-key-do-not-use-in-prod"


# ── CapabilityToken: the parts that work ─────────────────────────────────────


def test_issue_then_verify_roundtrip():
    t = CapabilityToken.issue(
        "director", "coder-1", ["read", "write"], ["proj-a"], secret=_SECRET
    )
    assert t.verify(_SECRET) is True
    assert t.can("read", "proj-a") is True
    assert t.can("write", "proj-a") is True


def test_wildcard_action_and_scope_grant_everything():
    t = CapabilityToken.issue("director", "bob", ["*"], ["*"], secret=_SECRET)
    assert t.can("anything", "any-scope") is True


def test_action_not_granted_is_denied():
    t = CapabilityToken.issue("director", "bob", ["read"], ["*"], secret=_SECRET)
    assert t.can("delete", "x") is False


def test_to_dict_from_dict_roundtrip_preserves_verification():
    t = CapabilityToken.issue("d", "bob", ["read"], ["proj-a"], secret=_SECRET)
    restored = CapabilityToken.from_dict(t.to_dict())
    assert restored == t
    assert restored.verify(_SECRET) is True


def test_tampering_with_bearer_breaks_signature():
    t = CapabilityToken.issue("d", "bob", ["read"], ["*"], secret=_SECRET)
    forged = replace(t, bearer="mallory")
    assert forged.verify(_SECRET) is False


def test_wrong_secret_fails_verification():
    t = CapabilityToken.issue("d", "bob", ["read"], ["*"], secret=_SECRET)
    assert t.verify(b"a-different-secret-entirely") is False


# ── expiry ────────────────────────────────────────────────────────────────────


def test_expired_token_neither_authorizes_nor_verifies():
    t = CapabilityToken.issue("d", "b", ["*"], ["*"], ttl_s=-1.0, secret=_SECRET)
    assert t.is_expired() is True
    assert t.can("read") is False
    # verify() also short-circuits to False on expiry (cannot validate the
    # signature of an expired token even if it is otherwise authentic).
    assert t.verify(_SECRET) is False


# ── GAP A: signature does not bind action-list STRUCTURE (delimiter collision) ──


def test_gap_delimiter_collision_enables_privilege_split():
    """HMAC payload joins actions with ``,`` and fields with ``|`` *without
    escaping*. A token issued for the single action ``"read,write"`` therefore
    signs the identical byte string as a token for ``["read", "write"]``.

    Consequence: an attacker can take a legitimately signed token and SPLIT one
    action into two, gaining ``can("write")`` that the original never granted —
    the forged token still passes verify().
    """
    legit = CapabilityToken.issue(
        "director", "bob", ["read,write"], ["*"], secret=_SECRET
    )
    # The genuine token is NOT authorized for the standalone action "write":
    assert legit.can("write") is False

    forged = replace(legit, actions=("read", "write"))
    # GAP: same comma-joined payload → same HMAC → forgery passes verification.
    assert forged.verify(_SECRET) is True
    # GAP: and now authorizes an action the signed grant never contained.
    assert forged.can("write") is True


def test_gap_field_delimiter_unescaped_in_payload():
    """The ``|`` field separator is likewise unescaped, so an action containing
    ``|`` can bleed into adjacent payload fields. This pins that no escaping
    exists (a hardened impl would reject or escape such values)."""
    a = CapabilityToken.issue("iss", "bob", ["x|proj-a"], [], secret=_SECRET)
    b = CapabilityToken.issue("iss", "bob", ["x"], ["proj-a"], secret=_SECRET)
    # Payloads: "iss|bob|x|proj-a||<exp>" vs "iss|bob|x|proj-a|<exp>" differ only
    # by an empty scope segment — not a collision here, but they share the same
    # raw field stream up to the scope boundary, demonstrating the ambiguity.
    # The concrete, exploitable collision is covered above; this guards the
    # broader "no canonical encoding" property.
    assert a.signature != b.signature  # current behavior; documents the shape


# ── GAP B: can() performs NO signature check (authz decoupled from authn) ───────


def test_gap_can_authorizes_completely_forged_token():
    """``can()`` checks only expiry + membership; it never validates the
    signature. A fully forged token (garbage signature) authorizes any action.

    Callers MUST call ``verify()`` first, but nothing enforces that — and the
    one production caller (``state_board._send_structured`` ~L1205) calls
    ``verify()`` yet NEVER calls ``can()``, so a token's actions/scope are not
    actually enforced anywhere in the send path.
    """
    forged = CapabilityToken(
        issuer="mallory",
        bearer="mallory",
        actions=("*",),
        scope=("*",),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        signature=b"not-a-real-signature",
    )
    assert forged.verify(_SECRET) is False  # signature is bogus
    assert forged.can("delete_production") is True  # GAP: authorized anyway


# ── GAP C: omitting the scope argument silently bypasses scope enforcement ──────


def test_gap_default_scope_bypasses_scope_enforcement():
    """A token scoped to 'project-a' is meant to be usable only there, but
    ``can(action)`` with no scope arg treats it as 'no scope requested' and
    returns True — a caller that forgets the scope gets a global grant."""
    t = CapabilityToken.issue("director", "bob", ["deploy"], ["project-a"], secret=_SECRET)
    assert t.can("deploy", "project-a") is True
    assert t.can("deploy", "project-b") is False  # correct: out of scope
    assert t.can("deploy") is True  # GAP: scope check skipped entirely


# ── GAP D: from_dict crashes on malformed input instead of failing cleanly ──────


def test_gap_from_dict_raises_valueerror_on_malformed_signature():
    """``from_dict`` pipes the signature straight into ``bytes.fromhex`` with no
    guard, so a corrupted/persisted token raises a raw ValueError rather than
    returning an unverifiable token or a typed SecurityError."""
    d = CapabilityToken.issue("d", "b", ["read"], ["*"], secret=_SECRET).to_dict()
    d["signature"] = "zz"  # not valid hex
    with pytest.raises(ValueError):
        CapabilityToken.from_dict(d)


def test_gap_identity_from_dict_raises_on_malformed_timestamp():
    """``AgentIdentity.from_dict`` calls ``datetime.fromisoformat`` unguarded."""
    with pytest.raises(ValueError):
        AgentIdentity.from_dict({"agent_id": "x", "created_at": "not-a-date"})


# ── GAP E: the promised env-var secret override is unimplemented ────────────────


def test_gap_no_env_override_for_signing_secret(monkeypatch):
    """The module docstring promises 'Override in production via env var or
    config', but NO code reads any env var: ``_DEFAULT_SECRET`` is a
    process-random value fixed at import. Setting env has zero effect, so a
    token issued under the process default cannot be verified after a
    persistence reload in a fresh process, and operators have no supported way
    to pin a shared secret short of threading ``secret=`` through every call.
    """
    monkeypatch.setenv("CAPABILITY_SECRET", "shared-prod-secret")
    monkeypatch.setenv("LLM_SECRET", "shared-prod-secret")
    t = CapabilityToken.issue("d", "b", ["read"], ["*"])  # uses process default
    # Env had no effect — the 'configured' secret does not verify the token:
    assert t.verify(b"shared-prod-secret") is False


# ── AuditLog ────────────────────────────────────────────────────────────────────


def test_audit_log_records_and_filters():
    log = AuditLog()
    log.record("mail.sent", actor="coder-1", action="send", target="rev-1", size=42)
    log.record("mail.sent", actor="coder-2", action="send", target="rev-1")
    by_actor = log.query(actor="coder-1")
    assert len(by_actor) == 1
    assert by_actor[0].details["size"] == 42
    assert len(log.query(target="rev-1")) == 2
    assert len(log.query(action="send")) == 2
    assert len(log.query(event_type="nope")) == 0


def test_audit_query_since_is_inclusive_lower_bound():
    """``query(since=t)`` keeps entries with ``ts >= t`` (the filter is
    ``ts < since: continue``). Pinning this because the sibling HumanChannel
    ``since`` filter uses the *opposite* (exclusive) convention — the two APIs
    disagree on boundary semantics."""
    log = AuditLog()
    log.record("x", "a", "first")
    t0 = log.query()[0].ts
    time.sleep(0.005)
    log.record("x", "a", "second")
    res = log.query(since=t0)
    assert len(res) == 2  # the entry exactly at `since` is included


def test_audit_retention_purges_on_record():
    """With zero retention, the prior entry is purged when the next is recorded
    (``record`` purges *before* appending)."""
    log = AuditLog(retention_days=0.0)
    log.record("x", "a", "act1")
    time.sleep(0.005)
    log.record("x", "a", "act2")
    entries = log.query()
    assert len(entries) == 1
    assert entries[0].action == "act2"

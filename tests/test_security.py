"""Tests for security model — AgentIdentity, CapabilityToken, AuditLog."""

from __future__ import annotations

import time

import pytest

from openagents_orchestration.enterprise.security import (
    AgentIdentity,
    AuditLog,
    CapabilityToken,
    SecurityError,
)


class TestAgentIdentity:
    def test_creation(self):
        ident = AgentIdentity(agent_id="a1", agent_type="coder", project_id="p1")
        assert ident.agent_id == "a1"
        assert ident.agent_type == "coder"
        assert ident.project_id == "p1"
        assert ident.team_id == ""

    def test_roundtrip_dict(self):
        ident = AgentIdentity(agent_id="a1", agent_type="coder")
        d = ident.to_dict()
        restored = AgentIdentity.from_dict(d)
        assert restored.agent_id == "a1"
        assert restored.agent_type == "coder"


class TestCapabilityToken:
    def test_issue_and_verify(self):
        token = CapabilityToken.issue(
            issuer="director",
            bearer="coder-1",
            actions=["read", "write"],
            scope=["project-a"],
            ttl_s=3600,
        )
        assert token.issuer == "director"
        assert token.bearer == "coder-1"
        assert token.verify() is True

    def test_can_action(self):
        token = CapabilityToken.issue(
            issuer="director",
            bearer="coder-1",
            actions=["read", "write"],
            scope=["project-a"],
            ttl_s=3600,
        )
        assert token.can("read", "project-a") is True
        assert token.can("write", "project-a") is True
        assert token.can("delete", "project-a") is False
        assert token.can("read", "project-b") is False

    def test_wildcard_action(self):
        token = CapabilityToken.issue(
            issuer="director",
            bearer="admin",
            actions=["*"],
            scope=["*"],
            ttl_s=3600,
        )
        assert token.can("anything", "anywhere") is True

    def test_expired_token(self):
        token = CapabilityToken.issue(
            issuer="director",
            bearer="coder-1",
            actions=["read"],
            scope=["*"],
            ttl_s=-1,  # already expired
        )
        assert token.is_expired() is True
        assert token.can("read") is False
        assert token.verify() is False

    def test_roundtrip_dict(self):
        token = CapabilityToken.issue(
            issuer="director",
            bearer="coder-1",
            actions=["read"],
            scope=["*"],
            ttl_s=3600,
        )
        d = token.to_dict()
        restored = CapabilityToken.from_dict(d)
        assert restored.issuer == token.issuer
        assert restored.actions == token.actions
        assert restored.verify() is True

    def test_tampered_token_fails_verify(self):
        token = CapabilityToken.issue(
            issuer="director",
            bearer="coder-1",
            actions=["read"],
            scope=["*"],
            ttl_s=3600,
        )
        # Modify the token
        bad = CapabilityToken.from_dict(token.to_dict())
        bad = CapabilityToken(
            issuer=bad.issuer,
            bearer=bad.bearer,
            actions=bad.actions,
            scope=bad.scope,
            expires_at=bad.expires_at,
            signature=b"tampered",
        )
        assert bad.verify() is False


class TestAuditLog:
    def test_record_and_query(self):
        log = AuditLog()
        log.record("test", actor="a1", action="spawn", target="coder-1")
        results = log.query(actor="a1")
        assert len(results) == 1
        assert results[0].actor == "a1"
        assert results[0].action == "spawn"

    def test_query_filters(self):
        log = AuditLog()
        log.record("test", actor="a1", action="spawn", target="c1")
        log.record("test", actor="a2", action="stop", target="c2")
        log.record("test", actor="a1", action="spawn", target="c3")

        assert len(log.query(actor="a1")) == 2
        assert len(log.query(action="stop")) == 1
        assert len(log.query(target="c1")) == 1
        assert len(log.query(actor="a1", action="spawn")) == 2

    def test_retention_purges_old(self):
        log = AuditLog(retention_days=0.0)  # immediate purge
        log.record("test", actor="a1", action="spawn")
        # Force purge
        log._purge_old()
        # Entry should be gone due to immediate retention
        assert len(log.query(actor="a1")) == 0

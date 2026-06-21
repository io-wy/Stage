"""Security model — capability tokens and identity for enterprise orchestration.

Each Agent receives an AgentIdentity at spawn time.  Cross-project messages
must be signed by the GlobalDirector.  All message sends are audited.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time as _time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any


class SecurityError(Exception):
    """Raised on authentication or authorization failure."""


# Module-level default secret. Prefer env override for cross-process token
# verification; fall back to a random per-process secret.
_DEFAULT_SECRET: bytes = os.environ.get(
    "STAGE_SIGNING_SECRET", ""
).encode() or secrets.token_bytes(32)


@dataclass(frozen=True, slots=True)
class AgentIdentity:
    """Immutable identity assigned to every agent at spawn time."""

    agent_id: str
    agent_type: str
    project_id: str = ""
    team_id: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "project_id": self.project_id,
            "team_id": self.team_id,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentIdentity:
        created_at = data.get("created_at")
        if created_at:
            created_at = datetime.fromisoformat(created_at)
        else:
            created_at = datetime.now(UTC)
        return cls(
            agent_id=data.get("agent_id", ""),
            agent_type=data.get("agent_type", ""),
            project_id=data.get("project_id", ""),
            team_id=data.get("team_id", ""),
            created_at=created_at,
        )


@dataclass(frozen=True)
class CapabilityToken:
    """Signed capability token authorizing a bounded set of actions.

    Tokens are bearer-style: whoever holds the token can exercise its
    capabilities within the declared scope.  In production the secret
    key should be rotated and stored in a secrets manager (Vault, AWS
    Secrets Manager, etc.).
    """

    issuer: str
    bearer: str
    actions: tuple[str, ...]
    scope: tuple[str, ...]
    expires_at: datetime
    signature: bytes

    def is_expired(self, now: datetime | None = None) -> bool:
        if now is None:
            now = datetime.now(UTC)
        return now >= self.expires_at

    def can(self, action: str, scope: str = "") -> bool:
        """True if the token authorizes ``action`` within ``scope``.

        Callers that need cryptographic assurance MUST call ``verify()`` first
        (or use ``can_with_verify``). This method intentionally does NOT verify
        the signature so that authn/authz remain separable.
        """
        if self.is_expired():
            return False
        if action not in self.actions and "*" not in self.actions:
            return False
        if not scope:
            return True
        if "*" in self.scope:
            return True
        return scope in self.scope

    def can_with_verify(
        self, action: str, scope: str = "", secret: bytes | None = None
    ) -> bool:
        """True if the token is valid AND authorizes ``action`` within ``scope``."""
        return self.verify(secret) and self.can(action, scope)

    def verify(self, secret: bytes | None = None) -> bool:
        """Recompute and compare the HMAC signature."""
        if self.is_expired():
            return False
        expected = self._sign(secret or _DEFAULT_SECRET)
        return hmac.compare_digest(expected, self.signature)

    def _sign(self, secret: bytes) -> bytes:
        """Compute HMAC-SHA256 over a canonical JSON payload."""
        payload = json.dumps(
            {
                "issuer": self.issuer,
                "bearer": self.bearer,
                "actions": sorted(self.actions),
                "scope": sorted(self.scope),
                "expires_at": self.expires_at.isoformat(),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hmac.new(secret, payload.encode(), hashlib.sha256).digest()

    @classmethod
    def issue(
        cls,
        issuer: str,
        bearer: str,
        actions: list[str],
        scope: list[str],
        ttl_s: float = 3600.0,
        secret: bytes | None = None,
    ) -> CapabilityToken:
        """Mint a new capability token."""
        expires_at = datetime.now(UTC) + timedelta(seconds=ttl_s)
        token = cls(
            issuer=issuer,
            bearer=bearer,
            actions=tuple(actions),
            scope=tuple(scope),
            expires_at=expires_at,
            signature=b"",
        )
        sec = secret or _DEFAULT_SECRET
        sig = token._sign(sec)
        return cls(
            issuer=token.issuer,
            bearer=token.bearer,
            actions=token.actions,
            scope=token.scope,
            expires_at=token.expires_at,
            signature=sig,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "issuer": self.issuer,
            "bearer": self.bearer,
            "actions": list(self.actions),
            "scope": list(self.scope),
            "expires_at": self.expires_at.isoformat(),
            "signature": self.signature.hex(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CapabilityToken:
        expires_at = data.get("expires_at")
        if expires_at:
            expires_at = datetime.fromisoformat(expires_at)
        else:
            expires_at = datetime.now(UTC)
        return cls(
            issuer=data.get("issuer", ""),
            bearer=data.get("bearer", ""),
            actions=tuple(data.get("actions", [])),
            scope=tuple(data.get("scope", [])),
            expires_at=expires_at,
            signature=bytes.fromhex(data.get("signature", "")),
        )


@dataclass
class AuditLogEntry:
    """A single audit record."""

    ts: float
    event_type: str
    actor: str
    action: str
    target: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "event_type": self.event_type,
            "actor": self.actor,
            "action": self.action,
            "target": self.target,
            "details": self.details,
        }


class AuditLog:
    """In-memory audit log with retention."""

    def __init__(self, retention_days: float = 30.0):
        self._entries: list[AuditLogEntry] = []
        self._retention_s = retention_days * 86400.0

    def record(
        self,
        event_type: str,
        actor: str,
        action: str,
        target: str = "",
        **details: Any,
    ) -> None:
        """Append an audit entry."""
        self._purge_old()
        self._entries.append(
            AuditLogEntry(
                ts=_time.time(),
                event_type=event_type,
                actor=actor,
                action=action,
                target=target,
                details=details,
            )
        )

    def query(
        self,
        *,
        actor: str | None = None,
        action: str | None = None,
        target: str | None = None,
        event_type: str | None = None,
        since: float = 0.0,
    ) -> list[AuditLogEntry]:
        """Filter audit entries."""
        results: list[AuditLogEntry] = []
        for e in self._entries:
            if e.ts < since:
                continue
            if actor is not None and e.actor != actor:
                continue
            if action is not None and e.action != action:
                continue
            if target is not None and e.target != target:
                continue
            if event_type is not None and e.event_type != event_type:
                continue
            results.append(e)
        return results

    def _purge_old(self) -> None:
        cutoff = _time.time() - self._retention_s
        self._entries = [e for e in self._entries if e.ts > cutoff]

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": [e.to_dict() for e in self._entries],
            "retention_days": self._retention_s / 86400.0,
        }

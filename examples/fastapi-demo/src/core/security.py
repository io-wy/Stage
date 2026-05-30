from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from jose import JWTError, jwt
import bcrypt

from openagents_orchestration.app.core.config import get_settings

BCRYPT_MAX_PASSWORD_BYTES = 72


def _password_bytes(password: str) -> bytes:
    password_bytes = password.encode("utf-8")
    if len(password_bytes) > BCRYPT_MAX_PASSWORD_BYTES:
        msg = "password must be 72 bytes or fewer when encoded as UTF-8"
        raise ValueError(msg)
    return password_bytes


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Return True when a plaintext password matches its stored hash."""

    return bcrypt.checkpw(
        _password_bytes(plain_password),
        hashed_password.encode("utf-8"),
    )


def get_password_hash(password: str) -> str:
    """Hash a plaintext password for storage."""

    return bcrypt.hashpw(_password_bytes(password), bcrypt.gensalt()).decode("utf-8")


def create_access_token(
    subject: str | int,
    expires_delta: timedelta | None = None,
    additional_claims: dict[str, Any] | None = None,
) -> str:
    """Create a signed JWT access token.

    The subject is stored in the standard ``sub`` claim. If ``expires_delta`` is
    omitted, ``settings.ACCESS_TOKEN_EXPIRE_MINUTES`` is used.
    """

    settings = get_settings()
    now = datetime.now(UTC)
    expire = now + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )

    payload: dict[str, Any] = {"sub": str(subject), "iat": now, "exp": expire}
    if additional_claims:
        payload.update(additional_claims)

    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any] | None:
    """Decode and validate a JWT access token.

    Returns the token payload when valid, otherwise ``None``.
    """

    settings = get_settings()
    try:
        return jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except JWTError:
        return None


def get_token_subject(token: str) -> str | None:
    """Return the ``sub`` claim from a valid access token, if present."""

    payload = decode_access_token(token)
    if payload is None:
        return None

    subject = payload.get("sub")
    return str(subject) if subject is not None else None

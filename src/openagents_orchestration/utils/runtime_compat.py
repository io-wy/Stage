"""Compatibility helpers for multiple OpenAgents SDK runtime shapes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openagents.errors.exceptions import OpenAgentsError
from openagents.interfaces.runtime import RunResult

try:  # Newer SDKs expose ErrorDetails in interfaces.runtime
    from openagents.interfaces.runtime import ErrorDetails as _SDKErrorDetails
except ImportError:  # pragma: no cover - exercised under older SDKs
    _SDKErrorDetails = None


@dataclass
class _CompatErrorDetails:
    message: str
    error_type: str
    module: str

    @classmethod
    def from_exception(cls, exc: BaseException) -> "_CompatErrorDetails":
        return cls(
            message=str(exc) or exc.__class__.__name__,
            error_type=exc.__class__.__name__,
            module=exc.__class__.__module__,
        )

    def model_dump(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "error_type": self.error_type,
            "module": self.module,
        }


def build_error_details(exc: BaseException) -> Any:
    """Return a best-effort error details object for this SDK version."""
    if _SDKErrorDetails is not None:
        return _SDKErrorDetails.from_exception(exc)
    return _CompatErrorDetails.from_exception(exc)


def error_details_payload(exc: BaseException) -> dict[str, Any]:
    details = build_error_details(exc)
    if hasattr(details, "model_dump"):
        return details.model_dump()
    if hasattr(details, "dict"):
        return details.dict()
    return {
        "message": str(exc),
        "error_type": exc.__class__.__name__,
        "module": exc.__class__.__module__,
    }


def run_result_error_kwargs(exc: BaseException) -> dict[str, Any]:
    """Build RunResult kwargs that match the installed SDK."""
    fields = getattr(RunResult, "model_fields", {}) or {}
    if "error_details" in fields:
        return {"error_details": build_error_details(exc)}

    kwargs: dict[str, Any] = {"error": str(exc)}
    if "exception" in fields and isinstance(exc, OpenAgentsError):
        kwargs["exception"] = exc
    return kwargs


def extract_result_error_message(result: Any) -> str:
    """Read the most useful failure message from a RunResult-like object."""
    details = getattr(result, "error_details", None)
    if details is not None:
        msg = getattr(details, "message", None)
        if msg:
            return str(msg)
        return str(details)

    error = getattr(result, "error", None)
    if error:
        return str(error)

    exc = getattr(result, "exception", None)
    if exc is not None:
        return str(exc)

    return "Agent failed"


def apply_sdk_patches() -> None:
    """Patch older SDK/provider quirks at import time."""
    try:
        from openagents.llm.providers import openai_compatible as provider
    except Exception:
        return

    original = getattr(provider, "_parse_tool_calls", None)
    if original is None or getattr(original, "__oa_patched__", False):
        return

    def _safe_parse_tool_calls(payload: Any) -> list[Any]:
        if payload is None:
            payload = []
        return original(payload)

    _safe_parse_tool_calls.__oa_patched__ = True  # type: ignore[attr-defined]
    provider._parse_tool_calls = _safe_parse_tool_calls


def is_retryable_llm_error(exc: BaseException) -> bool:
    """Best-effort classifier for flaky upstream/provider failures."""
    text = str(exc).lower()
    name = exc.__class__.__name__.lower()
    signals = (
        "server disconnected",
        "remoteprotocolerror",
        "timed out",
        "timeout",
        "connection reset",
        "connection aborted",
        "temporarily unavailable",
        "bad gateway",
        "service unavailable",
        "gateway timeout",
        "http 502",
        "http 503",
        "http 504",
        "rate limit",
        "too many requests",
    )
    return any(signal in text or signal in name for signal in signals)

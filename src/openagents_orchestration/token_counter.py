"""TokenCounter — model-aware token counting for context assembly.

Wraps tiktoken when available, with a ``len(text) // 4`` fallback.
Automatically selects the right encoding based on model name.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class TokenCounter:
    """Count tokens in text using tiktoken or a rough fallback.

    Usage::

        counter = TokenCounter(model="gpt-4o")
        n = counter.count("hello world")
    """

    # Model → tiktoken encoding mapping (best-effort)
    _ENCODING_MAP: dict[str, str] = {
        "gpt-4o": "o200k_base",
        "gpt-4": "cl100k_base",
        "gpt-3.5": "cl100k_base",
        "claude": "cl100k_base",  # Claude uses roughly compatible tokenizer
    }

    def __init__(self, model: str | None = None):
        self._model = (model or "").lower()
        self._encoder: Any = None
        self._name = "fallback_len//4"
        self._init_encoder()

    def _init_encoder(self) -> None:
        """Try to load tiktoken; on failure keep fallback."""
        try:
            import tiktoken
        except ImportError:
            logger.debug("tiktoken not installed; using fallback token counter")
            return

        encoding_name = self._resolve_encoding()
        try:
            self._encoder = tiktoken.get_encoding(encoding_name)
            self._name = f"tiktoken-{encoding_name}"
            logger.debug("TokenCounter using %s", self._name)
        except Exception as exc:
            logger.warning("tiktoken encoding %s failed: %s; using fallback", encoding_name, exc)

    def _resolve_encoding(self) -> str:
        """Pick tiktoken encoding based on model name."""
        for prefix, enc in self._ENCODING_MAP.items():
            if prefix in self._model:
                return enc
        return "cl100k_base"  # safest default

    # -- public API ----------------------------------------------------------

    def count(self, text: str) -> int:
        """Return token count for *text*.  Always >= 1."""
        if not text:
            return 0
        if self._encoder is not None:
            try:
                return max(1, len(self._encoder.encode(text)))
            except Exception:
                pass
        return max(1, len(text) // 4)

    def count_messages(self, messages: list[dict[str, Any]]) -> int:
        """Count tokens in a list of transcript messages."""
        total = 0
        for msg in messages:
            text = _content_to_text(msg.get("content"))
            total += self.count(text)
        return total

    @property
    def name(self) -> str:
        return self._name


# ---- helper (mirrors context._content_to_text) ---------------------------

import json


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif block.get("type") == "tool_use":
                    parts.append(
                        f"[tool_use {block.get('name', '?')}("
                        f"{json.dumps(block.get('input', {}), default=str, ensure_ascii=False)})]"
                    )
                elif block.get("type") == "tool_result":
                    parts.append(str(block.get("content", "")))
                else:
                    parts.append(json.dumps(block, default=str, ensure_ascii=False))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)

"""3-layer context compressor for the CoreCoder agent.

Faithful port of CoreCoder's progressive compression scheme:

* **Layer 1 — tool-output snipping** (triggers at 50% of budget).
  Walks every transcript message, finds long tool_result blocks (or long
  string contents that look like tool output), and keeps only the first
  ``head`` + last ``tail`` bytes with a ``[snipped N bytes]`` marker. Cheap
  and lossless for the parts that matter (start of stdout / tail of stderr).

* **Layer 2 — LLM summarization** (triggers at 70%).
  Hands the older half of the transcript to the LLM with a tight prompt
  ("preserve file paths edited, key decisions, unresolved errors") and
  replaces it with the returned single ``system`` message. Falls back to a
  deterministic head+tail concatenation if no LLM client is wired (test mode).

* **Layer 3 — hard collapse** (triggers at 90%).
  Keeps the first ``keep_first_messages`` (the original task statement and
  any leading system context) and the last ``keep_last_messages``
  (the recent loop turns) and drops everything in between with a single
  placeholder. This is the last-resort guard so we never overflow the LLM
  context window.

The layers run sequentially: Layer 1 runs first; if the transcript is still
above the next threshold we run Layer 2, etc. ``metadata`` records which
layers fired so observers can inspect compression behavior.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel

from openagents.interfaces.context import (
    ContextAssemblerPlugin,
    ContextAssemblyResult,
)


_DEFAULT_BUDGET_TOKENS = 12_000
_DEFAULT_RESERVE = 2_000


class CompressingContextAssembler(ContextAssemblerPlugin):
    """3-layer compressor (snip → summarize → hard-collapse)."""

    class Config(BaseModel):
        max_input_tokens: int = _DEFAULT_BUDGET_TOKENS
        reserve_for_response: int = _DEFAULT_RESERVE
        snip_threshold: float = 0.5
        summarize_threshold: float = 0.7
        hard_collapse_threshold: float = 0.9
        tool_output_max_bytes: int = 2_000
        tool_output_keep_head: int = 1_000
        tool_output_keep_tail: int = 500
        keep_recent_messages_for_summary: int = 10
        keep_first_messages: int = 2
        keep_last_messages_on_collapse: int = 5
        max_artifacts: int = 10
        summary_max_words: int = 200
        summary_model: str | None = None

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})
        self._cfg = self.Config.model_validate(self.config)

    @property
    def _budget(self) -> int:
        return max(1, self._cfg.max_input_tokens - self._cfg.reserve_for_response)

    async def assemble(
        self,
        *,
        request: Any,
        session_state: dict[str, Any],
        session_manager: Any,
    ) -> ContextAssemblyResult:
        llm_client = (
            session_state.get("llm_client") if isinstance(session_state, dict) else None
        )
        transcript = await session_manager.load_messages(request.session_id)
        artifacts = await session_manager.list_artifacts(request.session_id)

        layers_fired: list[str] = []
        original_tokens = self._count_total(llm_client, transcript)

        # ---- Layer 1: snip long tool outputs (>= 50%) ---------------------
        ratio = original_tokens / self._budget if self._budget else 0.0
        if ratio >= self._cfg.snip_threshold:
            transcript, snipped_bytes = self._snip_tool_outputs(transcript)
            if snipped_bytes > 0:
                layers_fired.append("snip")

        after_layer1 = self._count_total(llm_client, transcript)
        ratio = after_layer1 / self._budget if self._budget else 0.0

        # ---- Layer 2: LLM summarize older half (>= 70%) -------------------
        if ratio >= self._cfg.summarize_threshold:
            transcript = await self._summarize_old_half(llm_client, transcript)
            layers_fired.append("summarize")

        after_layer2 = self._count_total(llm_client, transcript)
        ratio = after_layer2 / self._budget if self._budget else 0.0

        # ---- Layer 3: hard collapse middle (>= 90%) -----------------------
        if ratio >= self._cfg.hard_collapse_threshold:
            transcript = self._hard_collapse(transcript)
            layers_fired.append("hard_collapse")

        if len(artifacts) > self._cfg.max_artifacts:
            omitted_artifacts = len(artifacts) - self._cfg.max_artifacts
            artifacts = artifacts[-self._cfg.max_artifacts :]
        else:
            omitted_artifacts = 0

        final_tokens = self._count_total(llm_client, transcript)
        return ContextAssemblyResult(
            transcript=transcript,
            session_artifacts=artifacts,
            metadata={
                "assembler": "CompressingContextAssembler",
                "strategy": "compressing",
                "budget_input_tokens": self._cfg.max_input_tokens,
                "tokens_before": original_tokens,
                "tokens_after": final_tokens,
                "layers_fired": layers_fired,
                "omitted_artifacts": omitted_artifacts,
                "token_counter": self._token_counter_name(llm_client),
            },
        )

    async def compact(
        self,
        *,
        request: Any,
        session_state: dict[str, Any],
        session_manager: Any,
    ) -> None:
        return None

    async def finalize(
        self,
        *,
        request: Any,
        session_state: dict[str, Any],
        session_manager: Any,
        result: Any,
    ) -> Any:
        return result

    # ---- token counting helpers ------------------------------------------

    def _measure(self, llm_client: Any, msg: dict[str, Any]) -> int:
        text = _content_to_text(msg.get("content"))
        if llm_client is None:
            return max(1, len(text) // 4)
        try:
            return max(1, llm_client.count_tokens(text))
        except (AttributeError, TypeError):
            return max(1, len(text) // 4)

    def _count_total(self, llm_client: Any, msgs: list[dict[str, Any]]) -> int:
        return sum(self._measure(llm_client, m) for m in msgs)

    def _token_counter_name(self, llm_client: Any) -> str:
        if llm_client is None:
            return "fallback_len//4"
        provider = getattr(llm_client, "provider_name", "")
        if provider == "openai_compatible":
            try:
                import tiktoken  # noqa: F401

                return "tiktoken"
            except ImportError:
                return "fallback_len//4"
        return "fallback_len//4"

    # ---- Layer 1: snip ---------------------------------------------------

    def _snip_tool_outputs(
        self, transcript: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], int]:
        """Snip long tool outputs in-place; returns (transcript, bytes_saved)."""
        max_bytes = self._cfg.tool_output_max_bytes
        head = self._cfg.tool_output_keep_head
        tail = self._cfg.tool_output_keep_tail
        saved = 0
        new_transcript: list[dict[str, Any]] = []
        for msg in transcript:
            content = msg.get("content")
            new_content, byte_delta = _snip_content(content, max_bytes, head, tail)
            saved += byte_delta
            new_msg = dict(msg)
            new_msg["content"] = new_content
            new_transcript.append(new_msg)
        return new_transcript, saved

    # ---- Layer 2: LLM summarize ------------------------------------------

    async def _summarize_old_half(
        self, llm_client: Any, transcript: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        keep_recent = self._cfg.keep_recent_messages_for_summary
        if len(transcript) <= keep_recent + 1:
            return transcript

        head_kept = transcript[: self._cfg.keep_first_messages]
        middle = transcript[self._cfg.keep_first_messages : -keep_recent]
        tail = transcript[-keep_recent:]
        if not middle:
            return transcript

        summary_text = await self._render_summary(llm_client, middle)
        summary_msg = {
            "role": "system",
            "content": (
                f"[CoreCoder context compression — {len(middle)} earlier message(s) "
                f"summarized]\n{summary_text}"
            ),
        }
        return head_kept + [summary_msg] + tail

    async def _render_summary(
        self, llm_client: Any, msgs: list[dict[str, Any]]
    ) -> str:
        rendered = "\n\n".join(_render_message_for_summary(m) for m in msgs)
        if llm_client is None:
            return _heuristic_summary(rendered, max_words=self._cfg.summary_max_words)

        prompt = (
            f"Summarize the following coding-agent conversation history into "
            f"a single paragraph of at most {self._cfg.summary_max_words} words. "
            "Preserve: (1) file paths read or edited, (2) decisions or design "
            "choices made, (3) unresolved errors or open questions, (4) "
            "important findings (bug locations, missing imports, failing tests). "
            "Drop chit-chat, repeated greetings, and verbose tool output. Do not "
            "fabricate information.\n\n=== HISTORY START ===\n"
            f"{rendered}\n=== HISTORY END ==="
        )
        try:
            response = await llm_client.generate(
                messages=[
                    {
                        "role": "system",
                        "content": "You are a concise summarizer. Output one paragraph.",
                    },
                    {"role": "user", "content": prompt},
                ],
                model=self._cfg.summary_model,
                temperature=0.0,
                max_tokens=512,
            )
            text = (response.output_text or "").strip()
            if text:
                return text
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:  # pragma: no cover - LLM error → graceful degradation
            pass
        return _heuristic_summary(rendered, max_words=self._cfg.summary_max_words)

    # ---- Layer 3: hard collapse ------------------------------------------

    def _hard_collapse(
        self, transcript: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        first_n = self._cfg.keep_first_messages
        last_n = self._cfg.keep_last_messages_on_collapse
        if len(transcript) <= first_n + last_n:
            return transcript
        head = transcript[:first_n]
        tail = transcript[-last_n:]
        omitted = len(transcript) - len(head) - len(tail)
        placeholder = {
            "role": "system",
            "content": (
                f"[CoreCoder hard-collapse: dropped {omitted} middle message(s) "
                "to fit the context window. If you need that history, summarize "
                "from what remains rather than asking the user.]"
            ),
        }
        return head + [placeholder] + tail


# ---- module helpers -----------------------------------------------------


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
                        f"[tool_use {block.get('name', '?')}({json.dumps(block.get('input', {}), default=str, ensure_ascii=False)})]"
                    )
                elif block.get("type") == "tool_result":
                    parts.append(str(block.get("content", "")))
                else:
                    parts.append(json.dumps(block, default=str, ensure_ascii=False))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)


def _snip_content(
    content: Any, max_bytes: int, head: int, tail: int
) -> tuple[Any, int]:
    """Walk a message's content and snip long tool outputs.

    Returns the (possibly new) content plus the number of characters saved.
    Operates on:
      - str content (rare for assistant turns; assumed to be tool output if long)
      - list-of-blocks content with ``tool_result`` / ``text`` types
    """
    if content is None:
        return content, 0

    if isinstance(content, str):
        if len(content) > max_bytes:
            new_text, saved = _snip_text(content, head, tail)
            return new_text, saved
        return content, 0

    if isinstance(content, list):
        new_blocks: list[Any] = []
        total_saved = 0
        for block in content:
            if not isinstance(block, dict):
                new_blocks.append(block)
                continue
            block_type = block.get("type")
            if block_type == "tool_result":
                payload = block.get("content")
                if isinstance(payload, str) and len(payload) > max_bytes:
                    new_text, saved = _snip_text(payload, head, tail)
                    total_saved += saved
                    new_block = dict(block)
                    new_block["content"] = new_text
                    new_blocks.append(new_block)
                else:
                    new_blocks.append(block)
            elif block_type == "text":
                text_value = block.get("text", "")
                if isinstance(text_value, str) and len(text_value) > max_bytes:
                    new_text, saved = _snip_text(text_value, head, tail)
                    total_saved += saved
                    new_block = dict(block)
                    new_block["text"] = new_text
                    new_blocks.append(new_block)
                else:
                    new_blocks.append(block)
            else:
                new_blocks.append(block)
        return new_blocks, total_saved

    return content, 0


def _snip_text(text: str, head: int, tail: int) -> tuple[str, int]:
    if len(text) <= head + tail:
        return text, 0
    snipped_bytes = len(text) - head - tail
    new_text = (
        text[:head]
        + f"\n... [snipped {snipped_bytes} bytes by CoreCoder layer-1 compression] ...\n"
        + text[-tail:]
    )
    return new_text, snipped_bytes


def _render_message_for_summary(msg: dict[str, Any]) -> str:
    role = msg.get("role", "?")
    body = _content_to_text(msg.get("content"))
    if len(body) > 1500:
        body = body[:1200] + "\n... (truncated for summarization input)"
    return f"[{role}] {body}"


def _heuristic_summary(rendered: str, *, max_words: int) -> str:
    """Cheap fallback when no LLM is available — keep the first/last lines."""
    lines = [ln for ln in rendered.splitlines() if ln.strip()]
    if not lines:
        return "(no prior context)"
    if len(lines) <= 8:
        joined = " | ".join(lines)
    else:
        joined = " | ".join(lines[:4] + ["..."] + lines[-3:])
    words = joined.split()
    if len(words) > max_words:
        words = words[:max_words] + ["..."]
    return " ".join(words)

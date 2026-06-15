"""Parse streaming LLM chunks into tool calls and support pre-execution."""

from __future__ import annotations

import json
from typing import Any


class _PartialToolCall:
    """Accumulates one tool call as it streams in."""

    def __init__(self, index: int) -> None:
        self.index = index
        self.id: str | None = None
        self.name: str | None = None
        self.arguments_buffer: str = ""
        self.completed: bool = False

    def apply_delta(self, delta: dict[str, Any]) -> None:
        """Apply an OpenAI-style tool_call delta."""
        if "id" in delta:
            self.id = delta["id"] or self.id
        function = delta.get("function") or {}
        if isinstance(function, dict):
            name = function.get("name")
            if isinstance(name, str) and name:
                self.name = name
            args = function.get("arguments")
            if isinstance(args, str):
                self.arguments_buffer += args

    @property
    def parsed_arguments(self) -> dict[str, Any] | None:
        """Return parsed arguments if JSON is complete, else None."""
        if not self.arguments_buffer:
            return None
        try:
            return json.loads(self.arguments_buffer)
        except json.JSONDecodeError:
            return None

    def to_tool_call(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "id": self.id or f"call_pre_{self.index}",
            "name": self.name or "unknown",
            "arguments": self.parsed_arguments or {},
        }


class StreamToolCallParser:
    """Parse streaming chunks into partial/completed tool calls.

    Handles OpenAI-style streaming where tool_calls arrive as deltas across
    multiple chunks. Produces completed tool_calls once the arguments JSON is
    fully received.
    """

    def __init__(self) -> None:
        self._partials: dict[int, _PartialToolCall] = {}
        self._completed: list[dict[str, Any]] = []

    def reset(self) -> None:
        """Reset state for a new streaming turn."""
        self._partials.clear()
        self._completed.clear()

    def feed_chunk(self, chunk: Any) -> list[dict[str, Any]]:
        """Feed one LLMChunk and return any newly completed tool calls.

        The chunk is expected to follow the SDK's LLMChunk shape for the
        OpenAI-compatible provider:
          - type="content_block_start", content={"type":"tool_use", "id":..., "name":...}
          - type="content_block_delta", delta={"type":"input_json_delta", "partial_json":...}
          - type="message_stop", content={"stop_reason": "tool_use" | ...}
        """
        newly_completed: list[dict[str, Any]] = []
        chunk_type = getattr(chunk, "type", None)

        if chunk_type == "content_block_start":
            content = getattr(chunk, "content", None) or {}
            if isinstance(content, dict) and content.get("type") == "tool_use":
                index = content.get("index", 0)
                partial = self._partials.setdefault(index, _PartialToolCall(index))
                partial.id = content.get("id") or partial.id
                partial.name = content.get("name") or partial.name

        elif chunk_type == "content_block_delta":
            delta = getattr(chunk, "delta", None) or {}
            if isinstance(delta, dict) and delta.get("type") == "input_json_delta":
                partial_json = delta.get("partial_json", "")
                if partial_json and self._partials:
                    # Apply to the most recently touched partial. In OpenAI
                    # streaming, tool argument deltas only arrive for one tool
                    # at a time per chunk, ordered by index.
                    if len(self._partials) == 1:
                        partial = next(iter(self._partials.values()))
                    else:
                        partial = self._partials.get(delta.get("index", 0))
                        if partial is None:
                            return newly_completed
                    partial.apply_delta({"function": {"arguments": partial_json}})
                    completed = partial.parsed_arguments
                    if completed is not None and not partial.completed:
                        partial.completed = True
                        call = partial.to_tool_call()
                        self._completed.append(call)
                        newly_completed.append(call)

        return newly_completed

    def finalize(self) -> list[dict[str, Any]]:
        """Return all completed tool calls at the end of a streaming turn."""
        return list(self._completed)


class PreExecutionCache:
    """Cache for tool pre-execution results."""

    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}

    def _key(self, tool_name: str, params: dict[str, Any]) -> str:
        try:
            params_json = json.dumps(params, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            params_json = str(params)
        return f"{tool_name}:{params_json}"

    def get(self, tool_name: str, params: dict[str, Any]) -> Any:
        return self._cache.get(self._key(tool_name, params), None)

    def set(self, tool_name: str, params: dict[str, Any], result: Any) -> None:
        self._cache[self._key(tool_name, params)] = result

    def clear(self) -> None:
        self._cache.clear()

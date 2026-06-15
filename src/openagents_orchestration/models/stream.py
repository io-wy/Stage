"""Stream event types for CoreCoderPattern streaming output."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class StreamEventType(StrEnum):
    """Event types emitted by CoreCoderPattern.execute_stream()."""

    text = "text"
    tool_call_start = "tool_call_start"
    tool_call_delta = "tool_call_delta"
    tool_call_complete = "tool_call_complete"
    tool_result = "tool_result"
    error = "error"
    complete = "complete"


@dataclass
class StreamEvent:
    """A single event in the CoreCoder streaming output stream."""

    type: StreamEventType
    text: str | None = None
    tool_id: str | None = None
    tool_name: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    is_error: bool = False
    error: str | None = None
    step: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def model_dump(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "text": self.text,
            "tool_id": self.tool_id,
            "tool_name": self.tool_name,
            "params": self.params,
            "result": self.result,
            "is_error": self.is_error,
            "error": self.error,
            "step": self.step,
            "metadata": self.metadata,
        }

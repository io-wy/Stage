"""Append-only governance audit storage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openagents_orchestration.control.models import CaseAuditEvent


class AuditStore:
    """Append-only JSONL store for governance events."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: CaseAuditEvent) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json())
            handle.write("\n")

    def read_all(self) -> list[CaseAuditEvent]:
        if not self.path.exists():
            return []
        events: list[CaseAuditEvent] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                events.append(CaseAuditEvent.model_validate_json(text))
        return events

    def read_case(self, case_id: str) -> list[CaseAuditEvent]:
        return [event for event in self.read_all() if event.case_id == case_id]


def replay_case_state(events: list[CaseAuditEvent]) -> dict[str, Any]:
    """Fold audit events into a latest-state dictionary."""

    state: dict[str, Any] = {}
    for event in events:
        state.setdefault("case_id", event.case_id)
        state.setdefault("run_id", event.run_id)
        state["last_event_type"] = event.event_type
        state["last_timestamp"] = event.timestamp
        if isinstance(event.payload, dict):
            for key, value in event.payload.items():
                state[key] = value
    return state

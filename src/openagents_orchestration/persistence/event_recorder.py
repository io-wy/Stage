"""EventRecorder — append-only JSONL mutation log.

Writes every StateBoard mutation as a JSON line.  Sequential seq numbers
enable replay from any snapshot point.  Buffer + flush for efficiency.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class EventRecorder:
    """Append-only JSONL recorder for StateBoard mutation events.

    Each line: {"seq": N, "type": "task.updated", ...payload, "ts": float}
    Seq numbers are monotonic within a session.
    """

    def __init__(
        self,
        session_dir: Path,
        session_id: str,
        *,
        flush_threshold: int = 10,
    ):
        self._session_dir = Path(session_dir)
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._session_id = session_id
        self._file = self._session_dir / "events.jsonl"
        self._buffer: list[dict[str, Any]] = []
        self._flush_threshold = flush_threshold
        self._seq = self._load_max_seq()

    # -- write API -----------------------------------------------------------

    def append(self, event_type: str, **payload: Any) -> int:
        """Append a mutation event. Returns the assigned seq number."""
        self._seq += 1
        entry = {
            "seq": self._seq,
            "type": event_type,
            "ts": time.time(),
            **payload,
        }
        self._buffer.append(entry)
        if len(self._buffer) >= self._flush_threshold:
            self.flush()
        return self._seq

    def flush(self) -> None:
        """Write buffered events to disk."""
        if not self._buffer:
            return
        lines = []
        for entry in self._buffer:
            try:
                lines.append(json.dumps(entry, ensure_ascii=False, default=str))
            except (TypeError, ValueError):
                # Skip unserializable entries — log_event should only contain
                # primitive data, but guard against stray objects.
                continue
        if lines:
            with self._file.open("a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        self._buffer.clear()

    def close(self) -> None:
        """Flush remaining buffer and close resources."""
        self.flush()

    # -- read API ------------------------------------------------------------

    def events_after(self, seq: int) -> list[dict[str, Any]]:
        """Read all events with seq > given seq number."""
        if not self._file.exists():
            return []
        results: list[dict[str, Any]] = []
        with self._file.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("seq", 0) > seq:
                    results.append(entry)
        return results

    def all_events(self) -> list[dict[str, Any]]:
        """Read all events from disk."""
        return self.events_after(0)

    # -- helpers -------------------------------------------------------------

    def _load_max_seq(self) -> int:
        """Scan existing file to find highest seq number."""
        if not self._file.exists():
            return 0
        max_seq = 0
        with self._file.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                max_seq = max(max_seq, entry.get("seq", 0))
        return max_seq

    @property
    def next_seq(self) -> int:
        return self._seq + 1

"""SessionResumer — load and resume orchestrator sessions from disk.

MVP scope:
- Load the latest StateBoard snapshot
- Replay events after the snapshot to reconstruct current state
- Detect interruption type from the last event
- Provide resident transcript paths for restoration

Full auto-resume (reconstruct running agents, rebuild conversation context)
is intentionally out of MVP scope — it requires re-running the Director
loop from the recovered state.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openagents_orchestration.persistence.event_recorder import EventRecorder
from openagents_orchestration.persistence.state_snapshotter import StateSnapshotter


class ResumeResult:
    """Result of loading a persisted session."""

    def __init__(
        self,
        *,
        session_id: str,
        snapshot: dict[str, Any] | None,
        snapshot_seq: int,
        events_after: list[dict[str, Any]],
        resident_paths: list[Path],
        interruption: str | None = None,
    ):
        self.session_id = session_id
        self.snapshot = snapshot
        self.snapshot_seq = snapshot_seq
        self.events_after = events_after
        self.resident_paths = resident_paths
        self.interruption = interruption  # "mid_tool" | "interrupted_prompt" | "completed" | None


class SessionResumer:
    """Load persisted session state for manual or automatic recovery."""

    def __init__(self, persist_dir: Path):
        self._persist_dir = Path(persist_dir)

    # -- load API ------------------------------------------------------------

    def load(self, session_id: str) -> ResumeResult:
        """Load a session's persisted state.

        Returns the latest snapshot + events that occurred after it.
        Does NOT reconstruct running agents or restart the Director loop —
        that is the caller's responsibility.
        """
        session_dir = self._persist_dir / session_id
        if not session_dir.exists():
            return ResumeResult(
                session_id=session_id,
                snapshot=None,
                snapshot_seq=0,
                events_after=[],
                resident_paths=[],
                interruption=None,
            )

        # 1. Find latest snapshot
        snapshotter = StateSnapshotter(session_dir / "snapshots")
        snapshot_path = snapshotter.latest_snapshot()
        snapshot: dict[str, Any] | None = None
        snapshot_seq = 0

        if snapshot_path is not None:
            snapshot = snapshotter.load_snapshot(snapshot_path)
            # Extract seq from filename: stateboard-{seq}.json
            try:
                snapshot_seq = int(snapshot_path.stem.split("-", 1)[1])
            except (ValueError, IndexError):
                snapshot_seq = 0

        # 2. Read events after snapshot
        recorder = EventRecorder(session_dir, session_id)
        events_after = recorder.events_after(snapshot_seq)

        # 3. Find resident transcript files
        residents_dir = session_dir / "residents"
        resident_paths = list(residents_dir.glob("*.json")) if residents_dir.exists() else []

        # 4. Detect interruption from last event
        interruption = self._detect_interruption(events_after)

        return ResumeResult(
            session_id=session_id,
            snapshot=snapshot,
            snapshot_seq=snapshot_seq,
            events_after=events_after,
            resident_paths=resident_paths,
            interruption=interruption,
        )

    def list_sessions(self) -> list[dict[str, Any]]:
        """List all persisted sessions with basic metadata."""
        if not self._persist_dir.exists():
            return []
        results: list[dict[str, Any]] = []
        for session_dir in sorted(self._persist_dir.iterdir()):
            if not session_dir.is_dir():
                continue
            # Try to read the latest snapshot for metadata
            snapshotter = StateSnapshotter(session_dir / "snapshots")
            latest = snapshotter.latest_snapshot()
            meta = {
                "session_id": session_dir.name,
                "has_snapshot": latest is not None,
            }
            # Check for events file
            events_file = session_dir / "events.jsonl"
            meta["has_events"] = events_file.exists()
            if events_file.exists():
                try:
                    meta["events_size"] = events_file.stat().st_size
                except OSError:
                    pass
            results.append(meta)
        return results

    # -- internal ------------------------------------------------------------

    @staticmethod
    def _detect_interruption(events: list[dict[str, Any]]) -> str | None:
        """Detect what kind of interruption occurred based on last events.

        Returns:
            - "mid_tool": last event suggests a tool call without result
            - "interrupted_prompt": human asked something, no response yet
            - "completed": looks like normal completion
            - None: not enough info
        """
        if not events:
            return None

        # Look at last 3 events for context
        recent = events[-3:]
        types = [e.get("type", "") for e in recent]

        # If the last event is a task/agent running, we might be mid-execution
        if types[-1] in ("task.running", "agent.running"):
            return "mid_tool"

        # If last event is human.asked without human.replied, interrupted prompt
        if types[-1] == "human.asked":
            # Check if there's a reply after
            return "interrupted_prompt"

        # If everything is terminal, completed
        terminal_types = {"task.completed", "task.failed", "task.skipped", "agent.done"}
        if types[-1] in terminal_types:
            return "completed"

        return None

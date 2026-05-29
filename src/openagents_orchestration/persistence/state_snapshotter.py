"""StateSnapshotter — periodic full snapshots of StateBoard.

Every N mutations, dumps the full board state as JSON.
Enables fast recovery without replaying the entire event log.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class StateSnapshotter:
    """Periodic StateBoard snapshots with cleanup.

    Snapshots are named ``stateboard-{seq}.json`` where ``seq`` is the
    event sequence number at which the snapshot was taken.  A companion
    ``stateboard-{seq}.meta.json`` records metadata (timestamp, event seq).
    """

    def __init__(
        self,
        snapshot_dir: Path,
        *,
        interval: int = 20,
        keep_last: int = 3,
    ):
        self._dir = Path(snapshot_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._interval = interval
        self._keep_last = keep_last
        self._mutation_count = 0

    # -- snapshot API --------------------------------------------------------

    def on_mutation(self, board: Any) -> dict[str, Any] | None:
        """Call after every StateBoard mutation. Returns snapshot info if taken."""
        self._mutation_count += 1
        if self._mutation_count % self._interval != 0:
            return None
        return self._save_snapshot(board)

    def force_snapshot(self, board: Any, *, seq: int = 0) -> dict[str, Any]:
        """Force an immediate snapshot."""
        return self._save_snapshot(board, seq=seq)

    # -- query API -----------------------------------------------------------

    def latest_snapshot(self) -> Path | None:
        """Return the path to the most recent snapshot, or None."""
        def _extract_seq(p: Path) -> int:
            try:
                return int(p.stem.split("-", 1)[1])
            except (ValueError, IndexError):
                return 0

        candidates = sorted(
            self._dir.glob("stateboard-*.json"),
            key=_extract_seq,
            reverse=True,
        )
        for cand in candidates:
            # Skip meta files
            if cand.name.endswith(".meta.json"):
                continue
            if cand.exists():
                return cand
        return None

    def load_snapshot(self, path: Path) -> dict[str, Any] | None:
        """Load a snapshot file. Returns None on error."""
        try:
            with path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None

    # -- internal ------------------------------------------------------------

    def _save_snapshot(self, board: Any, *, seq: int | None = None) -> dict[str, Any]:
        snapshot_seq = seq if seq is not None else self._mutation_count
        snapshot_path = self._dir / f"stateboard-{snapshot_seq}.json"
        meta_path = self._dir / f"stateboard-{snapshot_seq}.meta.json"

        snapshot = board.to_dict() if hasattr(board, "to_dict") else board.snapshot()
        ts = time.time()

        with snapshot_path.open("w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, ensure_ascii=False, indent=2, default=str)

        meta = {
            "seq": snapshot_seq,
            "ts": ts,
            "event_count": self._mutation_count,
        }
        with meta_path.open("w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)

        self._cleanup_old_snapshots()

        return {
            "path": str(snapshot_path),
            "seq": snapshot_seq,
            "ts": ts,
        }

    def _cleanup_old_snapshots(self) -> None:
        """Remove old snapshots, keeping only the last N."""
        candidates = sorted(
            self._dir.glob("stateboard-*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        # Filter out meta files
        candidates = [c for c in candidates if not c.name.endswith(".meta.json")]
        to_remove = candidates[self._keep_last:]
        for path in to_remove:
            try:
                path.unlink()
                meta = path.with_suffix(".json.meta.json")
                if meta.exists():
                    meta.unlink()
            except OSError:
                pass

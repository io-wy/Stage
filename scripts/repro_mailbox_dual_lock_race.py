"""Repro: InMemoryMailbox uses asyncio.Lock for async path and threading.Lock
for sync path.  Because the two locks are independent, an async enqueue and a
sync peek can race on the internal _buffer/_seq structures.

This script runs many interleavings and reports any observed inconsistency.

EMPIRICAL RESULT (Python 3.11.15): NO inconsistency observed across 10k
enqueues vs a tight sync-peek loop.  The individual buffer ops (list.append,
slice, len) are GIL-atomic, so the independent locks do not produce observable
corruption for the CURRENT operations.  Verdict: theoretical smell, not a
demonstrated bug.  A real race would need a non-atomic read-modify-write on the
buffer split across the two lock domains.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class StructuredMessage:
    msg_id: str

    def to_dict(self) -> dict[str, str]:
        return {"msg_id": self.msg_id}


class InMemoryMailbox:
    """Minimal reproduction of the locking strategy from mailbox/memory.py."""

    def __init__(self) -> None:
        self._buffer: list[tuple[int, int, StructuredMessage]] = []
        self._seq = 0
        self._lock = asyncio.Lock()
        self._sync_lock = threading.Lock()

    async def enqueue(self, msg: StructuredMessage) -> None:
        async with self._lock:
            self._seq += 1
            self._buffer.append((0, self._seq, msg))

    def _sync_peek(self, limit: int = 5) -> list[StructuredMessage]:
        with self._sync_lock:
            # Snapshot while (maybe) an async enqueue is half-done.
            snapshot = self._buffer[:limit]
            return [item[2] for item in snapshot]

    def size(self) -> int:
        # Intentionally unsynchronized read to catch torn updates.
        return len(self._buffer)


async def main() -> None:
    mailbox = InMemoryMailbox()
    stop = threading.Event()
    inconsistencies: list[str] = []

    def sync_peeker() -> None:
        while not stop.is_set():
            peeked = mailbox._sync_peek(limit=1000)
            size = mailbox.size()
            if len(peeked) != size:
                inconsistencies.append(
                    f"peek={len(peeked)} size={size}"
                )
            time.sleep(0.0001)

    async def async_enqueuer() -> None:
        for i in range(10_000):
            await mailbox.enqueue(StructuredMessage(msg_id=f"msg-{i}"))

    thread = threading.Thread(target=sync_peeker)
    thread.start()

    await async_enqueuer()

    stop.set()
    thread.join()

    if inconsistencies:
        print(f"RACE OBSERVED: {len(inconsistencies)} inconsistency events")
        print(inconsistencies[:10])
    else:
        print("No inconsistency observed in this run (does not prove safety).")


if __name__ == "__main__":
    asyncio.run(main())

"""Repro: synchronous blocking tool invokes are even worse than async zombies.

This variant uses time.sleep() inside _orig to model subprocess.run(...)
without a timeout.  Because the blocking call never yields to asyncio, the
worker thread cannot even poll a shutdown Event; only a real process-level
action (subprocess timeout, socket timeout, SIGKILL) can recover it.

EMPIRICAL RESULT (Python 3.11.15):
  - 8 sync-blocking calls with 1s SDK timeout into a 4-thread pool.
  - All 8 return TIMEOUT.
  - Only the first 4 workers can start; the other 4 never get a thread.
  - 0 workers finish (they are stuck in time.sleep).
  - The script can escape because it owns the executor and can force shutdown.
    Production code does NOT own asyncio's default executor that way.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import time
from dataclasses import dataclass, field


@dataclass
class ToolState:
    worker_started: set[int] = field(default_factory=set)
    worker_finished: set[int] = field(default_factory=set)


def _sync_blocking_orig(call_no: int, state: ToolState) -> str:
    """Models a truly synchronous blocking tool (e.g. subprocess.run w/o timeout)."""
    state.worker_started.add(call_no)
    try:
        time.sleep(60)  # simulates a long/hung sync call; 60s is enough to see the leak
    finally:
        state.worker_finished.add(call_no)
    return "never"


def _sync_runner(call_no: int, state: ToolState) -> str:
    return asyncio.run(_async_wrapper(call_no, state))


async def _async_wrapper(call_no: int, state: ToolState) -> str:
    """Original invoke is async, but it immediately delegates to sync blocking."""
    return _sync_blocking_orig(call_no, state)


async def _thread_safe_invoke(call_no: int, state: ToolState) -> str:
    return await asyncio.to_thread(_sync_runner, call_no, state)


async def main() -> None:
    state = ToolState()
    loop = asyncio.get_running_loop()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
    loop.set_default_executor(executor)

    print("Sending 8 sync-blocking tool calls with 1s SDK timeout into 4 threads.")
    tasks = [asyncio.wait_for(_thread_safe_invoke(i + 1, state), timeout=1.0) for i in range(8)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    timeouts = sum(1 for r in results if isinstance(r, asyncio.TimeoutError))
    print(f"Timeouts returned: {timeouts}/{len(results)}")

    # Only the first 4 workers can ever start; the rest starve.
    started = sum(1 for i in range(1, 9) if i in state.worker_started)
    print(f"Workers that actually started: {started}/{len(results)}")
    print(f"Workers finished: {len(state.worker_finished)}")

    # If we don't force-exit the executor, Python waits for 999s sleeps.
    print("\nForcing executor shutdown now (in production you would be stuck).")
    executor.shutdown(wait=False, cancel_futures=True)
    print("Shutdown requested; process can now exit.")


if __name__ == "__main__":
    asyncio.run(main())

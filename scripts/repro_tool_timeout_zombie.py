"""Repro: SDK timeout cannot kill a worker thread, so a blocking tool invoke
becomes a zombie that occupies the asyncio thread pool.

Recreates the exact call chain from OrchestratorRunner._make_thread_safe_invoke:

    SDK wait_for(_thread_safe_invoke(...), timeout=T)
        _thread_safe_invoke
            await asyncio.to_thread(_sync_runner)
                _sync_runner()
                    return asyncio.run(_orig(params, ctx))   # in worker thread

When wait_for cancels at T, it only pops off the await on to_thread.
The worker thread keeps running asyncio.run(_orig(...)) until _orig itself
returns.  If _orig is a blocking operation without its own timeout, the thread
is leaked.

EMPIRICAL RESULTS (Python 3.11.15, this repo):
  - Single 1s timeout: SDK returns TIMEOUT, worker thread survives.
  - Flood 12 calls into a 4-thread pool: first 4 threads become zombies and
    SATURATE the pool; remaining 8 never even start; a subsequent "fast" tool
    call times out because no worker thread is free.
  - Cleanup in this script uses a threading.Event so the process can exit.  In
    production a hung subprocess has NO such signal; the process never exits.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
from dataclasses import dataclass, field


@dataclass
class ToolState:
    """Shared mutable state observed by the main loop and worker threads."""
    worker_started: set[int] = field(default_factory=set)
    worker_finished: set[int] = field(default_factory=set)
    shutdown: threading.Event = field(default_factory=threading.Event)


async def _orig_zombie(params: dict, ctx: dict, state: ToolState) -> str:
    """Simulates a tool invoke that blocks until shutdown in the worker thread."""
    tid = threading.get_ident()
    call_no: int = params["call_no"]
    state.worker_started.add(call_no)
    try:
        # Models a blocking operation without its own timeout (e.g. a hung
        # subprocess or a sync network call that never returns).  We poll a
        # threading.Event so the repro can exit; in production there is no
        # such shutdown signal.
        while not state.shutdown.is_set():
            await asyncio.sleep(0.05)
    finally:
        state.worker_finished.add(call_no)


def _sync_runner(call_no: int, state: ToolState) -> str:
    """Mirrors runner._make_thread_safe_invoke's _sync_runner closure."""
    return asyncio.run(_orig_zombie({"call_no": call_no}, {}, state))


async def _thread_safe_invoke(call_no: int, state: ToolState) -> str:
    """Mirrors the wrapper produced by runner._make_thread_safe_invoke."""
    return await asyncio.to_thread(_sync_runner, call_no, state)


async def _sdk_wait_for_tool(call_no: int, state: ToolState, timeout: float) -> str:
    """Mirrors SDK SafeToolExecutor calling wait_for(tool.invoke(...))."""
    try:
        return await asyncio.wait_for(
            _thread_safe_invoke(call_no, state),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return "TIMEOUT"


async def main() -> None:
    state = ToolState()

    print("=" * 64)
    print("Single call with 1s timeout, worker blocks forever")
    print("=" * 64)

    result = await _sdk_wait_for_tool(0, state, timeout=1.0)
    print(f"SDK returned: {result}")
    print(f"Worker reported started: {0 in state.worker_started}")

    # Give the cancellation a moment to propagate and see if the thread dies.
    await asyncio.sleep(0.5)
    print(f"Worker reported finished after 0.5s: {0 in state.worker_finished}")

    if 0 in state.worker_started and 0 not in state.worker_finished:
        print("LEAK CONFIRMED: worker thread survived the SDK timeout")
    else:
        print("OK: worker thread terminated after timeout")

    print()
    print("=" * 64)
    print("Flood: default thread pool is ~min(32, cpu+4).  Spawn many zombies.")
    print("=" * 64)

    loop = asyncio.get_running_loop()
    # Ensure we can inspect the default executor's thread usage.
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
    loop.set_default_executor(executor)

    max_workers = executor._max_workers
    print(f"Controlled thread pool size: {max_workers}")

    flood_tasks = [
        _sdk_wait_for_tool(i + 1, state, timeout=1.0)
        for i in range(max_workers * 3)
    ]
    start = time.monotonic()
    results = await asyncio.gather(*flood_tasks)
    elapsed = time.monotonic() - start

    timeouts = results.count("TIMEOUT")
    print(f"Flood completed in {elapsed:.2f}s; timeouts={timeouts}/{len(results)}")

    # Count how many workers are still alive.
    alive = {i for i in range(1, max_workers * 3 + 1) if i in state.worker_started and i not in state.worker_finished}
    print(f"Zombie workers still running: {len(alive)} (of {len(results)} calls)")
    if alive:
        print(f"Sample zombie call numbers: {sorted(alive)[:10]}")

    # Now try one more normal (fast) tool call.  Because the pool is full of
    # zombies, it cannot start a new thread until a zombie finally exits.
    print()
    print("=" * 64)
    print("Fast tool call after flood (should be instant, but may wait)")
    print("=" * 64)

    async def _fast_tool() -> str:
        return await asyncio.to_thread(lambda: "fast")

    start = time.monotonic()
    try:
        fast_result = await asyncio.wait_for(_fast_tool(), timeout=2.0)
        print(f"Fast tool returned in {time.monotonic() - start:.2f}s: {fast_result!r}")
    except asyncio.TimeoutError:
        print(f"Fast tool TIMED OUT after 2s (thread pool saturated by zombies)")

    print()
    print("Cleaning up: forcing all zombies to finish for process exit...")
    state.shutdown.set()
    # Give zombies enough polling cycles to observe shutdown.
    await asyncio.sleep(0.3)
    alive_after_cleanup = {i for i in range(1, max_workers * 3 + 1) if i in state.worker_started and i not in state.worker_finished}
    print(f"Zombies still running after cleanup: {len(alive_after_cleanup)}")


if __name__ == "__main__":
    asyncio.run(main())
    print("main() exited; any still-running threads will keep the process alive.")

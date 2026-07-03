"""Repro: does a worker-thread loop (L2) touching a main-loop (L1) asyncio
primitive actually raise?  Instrumented to show WHY.

Mirrors runner._make_thread_safe_invoke: original invoke runs inside
asyncio.run() in a worker thread.

EMPIRICAL RESULTS (Python 3.11.15, this repo):
  - PRODUCTION default (debug off): NO error.  A Queue.put from L2 reaches an
    L1-parked getter; the callback runs on L1's thread.  It "works" only because
    BaseEventLoop.call_soon skips _check_thread() unless loop._debug is True, so
    the cross-thread scheduling is silently tolerated (non-thread-safe, but the
    main loop is parked in to_thread and gets woken when the worker returns).
  - asyncio DEBUG mode (PYTHONASYNCIODEBUG=1 / loop.set_debug(True)): the same
    code HANGS/deadlocks — _check_thread() raises "Non-thread-safe operation
    invoked on an event loop other than the current one" inside the worker,
    the L1 getter never resolves, and the run wedges.
  => Concern is NOT a hard prod crash, but a latent non-thread-safe landmine +
     a debug-mode footgun.  See /tmp/probe_debug_check.py for the isolated
     call_soon mechanism.
"""

from __future__ import annotations

import asyncio
import threading


def _wrapped_tool(coro_factory):
    """What runner._make_thread_safe_invoke does around a tool invoke."""

    def _sync_runner():
        return asyncio.run(coro_factory())

    return asyncio.to_thread(_sync_runner)


async def case_queue_pending_getter() -> None:
    """Queue bound to L1 via a pending getter (resident inbox pattern),
    then a wrapped tool on L2 pushes to it."""
    main_tid = threading.get_ident()
    queue: asyncio.Queue[str] = asyncio.Queue()

    getter = asyncio.ensure_future(queue.get())
    await asyncio.sleep(0.01)  # ensure the getter actually parks
    print(f"[Queue] main thread={main_tid}, parked getters={len(queue._getters)}")

    async def tool_invoke() -> str:
        worker_tid = threading.get_ident()
        print(f"[Queue] worker thread={worker_tid}, loop running={id(asyncio.get_running_loop())}")
        try:
            queue.put_nowait("from L2")   # wakes the L1-bound getter from L2
            print("[Queue] put_nowait returned WITHOUT error")
        except RuntimeError as exc:
            print(f"[Queue] put_nowait raised: {type(exc).__name__}: {exc}")
            raise
        return "pushed"

    try:
        result = await _wrapped_tool(tool_invoke)
        print(f"[Queue] wrapped tool result: {result!r}")
    except RuntimeError as exc:
        print(f"[Queue] BOMB propagated: {type(exc).__name__}: {exc}")
    finally:
        # See what the getter observed back on L1.
        await asyncio.sleep(0.01)
        if getter.done():
            try:
                print(f"[Queue] getter resolved to: {getter.result()!r}")
            except Exception as exc:  # noqa: BLE001
                print(f"[Queue] getter errored: {type(exc).__name__}: {exc}")
        else:
            print("[Queue] getter STILL PENDING (wakeup was lost across loops)")
            getter.cancel()


async def case_lock_held_on_main() -> None:
    """Lock acquired on L1, then a wrapped tool on L2 tries to acquire it."""
    lock = asyncio.Lock()
    await lock.acquire()  # bind + hold on L1
    print(f"[Lock] locked on L1, locked={lock.locked()}")

    async def tool_invoke() -> str:
        try:
            await asyncio.wait_for(lock.acquire(), timeout=0.2)
            return "acquired"
        except asyncio.TimeoutError:
            return "timeout (contended, no bomb)"
        except RuntimeError as exc:
            print(f"[Lock] acquire raised: {type(exc).__name__}: {exc}")
            raise

    try:
        result = await _wrapped_tool(tool_invoke)
        print(f"[Lock] wrapped tool result: {result!r}")
    except RuntimeError as exc:
        print(f"[Lock] BOMB propagated: {type(exc).__name__}: {exc}")
    finally:
        lock.release()


async def main() -> None:
    await case_queue_pending_getter()
    print("-" * 60)
    await case_lock_held_on_main()


if __name__ == "__main__":
    asyncio.run(main())

"""verify hook 测试 —— 确定性完成度核验（默认关，director 按需开）。

不打真实 LLM：用轻量 FakeRunner 桩掉 `_run_resident_single`（verifier 的返回），
真实 StateBoard 跑状态流转。
"""

from __future__ import annotations

from types import SimpleNamespace

from openagents_orchestration.core.state_board import StateBoard
from openagents_orchestration.hooks import HookManager, VerifyHooks
from openagents_orchestration.models.pattern import PatternOutcomeStatus
from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus


class FakeRunner:
    """桩 runner：记录 verifier 是否被调，返回固定 verdict。"""

    def __init__(self, board: StateBoard, verdict: str = "VERDICT: PASS\nREASON: ok"):
        self._state_board = board
        self._verdict = verdict
        self.verifier_called = False

    async def _run_resident_single(self, *, resident_id, agent_type, input_text, transcript):
        self.verifier_called = True
        return SimpleNamespace(final_output=self._verdict)


def _board(*, needs_verify: bool = True, expected=("x.py",)) -> StateBoard:
    board = StateBoard("obj", echo=False)
    board.add_tasks(TaskGraph(objective="obj", tasks=[
        TaskNode("t1", "do x", "coder",
                 expected_artifacts=list(expected), needs_verify=needs_verify),
    ]))
    board.update_task("t1", status=TaskStatus.COMPLETED, _force=True)
    return board


def _payload(agent_type: str = "coder", task_id: str = "t1") -> dict:
    return {
        "outcome": SimpleNamespace(status=PatternOutcomeStatus.COMPLETED, output="did x"),
        "agent_type": agent_type,
        "task_id": task_id,
    }


# -- hook async 支持 --------------------------------------------------------


async def test_arun_awaits_async_handler():
    hm = HookManager()
    calls: list[str] = []

    async def async_h(payload):
        calls.append("async")
        return payload

    def sync_h(payload):
        calls.append("sync")
        return payload

    hm.register("e", async_h)
    hm.register("e", sync_h)
    await hm.arun("e", {})
    assert calls == ["async", "sync"]  # async 被 await，sync 直接调


# -- 默认关：没 needs_verify 不触发 -----------------------------------------


async def test_verify_skips_without_needs_verify():
    board = _board(needs_verify=False)
    runner = FakeRunner(board, "VERDICT: FAIL\nREASON: x")  # 即便会 FAIL 也不该被调
    await VerifyHooks(runner).verify_after_execute(_payload())
    assert runner.verifier_called is False
    assert board.get_task("t1").status == TaskStatus.COMPLETED  # 没动


async def test_verify_skips_non_coder():
    board = _board(needs_verify=True)
    runner = FakeRunner(board, "VERDICT: FAIL")
    await VerifyHooks(runner).verify_after_execute(_payload(agent_type="reviewer"))
    assert runner.verifier_called is False  # 防递归：只验 coder
    assert board.get_task("t1").status == TaskStatus.COMPLETED


# -- needs_verify=True：PASS 保持 / FAIL 标 FIX_NEEDED ----------------------


async def test_verify_pass_keeps_completed():
    board = _board(needs_verify=True)
    runner = FakeRunner(board, "VERDICT: PASS\nREASON: looks done")
    await VerifyHooks(runner).verify_after_execute(_payload())
    assert runner.verifier_called is True
    assert board.get_task("t1").status == TaskStatus.COMPLETED


async def test_verify_fail_marks_fix_needed():
    board = _board(needs_verify=True)
    runner = FakeRunner(board, "VERDICT: FAIL\nREASON: x.py is empty")
    await VerifyHooks(runner).verify_after_execute(_payload())
    task = board.get_task("t1")
    assert task.status == TaskStatus.FIX_NEEDED
    assert "x.py is empty" in (task.error or "")


# -- 防死循环：失败达上限则接受 ----------------------------------------------


async def test_fix_attempts_cap():
    board = _board(needs_verify=True)
    task = board.get_task("t1")
    task.record_iteration("verifier", "verify_failed", "r1")
    task.record_iteration("verifier", "verify_failed", "r2")
    board.update_task("t1", status=TaskStatus.COMPLETED, _force=True)

    runner = FakeRunner(board, "VERDICT: FAIL\nREASON: still bad")
    await VerifyHooks(runner).verify_after_execute(_payload())
    # 已失败 2 次（上限），第 3 次 FAIL 接受 COMPLETED，不再 FIX_NEEDED
    assert board.get_task("t1").status == TaskStatus.COMPLETED

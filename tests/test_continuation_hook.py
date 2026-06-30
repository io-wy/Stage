"""continuation hook 测试 —— max_steps 续命（信任 + 护栏）。

不打真实 LLM：用轻量 FakeRunner 桩掉 run_agent（续命重 spawn），真实 StateBoard 跑
状态流转。验证：护栏内续命重跑；三条护栏（续命轮数 / Budget 总闸 / 死循环）任一触发
时升级 FAILED（交给 director fallback）；非 coder / 非 max_steps 跳过。
"""

from __future__ import annotations

from types import SimpleNamespace

from openagents_orchestration.core.state_board import Budget, StateBoard
from openagents_orchestration.hooks import ContinuationHooks
from openagents_orchestration.models.pattern import PatternOutcomeStatus
from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus


class FakeRunner:
    """桩 runner：记录续命 run_agent 调用；返回固定 outcome（不触发真实 after_execute 链）。"""

    def __init__(self, board: StateBoard):
        self._state_board = board
        self.spawn_calls: list[dict] = []

    async def run_agent(self, agent_type, input_text, agent_id=None, state=None):
        self.spawn_calls.append(
            {"agent_type": agent_type, "agent_id": agent_id, "input_text": input_text}
        )
        return SimpleNamespace(status=PatternOutcomeStatus.COMPLETED, output="done")


def _board(
    *,
    continuation_count: int = 0,
    max_continuations: int = 2,
    budget: Budget | None = None,
) -> StateBoard:
    board = StateBoard("obj", budget=budget, echo=False)
    board.add_tasks(TaskGraph(objective="obj", tasks=[
        TaskNode("t1", "do x", "coder", expected_artifacts=["x.py"],
                 continuation_count=continuation_count,
                 max_continuations=max_continuations),
    ]))
    board.update_task("t1", status=TaskStatus.RUNNING, _force=True)
    return board


def _payload(
    *,
    agent_type: str = "coder",
    status: PatternOutcomeStatus = PatternOutcomeStatus.MAX_STEPS,
    metadata: dict | None = None,
) -> dict:
    return {
        "outcome": SimpleNamespace(status=status, output="step budget exhausted"),
        "agent_type": agent_type,
        "agent_id": "coder-t1",
        "task_id": "t1",
        "result": SimpleNamespace(metadata=metadata or {"steps_used": 30}),
    }


# -- 护栏内：续命重跑 --------------------------------------------------------


async def test_max_steps_triggers_continuation():
    board = _board()
    runner = FakeRunner(board)
    await ContinuationHooks(runner).continuation_after_execute(_payload())
    # 续命重 spawn 一次，同 agent_id（→ 同 session → transcript 续接）
    assert len(runner.spawn_calls) == 1
    assert runner.spawn_calls[0]["agent_id"] == "coder-t1"
    assert runner.spawn_calls[0]["agent_type"] == "coder"
    assert "续命" in runner.spawn_calls[0]["input_text"]  # 续命提示注入
    # continuation_count++；task 仍 RUNNING（等续命跑完）
    task = board.get_task("t1")
    assert task.continuation_count == 1
    assert task.status == TaskStatus.RUNNING


# -- 护栏：续命轮数上限 ------------------------------------------------------


async def test_continuation_cap_exceeded():
    board = _board(continuation_count=2, max_continuations=2)  # 已达上限
    runner = FakeRunner(board)
    await ContinuationHooks(runner).continuation_after_execute(_payload())
    assert len(runner.spawn_calls) == 0  # 不续命
    task = board.get_task("t1")
    assert task.status == TaskStatus.FAILED
    assert "continuation cap" in (task.error or "")


# -- 护栏：Budget 总闸 -------------------------------------------------------


async def test_budget_exhausted_blocks_continuation():
    board = _board(budget=Budget(token_limit=100, token_used=100))  # token 耗尽
    runner = FakeRunner(board)
    await ContinuationHooks(runner).continuation_after_execute(_payload())
    assert len(runner.spawn_calls) == 0
    task = board.get_task("t1")
    assert task.status == TaskStatus.FAILED
    assert "budget" in (task.error or "")


# -- 护栏：死循环（机械空转）------------------------------------------------


async def test_dead_loop_blocks_continuation():
    board = _board()
    runner = FakeRunner(board)
    payload = _payload(metadata={"steps_used": 30, "consecutive_tool_failures": 3})
    await ContinuationHooks(runner).continuation_after_execute(payload)
    assert len(runner.spawn_calls) == 0
    task = board.get_task("t1")
    assert task.status == TaskStatus.FAILED
    assert "dead loop" in (task.error or "")


# -- 跳过：非 coder / 非 max_steps -------------------------------------------


async def test_skips_non_coder():
    board = _board()
    runner = FakeRunner(board)
    await ContinuationHooks(runner).continuation_after_execute(
        _payload(agent_type="reviewer")
    )
    assert len(runner.spawn_calls) == 0
    assert board.get_task("t1").status == TaskStatus.RUNNING  # 没动


async def test_skips_non_max_steps():
    board = _board()
    runner = FakeRunner(board)
    await ContinuationHooks(runner).continuation_after_execute(
        _payload(status=PatternOutcomeStatus.COMPLETED)
    )
    assert len(runner.spawn_calls) == 0
    assert board.get_task("t1").status == TaskStatus.RUNNING


# -- apply_outcome：max_steps 按 agent_type 解耦（防非 coder 卡 RUNNING）---------


def test_apply_outcome_coder_max_steps_keeps_running():
    """coder max_steps：task 保持 RUNNING（交 ContinuationHooks），不直接标 FAILED。"""
    board = _board()
    board.register_agent("coder-t1", "coder")
    board.apply_outcome(
        SimpleNamespace(status=PatternOutcomeStatus.MAX_STEPS, output="wip", error=None),
        task_id="t1", agent_id="coder-t1", agent_type="coder",
    )
    assert board.get_task("t1").status == TaskStatus.RUNNING


def test_apply_outcome_non_coder_max_steps_marks_failed():
    """非 coder max_steps：无续命路径，标 FAILED 避免卡 RUNNING。"""
    board = _board()
    board.register_agent("reviewer-t1", "reviewer")
    board.apply_outcome(
        SimpleNamespace(status=PatternOutcomeStatus.MAX_STEPS, output="x", error=None),
        task_id="t1", agent_id="reviewer-t1", agent_type="reviewer",
    )
    assert board.get_task("t1").status == TaskStatus.FAILED


# -- 异常兜底：续命逻辑抛异常不阻塞主流程，降级 FAILED ------------------------


async def test_continuation_exception_degrades_to_failed():
    """续命重跑抛异常时，不卡 RUNNING、不阻塞后续 hook：标 FAILED + 记 continuation.error。"""
    board = _board()

    class BoomRunner(FakeRunner):
        async def run_agent(self, agent_type, input_text, agent_id=None, state=None):
            raise RuntimeError("boom")

    await ContinuationHooks(BoomRunner(board)).continuation_after_execute(_payload())
    task = board.get_task("t1")
    assert task.status == TaskStatus.FAILED
    assert "continuation error" in (task.error or "")
    assert any(e.event_type == "continuation.error" for e in board.events)

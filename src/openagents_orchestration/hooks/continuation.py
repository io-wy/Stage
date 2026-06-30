"""ContinuationHooks — coder 耗尽 max_steps 的续命（信任 + 护栏）。

挂在 ``pattern.after_execute`` 上的 **async** handler。coder 跑满 max_steps（没干完）
后，框架在护栏内自动重 spawn 同一 task 续上——相同 agent_id → 相同 session_id →
transcript 自动续接（assemble 压缩），让 coder 接着干，而不是把「没干完」当「失败」硬停。

设计哲学（io-wy）：**信任 ReAct + 护栏**。系统不做外部进度打分（开发非线性，静态度量
会误杀探索/重构期）；只画两条护栏——资源边界 + 死循环——把「做得怎么样 / 要不要拆 /
要不要求助」还给 coder 的 ReAct。续命默认信任：给更多步接着干；护栏外才标 FAILED 升级，
交给 director 现有 fallback（replan / spawn_resident / ask_human）。

- 只续 ``agent_type == "coder"`` 且 ``outcome.status == max_steps``；verifier / director /
  subagent 一律跳过（防递归续命）。
- 三条护栏（任一触发 → 不续命、标 FAILED 升级）:
    1. 续命轮数:``continuation_count >= max_continuations``（默认 2，对齐 verify ``_MAX_FIX``）
    2. Budget 总闸:``board.budget.exhausted``（token/time/steps 任一）——防「loop 到做完」变「loop 到烧光」
    3. 死循环:结尾 ``consecutive_tool_failures`` / ``consecutive_empty_responses`` 超阈值（机械空转）
- 注册在 StateSyncHooks 之后、VerifyHooks 之前:apply_outcome 已把 max_steps 的 task
  留在 RUNNING（不当 FAILED），续命在此接管；续命跑完最终 COMPLETED 才轮到 verify。
- 续命自身失败不阻塞主流程（记 ``continuation.error``，放行）。
"""

from __future__ import annotations

import contextlib
from typing import Any

from openagents_orchestration.models.task import TaskStatus

_TOOL_FAIL_CAP = 3  # 结尾连续工具失败 ≥ 此值 → 机械卡死，不续命
_EMPTY_CAP = 2      # 结尾连续空响应 ≥ 此值 → 机械卡死，不续命


class ContinuationHooks:
    """注册到 after_execute 的 max_steps 续命 handler。持 runner 引用以重 spawn。"""

    def __init__(self, runner: Any) -> None:
        self.runner = runner

    async def continuation_after_execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        board = getattr(self.runner, "_state_board", None)
        if board is None:
            return payload

        outcome = payload.get("outcome")
        agent_type = payload.get("agent_type")
        agent_id = payload.get("agent_id")
        task_id = payload.get("task_id")
        status = getattr(getattr(outcome, "status", None), "value", None)

        # 只续产出型 coder 的 max_steps；verifier / director / subagent 一律跳过。
        if agent_type != "coder" or status != "max_steps" or not task_id:
            return payload
        task = board.get_task(task_id)
        if task is None or task.is_terminal():
            return payload

        # 续命决策/重跑的任何异常都不得阻塞 after_execute 链（否则后续 VerifyHooks 不跑、
        # 且 task 卡在 RUNNING）。异常 → 记事件 + 标 FAILED 升级 director fallback（X-07）。
        try:
            await self._continue_or_escalate(task, task_id, agent_id, board, payload)
        except Exception as exc:
            board.log_event("continuation.error", task_id=task_id, message=str(exc))
            with contextlib.suppress(Exception):
                board.update_task(
                    task_id,
                    status=TaskStatus.FAILED,
                    error=f"continuation error: {exc}",
                    _force=True,
                )
        return payload

    async def _continue_or_escalate(
        self, task: Any, task_id: str, agent_id: Any, board: Any, payload: dict[str, Any]
    ) -> None:
        """护栏检查 → 续命重跑 / 升级 FAILED。异常由 continuation_after_execute 兜底。"""
        # —— 护栏：任一触发 → 不续命，标 FAILED 升级 director fallback ——
        block = self._blocked_reason(task, board, payload)
        if block is not None:
            board.update_task(
                task_id,
                status=TaskStatus.FAILED,
                error=f"step budget exhausted; {block}",
                _force=True,
            )
            board.log_event(
                "continuation.exhausted",
                task_id=task_id,
                agent_id=agent_id,
                message=f"no continuation ({block}) → FAILED, escalate to director fallback",
            )
            return

        # —— 护栏内：续命重跑（默认信任，给更多步）——
        # continuation_count 经 update_task 落账（X-02：只走 StateBoard 方法）。
        board.update_task(
            task_id,
            continuation_count=task.continuation_count + 1,
            status=TaskStatus.RUNNING,
            _force=True,
        )
        task.record_iteration(
            agent_id or "coder", "continuation", f"round {task.continuation_count}"
        )
        board.log_event(
            "continuation.spawned",
            task_id=task_id,
            agent_id=agent_id,
            message=f"max_steps → continue (round {task.continuation_count}/{task.max_continuations})",
        )
        # 同 agent_id → 同 session_id → assemble 自动 load 回上轮 transcript（压缩续接）。
        # 同一 event loop 协程嵌套；递归深度受 continuation_count 上限约束，不爆栈。
        await self.runner.run_agent(
            agent_type="coder",
            input_text=self._continuation_input(task, board),
            agent_id=agent_id,
        )

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _blocked_reason(task: Any, board: Any, payload: dict[str, Any]) -> str | None:
        """返回不续命的原因；None 表示可续命。"""
        if task.continuation_count >= task.max_continuations:
            return f"continuation cap ({task.continuation_count}/{task.max_continuations})"
        budget = getattr(board, "budget", None)
        if budget is not None and getattr(budget, "exhausted", False):
            return "budget exhausted"
        # 死循环信号来自 _run_single 正常返回 metadata（runner.py 已补 consecutive_*）。
        meta = getattr(payload.get("result"), "metadata", None) or {}
        if int(meta.get("consecutive_tool_failures", 0) or 0) >= _TOOL_FAIL_CAP:
            return f"dead loop ({meta.get('consecutive_tool_failures')} consecutive tool failures)"
        if int(meta.get("consecutive_empty_responses", 0) or 0) >= _EMPTY_CAP:
            return f"dead loop ({meta.get('consecutive_empty_responses')} consecutive empty responses)"
        return None

    @staticmethod
    def _continuation_input(task: Any, board: Any) -> str:
        """复用 spawn_agent._build_input（带 cwd / artifacts / deps）+ 续命提示。"""
        from openagents_orchestration.tools.director.spawn_agent import SpawnAgentTool

        nudge = (
            "# 续命：上一轮步数耗尽，任务尚未完成\n"
            "完整历史在上方对话中（已压缩）。复查你做到哪一步了，从断点继续——"
            "不要从头重来。把剩余产出做完、跑验证，全绿后调用 complete_task。\n"
            "若反复卡在同一处，用 ask_human 求助、或 sub_agent 拆出受阻部分。"
        )
        return SpawnAgentTool._build_input(
            task, board, agent_type="coder", extra_context=nudge
        )

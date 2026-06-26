"""VerifyHooks — 确定性完成度核验（防假完成）。

挂在 ``pattern.after_execute`` 上的 **async** handler:coder 的 task 标 COMPLETED 后,
框架自动喂一份 markdown(要求 + coder 自报 + 待核实文件)给常驻 verifier,verifier 自己
read_file/bash 核实、判完成度(PASS/FAIL),不经 director。

- 单一职责:验 + 标状态。**不在这里重跑 coder**——FAIL 只标 FIX_NEEDED + 反馈,
  重跑由上层(director 看 FIX_NEEDED / 后续确定性调度)负责。
- 防递归:只验 ``agent_type == "coder"``;verifier/director/subagent 一律跳过。
- 防死循环:``iteration_history`` 里数 verify_failed 次数,到上限(2)就接受(带警告)。
- verify 自己挂了不阻塞主流程(附加核验层,放行 + 记事件)。
"""

from __future__ import annotations

import re
from typing import Any

from openagents_orchestration.models.task import TaskStatus

_MAX_FIX = 2  # 最多让 coder 重做 2 次;第 3 次仍 FAIL 则接受


class VerifyHooks:
    """注册到 after_execute 的完成度核验 handler。持 runner 引用以同步调 verifier。"""

    def __init__(self, runner: Any) -> None:
        self.runner = runner

    async def verify_after_execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        board = getattr(self.runner, "_state_board", None)
        if board is None:
            return payload

        outcome = payload.get("outcome")
        agent_type = payload.get("agent_type")
        task_id = payload.get("task_id")
        if not self._should_verify(agent_type, outcome, board, task_id):
            return payload

        task = board.get_task(task_id)
        markdown = self._build_markdown(task, outcome)

        # 同步喂一条给常驻 verifier(bundle 缓存复用;fresh transcript 省 token)
        try:
            result = await self.runner._run_resident_single(
                resident_id=f"verifier-{task_id}",
                agent_type="verifier",
                input_text=markdown,
                transcript=[],
            )
            verdict_text = str(getattr(result, "final_output", "") or "")
        except Exception as exc:  # verify 自身失败不阻塞主流程,放行
            board.log_event("verify.error", task_id=task_id, message=str(exc))
            return payload

        passed = self._parse_verdict(verdict_text)
        reason = self._parse_reason(verdict_text)

        if passed:
            board.log_event("verify.pass", task_id=task_id, message=reason or "PASS")
            return payload

        # FAIL:数之前的失败次数(上限兜底)
        fails = sum(
            1 for h in task.iteration_history if h.get("action") == "verify_failed"
        )
        if fails < _MAX_FIX:
            task.record_iteration("verifier", "verify_failed", reason)
            board.update_task(
                task_id,
                status=TaskStatus.FIX_NEEDED,
                error=f"verify failed: {reason}",
                _force=True,
            )
            board.log_event(
                "verify.fail", task_id=task_id, message=f"FIX_NEEDED: {reason}"
            )
        else:
            # 到上限,接受 COMPLETED(别卡死交付),记警告
            board.log_event(
                "verify.cap",
                task_id=task_id,
                message=f"accepted COMPLETED after {fails} failed verifies: {reason}",
            )
        return payload

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _should_verify(
        agent_type: Any, outcome: Any, board: Any, task_id: Any
    ) -> bool:
        # 防递归:只核验产出型 coder;verifier/director/subagent(sub-) 跳过
        if agent_type != "coder":
            return False
        status = getattr(getattr(outcome, "status", None), "value", None)
        if status != "completed":
            return False
        if not task_id:
            return False
        task = board.get_task(task_id)
        if task is None or not getattr(task, "needs_verify", False):
            return False  # 默认关:只对 director 显式 spawn(verify=True) 标记的 task 核验
        # 没声明产出文件的 task 无从核验,跳过
        return bool(task.expected_artifacts)

    @staticmethod
    def _build_markdown(task: Any, outcome: Any) -> str:
        coder_report = str(getattr(outcome, "output", "") or "")[:1000]
        files = "\n".join(f"- {a}" for a in task.expected_artifacts)
        return (
            f"# 要求\n{task.description}\n\n"
            f"# coder 自报做了什么\n{coder_report}\n\n"
            f"# 待核实的产出文件(请自己用 read_file 看真在不在、内容对不对)\n{files}\n\n"
            "# 请核实并判断:做完没 / 齐不齐。严格输出两行:\n"
            "VERDICT: PASS|FAIL\nREASON: 一句话"
        )

    @staticmethod
    def _parse_verdict(text: str) -> bool:
        # 仅明确 PASS 才算通过;格式不对/缺失一律按未通过(保守防假完成,有上限兜底)
        m = re.search(r"VERDICT:\s*(PASS|FAIL)", text, re.IGNORECASE)
        return bool(m) and m.group(1).upper() == "PASS"

    @staticmethod
    def _parse_reason(text: str) -> str:
        m = re.search(r"REASON:\s*(.+)", text, re.IGNORECASE)
        return m.group(1).strip()[:200] if m else ""

"""自建评估 Harness.

从 YAML 文件加载自定义任务，测试戏台编排能力.
集成 Agent-as-Judge（Claude Code CLI）做主观评估.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import yaml

from eval.base import EvalHarness, EvalResult, EvalTask, WorkDirSetup, verify_task
from eval.judge import ClaudeCodeJudge


class CustomHarness(EvalHarness):
    """自建评估 Harness — 完整 7 维评估."""

    name = "custom"

    def __init__(
        self,
        work_dir: Path,
        config_path: str | Path = "agent.json",
        tasks_dir: str | Path | None = None,
        limit: int | None = None,
        skip_judge: bool = False,
    ):
        super().__init__(work_dir, config_path)
        self.tasks_dir = (
            Path(tasks_dir) if tasks_dir else Path(__file__).parent / "tasks"
        )
        self._limit = limit
        self._skip_judge = skip_judge
        self._judge: ClaudeCodeJudge | None = None
        if not skip_judge:
            self._judge = ClaudeCodeJudge()

    def load_tasks(self, limit: int | None = None) -> list[EvalTask]:
        lim = limit if limit is not None else self._limit
        tasks = []

        if not self.tasks_dir.exists():
            return tasks

        for yaml_file in sorted(self.tasks_dir.rglob("*.yaml")):
            with open(yaml_file, encoding="utf-8") as f:
                data = yaml.safe_load(f)

            tasks.append(
                EvalTask(
                    task_id=data["id"],
                    category=data.get("category", "custom"),
                    difficulty=data.get("difficulty", "medium"),
                    description=data["description"],
                    initial_files=data.get("initial_files", {}),
                    initial_dirs=data.get("initial_dirs", []),
                    verification=data.get("verification", []),
                    expected_graph=data.get("expected_graph"),
                    max_steps=data.get("max_steps", 20),
                    max_tokens=data.get("max_tokens", 50_000),
                    timeout_sec=data.get("timeout_sec", 300),
                )
            )

            if lim is not None and len(tasks) >= lim:
                break

        return tasks

    async def run_task(self, task: EvalTask) -> EvalResult:

        start = time.monotonic()
        try:
            with WorkDirSetup(self.work_dir, task.task_id).with_task(task) as work_path:
                from openagents_orchestration.models.task import TaskStatus

                # 共享方法：启动 runner 并收集资源消耗
                (
                    report,
                    board,
                    steps,
                    tokens,
                    budget_exceeded,
                ) = await self._run_orchestrator(task, work_path)

            # ---- 客观指标收集 ------------------------------------------------

            # 1. 验证规则通过率
            verify_errors: dict[str, str] = {}
            verify_scores = verify_task(
                work_path, task.verification, errors_out=verify_errors
            )
            verify_pass_rate = (
                sum(verify_scores.values()) / len(verify_scores)
                if verify_scores
                else 0.0
            )

            # 2. Token 效率 — 共享方法
            token_efficiency = self.compute_token_efficiency(task, steps, tokens)

            # 3. 编排质量 — 客观部分 (graph_jaccard)
            orchestration_obj = 0.0
            if task.expected_graph and board:
                orchestration_obj = self._graph_jaccard(task.expected_graph, board)

            # 4. 协作成功率 — 客观部分
            collaboration_obj = 0.0
            if board:
                review_approved = sum(
                    1
                    for e in board.events
                    if "reviewer_approved" in str(getattr(e, "message", ""))
                )
                review_spawned = sum(
                    1
                    for e in board.events
                    if "spawned_reviewer" in str(getattr(e, "message", ""))
                )
                if review_spawned > 0:
                    collaboration_obj = review_approved / review_spawned
                else:
                    # 排除 director/monitor，只看战术 agent
                    tactical = [
                        a
                        for a in board.agents.values()
                        if getattr(a, "agent_type", "") not in ("director", "monitor")
                    ]
                    # 有多战术 agent 但无 reviewer 闭环则 0.3，单战术 agent 不应惩罚无必要协作
                    collaboration_obj = 0.3 if len(tactical) > 1 else 0.8

            # 5. 恢复率
            recovery_rate = 0.0
            if board:
                total_failed = sum(
                    1 for t in board.tasks.values() if t.status == TaskStatus.FAILED
                )
                # 从 events 中找 FAILED -> COMPLETED 的转换
                recovered = 0
                for task_id in board.tasks:
                    failed_seen = False
                    for e in board.events:
                        if getattr(e, "task_id", "") == task_id:
                            et = getattr(e, "event_type", "")
                            if "failed" in et:
                                failed_seen = True
                            elif failed_seen and "completed" in et:
                                recovered += 1
                                break
                recovery_rate = min(recovered / max(total_failed, 1), 1.0)

            # 6. 产出质量 — 纯主观，客观部分暂设为 0
            output_quality = 0.0

            # 7. 自治度 — 共享方法
            autonomy = self.compute_autonomy(board)

            duration = time.monotonic() - start

            # ---- 主观指标 (Agent-as-Judge) ----------------------------------

            judge_error = None
            judge_cost_usd = None

            if not self._skip_judge and self._judge is not None:
                judge_result = await self._judge.evaluate(
                    task_description=task.description,
                    state_board=board,
                    work_dir=work_path,
                    verify_scores=verify_scores,
                )

                if judge_result.get("error"):
                    judge_error = judge_result["error"]
                    print(f"  [JUDGE WARNING] {judge_error}")
                    # 主观部分填 0，客观部分照常
                    task_success = verify_pass_rate * 0.5
                    orchestration_quality = orchestration_obj * 0.4
                    collaboration_success = collaboration_obj * 0.5
                    output_quality = 0.0
                else:
                    # 组合客观 + 主观
                    task_success = (
                        verify_pass_rate * 0.5 + judge_result["fulfillment_score"] * 0.5
                    )
                    orchestration_quality = (
                        orchestration_obj * 0.4
                        + judge_result["decomposition_score"] * 0.6
                    )
                    collaboration_success = (
                        collaboration_obj
                        * judge_result["collaboration_feedback_quality"]
                    )
                    output_quality = judge_result["output_quality_score"]
                    judge_cost_usd = judge_result.get("cost_usd")
            else:
                # skip judge — 只用客观部分
                task_success = verify_pass_rate
                orchestration_quality = orchestration_obj
                collaboration_success = collaboration_obj
                output_quality = 0.0

            return EvalResult(
                task_id=task.task_id,
                category=task.category,
                difficulty=task.difficulty,
                success=task_success >= 0.8,
                passed=sum(1 for v in verify_scores.values() if v >= 0.8),
                total=len(verify_scores),
                task_success=task_success,
                token_efficiency=token_efficiency,
                orchestration_quality=orchestration_quality,
                collaboration_success=collaboration_success,
                recovery_rate=recovery_rate,
                output_quality=output_quality,
                autonomy=autonomy,
                steps_taken=steps,
                tokens_used=tokens,
                budget_exceeded=budget_exceeded,
                duration_sec=duration,
                judge_skipped=self._skip_judge,
                judge_error=judge_error,
                judge_cost_usd=judge_cost_usd,
                raw={
                    "verify_scores": verify_scores,
                    "verify_errors": verify_errors,
                    "orchestration_obj": orchestration_obj,
                    "collaboration_obj": collaboration_obj,
                },
            )

        except TimeoutError:
            return EvalResult(
                task_id=task.task_id,
                category=task.category,
                difficulty=task.difficulty,
                success=False,
                error="timeout",
                duration_sec=task.timeout_sec,
                judge_skipped=self._skip_judge,
            )
        except Exception as e:
            return EvalResult(
                task_id=task.task_id,
                category=task.category,
                difficulty=task.difficulty,
                success=False,
                error=str(e),
                duration_sec=time.monotonic() - start,
                judge_skipped=self._skip_judge,
            )

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _graph_jaccard(expected_graph: dict[str, Any], board: Any) -> float:
        """计算期望任务图 vs 实际任务图的结构相似度 (Jaccard)."""
        expected_tasks = expected_graph.get("tasks", [])
        expected_ids = {t["id"] for t in expected_tasks}
        expected_edges: set[tuple[str, str]] = set()
        for t in expected_tasks:
            for dep in t.get("dependencies", []):
                expected_edges.add((dep, t["id"]))

        actual_tasks = list(board.tasks.values())
        actual_ids = {t.task_id for t in actual_tasks}
        actual_edges: set[tuple[str, str]] = set()
        for t in actual_tasks:
            for dep in t.dependencies:
                actual_edges.add((dep, t.task_id))

        # Node Jaccard
        node_intersection = len(expected_ids & actual_ids)
        node_union = len(expected_ids | actual_ids)
        node_jaccard = node_intersection / node_union if node_union > 0 else 0.0

        # Edge Jaccard
        edge_intersection = len(expected_edges & actual_edges)
        edge_union = len(expected_edges | actual_edges)
        edge_jaccard = edge_intersection / edge_union if edge_union > 0 else 0.0

        return (node_jaccard + edge_jaccard) / 2.0

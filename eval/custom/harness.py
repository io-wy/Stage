"""自建评估 Harness.

从 YAML 文件加载自定义任务，测试戏台编排能力.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import yaml

from eval.base import EvalHarness, EvalResult, EvalTask, WorkDirSetup, verify_task


class CustomHarness(EvalHarness):
    """自建评估 Harness."""

    name = "custom"

    def __init__(
        self,
        work_dir: Path,
        config_path: str | Path = "agent.json",
        tasks_dir: str | Path | None = None,
        limit: int | None = None,
    ):
        super().__init__(work_dir, config_path)
        self.tasks_dir = Path(tasks_dir) if tasks_dir else Path(__file__).parent / "tasks"
        self._limit = limit

    def load_tasks(self, limit: int | None = None) -> list[EvalTask]:
        lim = limit if limit is not None else self._limit
        tasks = []

        if not self.tasks_dir.exists():
            return tasks

        for yaml_file in sorted(self.tasks_dir.rglob("*.yaml")):
            with open(yaml_file, encoding="utf-8") as f:
                data = yaml.safe_load(f)

            tasks.append(EvalTask(
                task_id=data["id"],
                category=data.get("category", "custom"),
                difficulty=data.get("difficulty", "medium"),
                description=data["description"],
                initial_files=data.get("initial_files", {}),
                initial_dirs=data.get("initial_dirs", []),
                verification=data.get("verification", []),
                expected_graph=data.get("expected_graph"),
                max_steps=data.get("max_steps", 20),
                max_tokens=data.get("max_tokens", 50000),
                timeout_sec=data.get("timeout_sec", 300),
            ))

            if lim is not None and len(tasks) >= lim:
                break

        return tasks

    async def run_task(self, task: EvalTask) -> EvalResult:
        import asyncio

        ws = WorkDirSetup(self.work_dir, task.task_id)
        work_path = ws.setup(task)

        start = time.monotonic()
        try:
            from openagents_orchestration.runner import OrchestratorRunner
            from openagents_orchestration.state_board import Budget

            runner = OrchestratorRunner(
                config_path=str(self.config_path),
            )

            budget = Budget(
                max_steps=task.max_steps,
                token_limit=task.max_tokens,
            )

            report = await asyncio.wait_for(
                runner.run(task.description, budget=budget, work_dir=work_path),
                timeout=task.timeout_sec,
            )

            # 收集资源消耗
            steps = runner.state_board.budget.steps_taken if runner.state_board else 0
            tokens = runner.state_board.budget.token_used if runner.state_board else 0
            budget_exceeded = runner.state_board.budget.exhausted if runner.state_board else False

            # 验证
            verify_scores = verify_task(work_path, task.verification)
            success_score = sum(verify_scores.values()) / len(verify_scores) if verify_scores else 0.0

            # 调度质量 (如果有期望的任务图)
            scheduling_score = 0.0
            if task.expected_graph and runner.state_board:
                # TODO: 从 StateBoard 提取实际任务图做对比
                scheduling_score = 0.5  # placeholder

            # 状态一致性
            state_score = 0.0
            if runner.state_board:
                artifacts = getattr(runner.state_board, "artifact_records", [])
                if artifacts:
                    verified = sum(1 for a in artifacts if getattr(a, "verified", False))
                    state_score = verified / len(artifacts)

            duration = time.monotonic() - start

            return EvalResult(
                task_id=task.task_id,
                category=task.category,
                difficulty=task.difficulty,
                success=success_score >= 0.8,
                passed=sum(1 for v in verify_scores.values() if v >= 0.8),
                total=len(verify_scores),
                success_score=success_score,
                scheduling_score=scheduling_score,
                execution_score=success_score,  # 简化: 执行质量 = 验证通过率
                efficiency_score=min(task.max_steps / max(steps, 1), 1.0) if steps > 0 else 0.0,
                state_score=state_score,
                steps_taken=steps,
                tokens_used=tokens,
                budget_exceeded=budget_exceeded,
                duration_sec=duration,
                raw={"verify_scores": verify_scores},
            )

        except asyncio.TimeoutError:
            return EvalResult(
                task_id=task.task_id,
                category=task.category,
                difficulty=task.difficulty,
                success=False,
                error="timeout",
                duration_sec=task.timeout_sec,
            )
        except Exception as e:
            return EvalResult(
                task_id=task.task_id,
                category=task.category,
                difficulty=task.difficulty,
                success=False,
                error=str(e),
                duration_sec=time.monotonic() - start,
            )
        finally:
            ws.cleanup()

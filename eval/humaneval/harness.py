"""HumanEval 评估 Harness.

把每个 HumanEval 问题作为戏台任务，评估编排效果.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from eval.base import EvalHarness, EvalResult, EvalTask, WorkDirSetup
from eval.humaneval.loader import load_humaneval
from eval.humaneval.verify import verify_humaneval_solution


class HumanEvalHarness(EvalHarness):
    """HumanEval 评估 Harness."""

    name = "humaneval"

    def __init__(
        self,
        work_dir: Path,
        config_path: str | Path = "agent.json",
        source: str | None = None,
        split: str = "test",
        limit: int | None = None,
    ):
        super().__init__(work_dir, config_path)
        self.source = source
        self.split = split
        self._limit = limit
        self._problems: list[dict[str, Any]] = []

    def load_tasks(self, limit: int | None = None) -> list[EvalTask]:
        lim = limit if limit is not None else self._limit
        self._problems = load_humaneval(source=self.source, split=self.split, limit=lim)

        tasks = []
        for prob in self._problems:
            task_id = prob.get("task_id", "unknown")
            prompt = prob.get("prompt", "")
            test = prob.get("test", "")
            entry_point = prob.get("entry_point", "")
            canonical_solution = prob.get("canonical_solution", "")

            # 难度: 基于 canonical_solution 行数简单分档
            sol_lines = (
                len(canonical_solution.strip().split("\n"))
                if canonical_solution
                else 10
            )
            if sol_lines <= 5:
                difficulty = "easy"
            elif sol_lines <= 15:
                difficulty = "medium"
            else:
                difficulty = "hard"

            # 初始文件: prompt 作为待填充的骨架
            # test 文件需要 import solution 中的函数
            test_with_import = f"from solution import {entry_point}\n\n{test}"
            initial_files = {
                "solution.py": prompt,
                "test_solution.py": test_with_import,
            }

            objective = """Complete the Python function in solution.py.

The file contains a function signature and docstring. Your task:
1. Read solution.py to understand the problem
2. Implement the function body (keep the existing signature and docstring)
3. Run tests in test_solution.py to verify your solution
4. Fix any issues until all tests pass

Do not change the function signature or the test file.
"""

            tasks.append(
                EvalTask(
                    task_id=task_id,
                    category="humaneval",
                    difficulty=difficulty,
                    description=objective,
                    initial_files=initial_files,
                    initial_dirs=["output"],
                    max_steps=20,
                    max_tokens=200000,
                    timeout_sec=300,
                    verification=[
                        {"type": "test_pass", "command": "python test_solution.py"},
                    ],
                )
            )
        return tasks

    async def run_task(self, task: EvalTask) -> EvalResult:

        start = time.monotonic()
        try:
            with WorkDirSetup(self.work_dir, task.task_id).with_task(task) as work_path:
                # 找到对应的 problem 数据
                problem = None
                for p in self._problems:
                    if p.get("task_id") == task.task_id:
                        problem = p
                        break

                # 共享方法：启动 runner
                (
                    report,
                    board,
                    steps,
                    tokens,
                    budget_exceeded,
                ) = await self._run_orchestrator(task, work_path)

                # 验证: 提取 solution.py 中的函数体，执行测试
                solution_file = work_path.resolve() / "solution.py"
                test_file = work_path.resolve() / "test_solution.py"

                # 如果戏子没改 solution.py（输出到了别的地方），尝试从常见位置找
                if not solution_file.exists():
                    for alt in [
                        "output/solution.py",
                        "src/solution.py",
                        "fixed_solution.py",
                    ]:
                        alt_path = work_path / alt
                        if alt_path.exists():
                            solution_file = alt_path
                            break

                if solution_file.exists() and test_file.exists():
                    verify_result = verify_humaneval_solution(
                        solution_file=solution_file,
                        test_file=test_file,
                        entry_point=problem.get("entry_point", "") if problem else "",
                    )
                else:
                    verify_result = {"passed": False, "reason": "missing files"}

                duration = time.monotonic() - start

                # Token efficiency — 共享方法
                token_efficiency = self.compute_token_efficiency(task, steps, tokens)

                return EvalResult(
                    task_id=task.task_id,
                    category=task.category,
                    difficulty=task.difficulty,
                    success=verify_result.get("passed", False),
                    passed=1 if verify_result.get("passed") else 0,
                    total=1,
                    task_success=1.0 if verify_result.get("passed") else 0.0,
                    token_efficiency=token_efficiency,
                    orchestration_quality=0.0,
                    collaboration_success=0.0,
                    recovery_rate=0.0,
                    output_quality=0.0,
                    autonomy=self.compute_autonomy(board),
                    steps_taken=steps,
                    tokens_used=tokens,
                    budget_exceeded=budget_exceeded,
                    duration_sec=duration,
                    judge_skipped=True,
                    raw=verify_result,
                )

        except TimeoutError:
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

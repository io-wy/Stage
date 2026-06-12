"""SWE-bench-lite 评估 Harness.

将 SWE-bench 实例转化为戏台任务，运行导演编排修复流程.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from eval.base import EvalHarness, EvalResult, EvalTask, WorkDirSetup
from eval.swe_bench_lite.loader import load_swe_bench_lite, setup_repo
from eval.swe_bench_lite.verify import extract_patch_from_work_dir, verify_instance


class SWEBenchHarness(EvalHarness):
    """SWE-bench-lite 评估 Harness."""

    name = "swe_bench_lite"

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
        self._instances: list[dict[str, Any]] = []

    def load_tasks(self, limit: int | None = None) -> list[EvalTask]:
        """加载 SWE-bench-lite 实例为 EvalTask."""
        lim = limit if limit is not None else self._limit
        self._instances = load_swe_bench_lite(
            source=self.source,
            split=self.split,
            limit=lim,
        )

        tasks = []
        for inst in self._instances:
            task_id = inst.get("instance_id", "unknown")
            # 构建 objective: issue 描述 + 上下文
            problem = inst.get("problem_statement", "").strip()
            repo = inst.get("repo", "")
            hints = inst.get("hints_text", "")

            hints_section = f"Hints:\n{hints}" if hints else ""
            objective = f"""Fix the following issue in {repo}.

Problem Statement:
{problem}

{hints_section}

Your task:
1. Understand the issue by reading the relevant code
2. Make minimal, correct changes to fix the problem
3. Ensure tests pass after your fix
4. Output the fix as a git patch or modified files in the repo/
"""

            tasks.append(
                EvalTask(
                    task_id=task_id,
                    category="swe_bench_lite",
                    difficulty="hard",
                    description=objective,
                    max_steps=50,
                    max_tokens=1000000,
                    timeout_sec=1200,
                    verification=[
                        {"type": "custom", "instance": inst},  # 由 harness 自行验证
                    ],
                )
            )
        return tasks

    async def run_task(self, task: EvalTask) -> EvalResult:
        """运行单个 SWE-bench 实例."""

        # 找到对应的原始实例数据
        instance = None
        for inst in self._instances:
            if inst.get("instance_id") == task.task_id:
                instance = inst
                break
        if instance is None:
            return EvalResult(
                task_id=task.task_id,
                category=task.category,
                difficulty=task.difficulty,
                success=False,
                error=f"instance not found for {task.task_id}",
                duration_sec=0.0,
            )

        # repo 放在全局缓存目录（按 repo 名分），跨 eval 运行复用
        repo_name = instance.get("repo", "unknown").replace("/", "_")
        repo_cache = Path(".eval_cache") / "repos" / repo_name
        repo_cache.mkdir(parents=True, exist_ok=True)

        start = time.monotonic()
        try:
            with WorkDirSetup(self.work_dir, task.task_id).with_task(task) as work_path:
                # clone repo（复用全局缓存）
                repo_dir = (
                    setup_repo(instance, repo_cache) if instance else work_path / "repo"
                )

                # 把 repo 放到工作目录下（符号链接或复制），让戏子能看到代码
                repo_in_work = work_path / "repo"
                if not repo_in_work.exists():
                    if repo_dir.exists():
                        import shutil

                        shutil.copytree(repo_dir, repo_in_work)
                    else:
                        repo_in_work.mkdir(parents=True, exist_ok=True)

                # 安装 repo 环境（pip install -e .），让测试能直接运行
                self._install_repo_env(repo_in_work)

                # 构建给导演的 objective，包含 repo 上下文
                objective = (
                    f"{task.description}\n\n"
                    f"The repository is located at: repo/\n"
                    f"All file paths should be relative to repo/. "
                    f"Use repo/astropy/... when reading or editing files."
                )

                # 共享方法：启动 runner（使用自定义 objective）
                (
                    report,
                    board,
                    steps,
                    tokens,
                    budget_exceeded,
                ) = await self._run_orchestrator(task, work_path, description=objective)

                # 提取 patch
                patch = extract_patch_from_work_dir(work_path)

                # 验证
                verify_result = verify_instance(
                    repo_dir,
                    patch,
                    {"test_command": instance.get("test_command")} if instance else {},
                )

                duration = time.monotonic() - start

                # Token efficiency: SWE-bench uses unlimited budget, skip calculation
                token_efficiency = 0.0

                return EvalResult(
                    task_id=task.task_id,
                    category=task.category,
                    difficulty=task.difficulty,
                    success=verify_result["passed"],
                    passed=1 if verify_result["passed"] else 0,
                    total=1,
                    task_success=1.0 if verify_result["passed"] else 0.0,
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
                    raw={
                        "apply_success": verify_result["apply_success"],
                        "test_result": verify_result.get("test_result", {}),
                        "patch_length": len(patch) if patch else 0,
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

    @staticmethod
    def _install_repo_env(repo_dir: Path) -> None:
        """Install the repository so tests can be run without extra setup."""
        import subprocess
        import sys

        install_marker = repo_dir / ".eval_installed"
        if install_marker.exists():
            return

        # Try pip install -e . first
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-e", "."],
                cwd=repo_dir,
                capture_output=True,
                timeout=300,
            )
            if result.returncode == 0:
                install_marker.write_text("pip install -e .", encoding="utf-8")
                return
        except (subprocess.TimeoutExpired, Exception):
            pass

        # Fallback: try python setup.py develop
        setup_py = repo_dir / "setup.py"
        if setup_py.exists():
            try:
                result = subprocess.run(
                    [sys.executable, "setup.py", "develop"],
                    cwd=repo_dir,
                    capture_output=True,
                    timeout=300,
                )
                if result.returncode == 0:
                    install_marker.write_text("setup.py develop", encoding="utf-8")
                    return
            except (subprocess.TimeoutExpired, Exception):
                pass

        # Best-effort: don't fail if install doesn't work
        install_marker.write_text("install_failed", encoding="utf-8")

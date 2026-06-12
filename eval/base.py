"""评估基类 — 定义通用接口与 7 维评估模型."""

from __future__ import annotations

import abc
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EvalTask:
    """单个评估任务的定义."""

    task_id: str
    category: str
    difficulty: str  # easy / medium / hard
    description: str
    # 初始文件系统状态: {relative_path: content}
    initial_files: dict[str, str] = field(default_factory=dict)
    # 需要创建的目录
    initial_dirs: list[str] = field(default_factory=list)
    # 验证规则
    verification: list[dict[str, Any]] = field(default_factory=list)
    # 期望的任务分解 (用于计算编排质量客观部分)
    expected_graph: dict[str, Any] | None = None
    # 资源限制
    max_steps: int = 20
    max_tokens: int = 50_000
    timeout_sec: int = 300


@dataclass
class EvalResult:
    """单个评估任务的结果 — 7 维评估体系."""

    task_id: str
    category: str
    difficulty: str

    # 基础结果
    success: bool = False
    passed: int = 0
    total: int = 0

    # ===== 7 维评估指标 (0-1) =====
    # 1. 任务成功率 — 做没做对
    task_success: float = 0.0
    # 2. Token 效率 — 花得值不值
    token_efficiency: float = 0.0
    # 3. 编排质量 — 调度好不好
    orchestration_quality: float = 0.0
    # 4. 协作成功率 — 协作闭环是否有效
    collaboration_success: float = 0.0
    # 5. 恢复率 — 错了能不能自己恢复
    recovery_rate: float = 0.0
    # 6. 产出质量 — 产出物质量
    output_quality: float = 0.0
    # 7. 自治度 — 需不需要人救
    autonomy: float = 0.0

    # 资源消耗
    steps_taken: int = 0
    tokens_used: int = 0
    budget_exceeded: bool = False
    duration_sec: float = 0.0

    # Judge 相关元数据
    judge_skipped: bool = False
    judge_error: str | None = None
    judge_cost_usd: float | None = None

    # 原始数据
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EvalHarness(abc.ABC):
    """评估 Harness 基类."""

    name: str = ""

    def __init__(self, work_dir: Path, config_path: str | Path = "agent.json"):
        self.work_dir = Path(work_dir)
        self.config_path = Path(config_path)
        self.results: list[EvalResult] = []

    @abc.abstractmethod
    def load_tasks(self, limit: int | None = None) -> list[EvalTask]:
        """加载评估任务列表."""
        ...

    @abc.abstractmethod
    async def run_task(self, task: EvalTask) -> EvalResult:
        """运行单个任务并返回结果."""
        ...

    async def _run_orchestrator(
        self,
        task: EvalTask,
        work_path: Path,
        description: str | None = None,
    ) -> tuple[Any, Any, int, int, bool]:
        """共享方法：启动 OrchestratorRunner 运行任务.

        Args:
            description: 可选自定义 objective，默认使用 task.description

        Returns: (report, state_board, steps, tokens, budget_exceeded)
        """
        import asyncio

        from openagents_orchestration.runner import OrchestratorRunner
        from openagents_orchestration.state_board import Budget

        runner = OrchestratorRunner(config_path=str(self.config_path))

        # SWE-bench 等使用无限预算时用 -1
        budget = Budget(
            max_steps=task.max_steps if task.max_steps >= 0 else -1,
            token_limit=task.max_tokens if task.max_tokens >= 0 else -1,
            time_limit_s=task.timeout_sec,
        )

        desc = description if description is not None else task.description
        report = await asyncio.wait_for(
            runner.run(desc, budget=budget, work_dir=work_path),
            timeout=task.timeout_sec,
        )

        board = runner.state_board
        steps = board.budget.steps_taken if board else 0
        tokens = board.budget.token_used if board else 0
        budget_exceeded = board.budget.exhausted if board else False

        await runner.close()
        return report, board, steps, tokens, budget_exceeded

    @staticmethod
    def compute_token_efficiency(task: EvalTask, steps: int, tokens: int) -> float:
        """计算 token 效率：任一维度超预算都会拉低分数."""
        if steps <= 0 or tokens <= 0:
            return 0.0
        # 负值表示无限预算，此时只计算实际使用的比例（越小越好）
        if task.max_steps < 0 and task.max_tokens < 0:
            return 0.0  # 无限预算时无法计算效率
        token_ratio = min(task.max_tokens / tokens, 1.0) if task.max_tokens > 0 else 1.0
        step_ratio = min(task.max_steps / steps, 1.0) if task.max_steps > 0 else 1.0
        return min(token_ratio, step_ratio)

    @staticmethod
    def compute_autonomy(board: Any) -> float:
        """从 StateBoard 计算自治度."""
        if board is None:
            return 1.0
        human_questions = getattr(board, "_human_questions", [])
        total_tasks = len(getattr(board, "tasks", {}))
        if total_tasks <= 0:
            return 1.0
        return 1.0 - (len(human_questions) / total_tasks)

    async def run_all(
        self, limit: int | None = None, difficulty_filter: str | None = None
    ) -> list[EvalResult]:
        """运行所有任务."""
        tasks = self.load_tasks(limit=limit)
        if difficulty_filter:
            tasks = [t for t in tasks if t.difficulty == difficulty_filter]
        for t in tasks:
            print(f"\n[EVAL:{self.name}] {t.task_id} ({t.difficulty})")
            start = time.monotonic()
            try:
                result = await self.run_task(t)
            except Exception as e:
                result = EvalResult(
                    task_id=t.task_id,
                    category=t.category,
                    difficulty=t.difficulty,
                    error=str(e),
                    duration_sec=time.monotonic() - start,
                )
                print(f"  ERROR: {e}")
            self.results.append(result)
            print(
                f"  success={result.success} "
                f"task_success={result.task_success:.2f} "
                f"orchestration={result.orchestration_quality:.2f} "
                f"output_quality={result.output_quality:.2f} "
                f"autonomy={result.autonomy:.2f} "
                f"steps={result.steps_taken} tokens={result.tokens_used} "
                f"dur={result.duration_sec:.1f}s"
            )
        return self.results

    def report(self) -> dict[str, Any]:
        """生成汇总报告."""
        if not self.results:
            return {}

        def _avg(key: str) -> float:
            vals = [
                getattr(r, key) for r in self.results if getattr(r, key) is not None
            ]
            return sum(vals) / len(vals) if vals else 0.0

        report = {
            "harness": self.name,
            "summary": {
                "total": len(self.results),
                "passed": sum(1 for r in self.results if r.success),
                "pass_rate": sum(1 for r in self.results if r.success)
                / len(self.results),
                "avg_task_success": _avg("task_success"),
                "avg_token_efficiency": _avg("token_efficiency"),
                "avg_orchestration_quality": _avg("orchestration_quality"),
                "avg_collaboration_success": _avg("collaboration_success"),
                "avg_recovery_rate": _avg("recovery_rate"),
                "avg_output_quality": _avg("output_quality"),
                "avg_autonomy": _avg("autonomy"),
                "avg_steps": _avg("steps_taken"),
                "avg_tokens": _avg("tokens_used"),
                "avg_duration_sec": _avg("duration_sec"),
                "judge_errors": sum(1 for r in self.results if r.judge_error),
            },
            "by_difficulty": {},
            "tasks": [r.to_dict() for r in self.results],
        }

        # 按难度分组
        diffs: dict[str, list[EvalResult]] = {}
        for r in self.results:
            diffs.setdefault(r.difficulty, []).append(r)
        for diff, rs in diffs.items():
            report["by_difficulty"][diff] = {
                "count": len(rs),
                "passed": sum(1 for r in rs if r.success),
                "pass_rate": sum(1 for r in rs if r.success) / len(rs),
                "avg_task_success": _avg_for_list([r.task_success for r in rs]),
                "avg_output_quality": _avg_for_list([r.output_quality for r in rs]),
            }

        return report


def _avg_for_list(vals: list[Any]) -> float:
    """过滤 None 后计算平均值."""
    filtered = [v for v in vals if v is not None]
    return sum(filtered) / len(filtered) if filtered else 0.0

    def save_report(self, path: Path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.report(), f, indent=2, ensure_ascii=False)


class WorkDirSetup:
    """辅助类: 初始化/清理工作目录."""

    def __init__(self, base_dir: Path, task_id: str):
        self.base_dir = base_dir
        self.task_id = task_id
        self.path = base_dir / task_id.replace("/", "_")

    def setup(self, task: EvalTask) -> Path:
        """创建目录并写入初始文件."""
        if self.path.exists():
            import shutil

            shutil.rmtree(self.path)
        self.path.mkdir(parents=True)
        for d in task.initial_dirs:
            (self.path / d).mkdir(parents=True, exist_ok=True)
        for rel_path, content in task.initial_files.items():
            fpath = self.path / rel_path
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(content, encoding="utf-8")
        return self.path

    def cleanup(self) -> None:
        import shutil

        if self.path.exists():
            shutil.rmtree(self.path)

    def __enter__(self) -> Path:
        """Context manager entry: requires task to be set via attribute."""
        if getattr(self, "_enter_task", None) is None:
            raise RuntimeError("Use WorkDirSetup.with_task(task) as context manager")
        return self.setup(self._enter_task)

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.cleanup()

    def with_task(self, task: EvalTask) -> WorkDirSetup:
        """绑定 task，启用 context manager 用法."""
        self._enter_task = task
        return self


def verify_task(
    work_dir: Path,
    rules: list[dict[str, Any]],
    errors_out: dict[str, str] | None = None,
) -> dict[str, float]:
    """通用验证器: 执行验证规则并返回各条分数.

    Args:
        errors_out: 可选字典，用于接收每个规则的错误信息
                   (timeout | file_not_found | unknown_rule | error)
    """
    import warnings

    scores: dict[str, float] = {}
    for i, rule in enumerate(rules):
        rule_type = rule.get("type", "")
        key = f"{rule_type}_{i}"
        try:
            if rule_type == "file_exists":
                scores[key] = 1.0 if (work_dir / rule["path"]).exists() else 0.0
            elif rule_type == "file_contains":
                path = work_dir / rule["path"]
                if not path.exists():
                    scores[key] = 0.0
                    continue
                content = path.read_text(encoding="utf-8")
                patterns = rule.get("patterns", [])
                if patterns:
                    matches = sum(1 for p in patterns if p in content)
                    scores[key] = matches / len(patterns)
                else:
                    scores[key] = 1.0
            elif rule_type == "test_pass":
                import shlex
                import subprocess

                cmd = shlex.split(rule["command"])
                cwd = work_dir / rule["cwd"] if rule.get("cwd") else work_dir
                env = os.environ.copy()
                env.update(rule.get("env", {}))
                result = subprocess.run(
                    cmd,
                    shell=False,
                    cwd=cwd,
                    env=env,
                    capture_output=True,
                    timeout=rule.get("timeout", 60),
                )
                scores[key] = 1.0 if result.returncode == 0 else 0.0
            elif rule_type == "exec":
                import shlex
                import subprocess

                cmd = shlex.split(rule["command"])
                cwd = work_dir / rule["cwd"] if rule.get("cwd") else work_dir
                env = os.environ.copy()
                env.update(rule.get("env", {}))
                result = subprocess.run(
                    cmd,
                    shell=False,
                    cwd=cwd,
                    env=env,
                    capture_output=True,
                    timeout=rule.get("timeout", 60),
                )
                expected = rule.get("expected_output", "")
                actual = result.stdout.decode("utf-8", errors="replace")
                scores[key] = 1.0 if expected in actual else 0.0
            else:
                warnings.warn(
                    f"verify_task: unknown rule type '{rule_type}' for key {key}",
                    stacklevel=2,
                )
                scores[key] = 0.0
                if errors_out is not None:
                    errors_out[key] = f"unknown_rule:{rule_type}"
        except subprocess.TimeoutExpired:
            warnings.warn(
                f"verify_task: rule {key} timed out after {rule.get('timeout', 60)}s",
                stacklevel=2,
            )
            scores[key] = 0.0
            if errors_out is not None:
                errors_out[key] = "timeout"
        except FileNotFoundError as e:
            warnings.warn(f"verify_task: rule {key} file not found: {e}", stacklevel=2)
            scores[key] = 0.0
            if errors_out is not None:
                errors_out[key] = f"file_not_found:{e}"
        except Exception as e:
            warnings.warn(f"verify_task: rule {key} error: {e}", stacklevel=2)
            scores[key] = 0.0
            if errors_out is not None:
                errors_out[key] = f"error:{e}"
    return scores

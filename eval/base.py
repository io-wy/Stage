"""评估基类 — 定义通用接口与 7 维评估模型."""

from __future__ import annotations

import abc
import json
import time
from dataclasses import dataclass, field, asdict
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

    async def run_all(self, limit: int | None = None) -> list[EvalResult]:
        """运行所有任务."""
        tasks = self.load_tasks(limit=limit)
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
            vals = [getattr(r, key) for r in self.results if getattr(r, key) is not None]
            return sum(vals) / len(vals) if vals else 0.0

        report = {
            "harness": self.name,
            "summary": {
                "total": len(self.results),
                "passed": sum(1 for r in self.results if r.success),
                "pass_rate": sum(1 for r in self.results if r.success) / len(self.results),
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
                "avg_task_success": sum(r.task_success for r in rs) / len(rs),
                "avg_output_quality": sum(r.output_quality for r in rs) / len(rs),
            }

        return report

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


def verify_task(work_dir: Path, rules: list[dict[str, Any]]) -> dict[str, float]:
    """通用验证器: 执行验证规则并返回各条分数."""
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
                import subprocess
                result = subprocess.run(
                    rule["command"],
                    shell=True,
                    cwd=work_dir,
                    capture_output=True,
                    timeout=rule.get("timeout", 60),
                )
                scores[key] = 1.0 if result.returncode == 0 else 0.0
            elif rule_type == "exec":
                import subprocess
                result = subprocess.run(
                    rule["command"],
                    shell=True,
                    cwd=work_dir,
                    capture_output=True,
                    timeout=rule.get("timeout", 60),
                )
                expected = rule.get("expected_output", "")
                actual = result.stdout.decode("utf-8", errors="replace")
                scores[key] = 1.0 if expected in actual else 0.0
            else:
                scores[key] = 0.0
        except Exception:
            scores[key] = 0.0
    return scores

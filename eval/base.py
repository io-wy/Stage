"""评估基类 — 定义通用接口."""

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
    # 期望的任务分解 (用于计算调度质量)
    expected_graph: dict[str, Any] | None = None
    # 资源限制
    max_steps: int = 20
    max_tokens: int = 50000
    timeout_sec: int = 300


@dataclass
class EvalResult:
    """单个评估任务的结果."""

    task_id: str
    category: str
    difficulty: str

    # 基础结果
    success: bool = False
    passed: int = 0
    total: int = 0

    # 维度分数 (0-1)
    success_score: float = 0.0
    scheduling_score: float = 0.0
    execution_score: float = 0.0
    efficiency_score: float = 0.0
    resilience_score: float = 0.0
    state_score: float = 0.0

    # 资源消耗
    steps_taken: int = 0
    tokens_used: int = 0
    budget_exceeded: bool = False
    duration_sec: float = 0.0

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
            print(f"  success={result.success} score={result.success_score:.2f} "
                  f"steps={result.steps_taken} tokens={result.tokens_used} "
                  f"dur={result.duration_sec:.1f}s")
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
                "avg_success_score": _avg("success_score"),
                "avg_scheduling_score": _avg("scheduling_score"),
                "avg_efficiency_score": _avg("efficiency_score"),
                "avg_steps": _avg("steps_taken"),
                "avg_tokens": _avg("tokens_used"),
                "avg_duration_sec": _avg("duration_sec"),
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
                "avg_success_score": sum(r.success_score for r in rs) / len(rs),
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

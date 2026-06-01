"""SWE-bench-lite 评估适配.

从 HuggingFace 加载 SWE-bench-lite 数据集，用戏台编排引擎尝试修复 GitHub issue.
由于资源限制，默认只跑少量样本 (--limit N).

Usage:
    uv run python -m eval.main --suite swe_bench_lite --limit 3
"""

from eval.swe_bench_lite.harness import SWEBenchHarness

__all__ = ["SWEBenchHarness"]

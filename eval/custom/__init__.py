"""自建评估任务.

从 YAML 文件加载自定义任务，测试戏台编排能力.

Usage:
    uv run python -m eval.main --suite custom --limit 5
"""

from eval.custom.harness import CustomHarness

__all__ = ["CustomHarness"]

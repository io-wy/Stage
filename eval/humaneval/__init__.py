"""HumanEval 评估适配.

用戏台编排引擎解决 HumanEval 编程问题.
默认把每个问题拆为: 理解 -> 实现 -> 测试.

Usage:
    uv run python -m eval.main --suite humaneval --limit 10
"""

from eval.humaneval.harness import HumanEvalHarness

__all__ = ["HumanEvalHarness"]

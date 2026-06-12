"""戏台 (Xitai) 评估套件.

包含三个评估方向:
- swe_bench_lite: 软件工程任务评估
- humaneval: 代码生成评估
- custom: 自建多 Agent 编排评估（集成 Agent-as-Judge）

注意: eval 包运行时自动将项目 src/ 加入 Python path，
确保能导入 openagents_orchestration。
"""

import sys
from pathlib import Path

# 将项目 src/ 加入 Python path
_EVAL_ROOT = Path(__file__).parent.parent  # 项目根目录
_SRC_DIR = _EVAL_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from eval.base import EvalHarness, EvalResult, EvalTask  # noqa: E402
from eval.judge import ClaudeCodeJudge  # noqa: E402

__all__ = ["EvalResult", "EvalTask", "EvalHarness", "ClaudeCodeJudge"]

"""HumanEval 验证器.

执行 HumanEval 风格的测试，验证生成的 solution.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from typing import Any


def extract_function_body(source: str, entry_point: str) -> str | None:
    """从 source 中提取指定函数的 body."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == entry_point:
            # 返回从函数定义开始到结束的 source
            lines = source.split("\n")
            start_line = node.lineno - 1
            end_line = node.end_lineno
            return "\n".join(lines[start_line:end_line])
    return None


def verify_humaneval_solution(
    solution_file: Path,
    test_file: Path,
    entry_point: str = "",
) -> dict[str, Any]:
    """验证 HumanEval 解决方案.

    策略:
    1. 检查 solution.py 语法是否合法
    2. 运行 test_solution.py
    3. 如果测试失败，尝试提取错误信息
    """
    solution_code = solution_file.read_text(encoding="utf-8")

    # 语法检查
    try:
        ast.parse(solution_code)
    except SyntaxError as e:
        return {
            "passed": False,
            "reason": f"syntax error: {e}",
        }

    # 检查 entry_point 是否存在
    if entry_point:
        found = False
        try:
            tree = ast.parse(solution_code)
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == entry_point:
                    found = True
                    break
        except Exception:
            pass
        if not found:
            return {
                "passed": False,
                "reason": f"entry_point '{entry_point}' not found",
            }

    # 执行测试
    work_dir = solution_file.parent
    # HumanEval 测试通常依赖 solution.py 中的函数
    # 我们需要确保 test_solution.py 能 import solution
    test_code = test_file.read_text(encoding="utf-8")

    # 如果 test 文件里用了 from solution import ... 我们需要在正确的目录运行
    try:
        result = subprocess.run(
            [sys.executable, str(test_file)],
            cwd=work_dir,
            capture_output=True,
            timeout=30,
        )
        passed = result.returncode == 0
        return {
            "passed": passed,
            "reason": "tests passed" if passed else "tests failed",
            "stdout": result.stdout.decode("utf-8", errors="replace")[-2000:],
            "stderr": result.stderr.decode("utf-8", errors="replace")[-2000:],
        }
    except subprocess.TimeoutExpired:
        return {
            "passed": False,
            "reason": "test timeout",
        }
    except Exception as e:
        return {
            "passed": False,
            "reason": f"test execution error: {e}",
        }

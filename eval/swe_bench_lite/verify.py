"""SWE-bench-lite 验证器.

验证生成的 patch 是否正确修复了 issue.
简化版: 在准备好的环境中运行测试，看是否通过.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def apply_patch(repo_dir: Path, patch_content: str | None) -> bool:
    """将生成的 patch 应用到仓库."""
    if not patch_content:
        return False

    patch_file = repo_dir / "__eval_patch__.diff"
    patch_file.write_text(patch_content, encoding="utf-8")

    result = subprocess.run(
        ["git", "apply", patch_file.name],
        cwd=repo_dir,
        capture_output=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")[:500]
        print(f"[verify] git apply failed: {stderr}", file=__import__("sys").stderr)
    patch_file.unlink(missing_ok=True)
    return result.returncode == 0


def run_tests(repo_dir: Path, test_spec: dict[str, Any]) -> dict[str, Any]:
    """运行测试验证修复.

    Args:
        repo_dir: 代码仓库目录
        test_spec: 测试配置，例如:
            {"test_command": "pytest tests/test_issue.py::test_case -x"}
            或从 SWE-bench 的 test_patch 推导
    """
    # 优先用实例自带的 test command
    cmd = test_spec.get("test_command")
    if not cmd:
        # 尝试从 instance 推断
        # SWE-bench 通常有 test_patch 字段，里面包含测试文件
        test_patch = test_spec.get("test_patch", "")
        if test_patch:
            # 尝试从 test_patch 中找测试命令
            # 简化: 尝试 pytest 在 tests/ 目录
            cmd = "pytest tests/ -x --tb=short"
        else:
            cmd = "python -m pytest"

    result = subprocess.run(
        cmd,
        shell=True,
        cwd=repo_dir,
        capture_output=True,
        timeout=120,
    )

    return {
        "passed": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout.decode("utf-8", errors="replace")[-2000:],
        "stderr": result.stderr.decode("utf-8", errors="replace")[-2000:],
    }


def verify_instance(
    repo_dir: Path,
    patch_content: str | None,
    test_spec: dict[str, Any],
) -> dict[str, Any]:
    """完整验证流程."""
    if not patch_content:
        return {
            "passed": False,
            "reason": "no patch generated",
            "apply_success": False,
            "test_result": None,
        }

    apply_ok = apply_patch(repo_dir, patch_content)
    if not apply_ok:
        return {
            "passed": False,
            "reason": "patch apply failed",
            "apply_success": False,
            "test_result": None,
        }

    test_result = run_tests(repo_dir, test_spec)
    return {
        "passed": test_result["passed"],
        "reason": "test passed" if test_result["passed"] else "test failed",
        "apply_success": True,
        "test_result": test_result,
    }


def extract_patch_from_work_dir(work_dir: Path) -> str | None:
    """从工作目录中提取生成的 patch.

    策略:
    1. 找 output/patch.diff 或类似文件
    2. 或从 repo 目录的 git diff 中提取
    """
    # 策略 1: 找显式输出的 patch 文件
    for candidate in ["patch.diff", "fix.diff", "output/patch.diff", "output/fix.diff"]:
        path = work_dir / candidate
        if path.exists():
            return path.read_text(encoding="utf-8")

    # 策略 2: 从 git diff 提取
    repo_dir = work_dir / "repo"
    if (repo_dir / ".git").exists():
        result = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=repo_dir,
            capture_output=True,
        )
        if result.returncode == 0:
            diff = result.stdout.decode("utf-8")
            if diff.strip():
                return diff

    return None

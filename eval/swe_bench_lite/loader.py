"""SWE-bench-lite 数据加载器.

支持从以下来源加载:
1. HuggingFace datasets (princeton-nlp/SWE-bench_Lite)
2. 本地缓存的 JSONL 文件
3. 本地目录中已有的 git 仓库
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


def load_from_huggingface(split: str = "test", limit: int | None = None) -> list[dict[str, Any]]:
    """从 HuggingFace 加载 SWE-bench-lite 数据集."""
    try:
        from datasets import load_dataset
    except ImportError:
        raise ImportError(
            "datasets library not installed. Run: uv pip install datasets"
        )

    ds = load_dataset("princeton-nlp/SWE-bench_Lite", split=split)
    items = []
    for i, row in enumerate(ds):
        if limit is not None and i >= limit:
            break
        items.append(dict(row))
    return items


def load_from_jsonl(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    """从本地 JSONL 文件加载."""
    items = []
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if limit is not None and i >= limit:
                break
            items.append(json.loads(line))
    return items


def load_swe_bench_lite(
    source: str | None = None,
    split: str = "test",
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """统一加载入口.

    Args:
        source: "huggingface" 或本地 JSONL 路径. 默认先尝试 huggingface.
        split: 数据集 split.
        limit: 最多加载多少条.
    """
    if source and Path(source).exists():
        return load_from_jsonl(Path(source), limit=limit)

    # 尝试本地缓存
    cache_dir = Path(".eval_cache") / "swe_bench_lite"
    cache_file = cache_dir / f"{split}.jsonl"
    if cache_file.exists():
        return load_from_jsonl(cache_file, limit=limit)

    # 从 HuggingFace 下载并缓存
    try:
        items = load_from_huggingface(split=split, limit=limit)
        cache_dir.mkdir(parents=True, exist_ok=True)
        with open(cache_file, "w", encoding="utf-8") as f:
            for item in items:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        return items
    except Exception as e:
        raise RuntimeError(
            f"Failed to load SWE-bench-lite: {e}\n"
            "Please install datasets: uv pip install datasets\n"
            "Or download manually and pass --source <path>.jsonl"
        )


def clone_repo(repo_url: str, commit_hash: str, dest: Path) -> Path:
    """克隆指定 commit 的代码仓库."""
    # 转换为 https URL (处理 github.com/owner/repo 格式)
    if not repo_url.startswith("http"):
        repo_url = f"https://github.com/{repo_url}.git"

    if dest.exists():
        import shutil
        shutil.rmtree(dest)

    subprocess.run(
        ["git", "clone", "--depth", "1", repo_url, str(dest)],
        check=True,
        capture_output=True,
    )

    # checkout 到 issue 前的 commit
    subprocess.run(
        ["git", "checkout", commit_hash],
        cwd=dest,
        check=True,
        capture_output=True,
    )
    return dest


def setup_repo(instance: dict[str, Any], work_dir: Path) -> Path:
    """为单个实例准备代码仓库."""
    repo_path = work_dir / "repo"
    repo = instance.get("repo", "")
    base_commit = instance.get("base_commit", "")

    if not repo or not base_commit:
        raise ValueError(f"Missing repo or base_commit in instance: {instance.get('instance_id')}")

    return clone_repo(repo, base_commit, repo_path)

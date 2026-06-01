"""HumanEval 数据加载器.

支持:
1. HuggingFace datasets (openai_humaneval)
2. 本地 JSONL 文件
3. 手动下载的 humaneval.jsonl
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_from_huggingface(split: str = "test", limit: int | None = None) -> list[dict[str, Any]]:
    try:
        from datasets import load_dataset
    except ImportError:
        raise ImportError("datasets library not installed. Run: uv pip install datasets")

    ds = load_dataset("openai_humaneval", split=split, trust_remote_code=True)
    items = []
    for i, row in enumerate(ds):
        if limit is not None and i >= limit:
            break
        items.append(dict(row))
    return items


def load_from_jsonl(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    items = []
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if limit is not None and i >= limit:
                break
            items.append(json.loads(line))
    return items


def load_humaneval(
    source: str | None = None,
    split: str = "test",
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """统一加载入口."""
    if source and Path(source).exists():
        return load_from_jsonl(Path(source), limit=limit)

    cache_dir = Path(".eval_cache") / "humaneval"
    cache_file = cache_dir / f"{split}.jsonl"
    if cache_file.exists():
        return load_from_jsonl(cache_file, limit=limit)

    try:
        items = load_from_huggingface(split=split, limit=limit)
        cache_dir.mkdir(parents=True, exist_ok=True)
        with open(cache_file, "w", encoding="utf-8") as f:
            for item in items:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        return items
    except Exception as e:
        raise RuntimeError(
            f"Failed to load HumanEval: {e}\n"
            "Please install datasets: uv pip install datasets\n"
            "Or download manually and pass --source <path>.jsonl"
        )

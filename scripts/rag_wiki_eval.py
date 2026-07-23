"""Wiki-backed RAG build / eval helper.

Examples:
  PYTHONPATH=src python scripts/rag_wiki_eval.py --wiki "${STAGE_WIKI_PATH:?set STAGE_WIKI_PATH}" \
    --kb /private/tmp/sast-wiki-kb.json

  PYTHONPATH=src python scripts/rag_wiki_eval.py --wiki "${STAGE_WIKI_PATH:?set STAGE_WIKI_PATH}" \
    --question "NAS 的访问方式是什么？" --top-k 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
for path in (REPO_ROOT, SRC_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from openagents_orchestration.rag import (  # noqa: E402
    DocMetadata,
    MockEmbeddingClient,
    OllamaEmbeddingClient,
    build_pipeline,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wiki", type=Path, required=True, help="wiki 根目录")
    parser.add_argument("--kb", type=Path, help="可选：生成/加载的 KB JSON")
    parser.add_argument("--question", default="", help="可选：检索问题")
    parser.add_argument("--embedding", choices=("ollama", "mock"), default="ollama")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    return parser.parse_args(argv)


async def async_main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.wiki.exists():
        raise SystemExit(f"wiki not found: {args.wiki}")

    pipe = build_pipeline(embedding=_embedding(args.embedding))
    if args.kb is not None and args.kb.exists():
        pipe.load(args.kb)
    else:
        await _ingest_wiki(pipe, args.wiki)
        if args.kb is not None:
            args.kb.parent.mkdir(parents=True, exist_ok=True)
            pipe.save(args.kb)

    if args.question.strip():
        result = await pipe.query(args.question, top_k=args.top_k)
        payload = {
            "question": args.question,
            "top_k": args.top_k,
            "passages": [
                {
                    "source": passage.metadata.source,
                    "score": passage.score,
                    "text": passage.text,
                }
                for passage in result.passages
            ],
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"question: {payload['question']}")
            for passage in payload["passages"]:
                print(f"[{passage['score']:.3f}] {passage['source']} | {passage['text'][:80]}")
    else:
        print(f"ingested wiki: {args.wiki}")
        print(f"kb size: {len(pipe)} chunks")
    return 0


async def _ingest_wiki(pipe, wiki_root: Path) -> None:
    for path in sorted(_iter_wiki_files(wiki_root)):
        relative = path.relative_to(wiki_root).as_posix()
        await pipe.ingest(path, DocMetadata(source=relative, tags=["wiki"]))


def _iter_wiki_files(wiki_root: Path) -> list[Path]:
    files: list[Path] = []
    for path in wiki_root.rglob("*"):
        if path.is_file() and not path.name.startswith("."):
            files.append(path)
    return files


def _embedding(kind: str):
    if kind == "mock":
        return MockEmbeddingClient()
    return OllamaEmbeddingClient(timeout=180, batch_size=1)


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())

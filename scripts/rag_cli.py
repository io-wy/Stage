"""Standalone RAG CLI.

Examples:
  PYTHONPATH=src python scripts/rag_cli.py --kb /private/tmp/wiki-kb.json \
    --question "服务入口是什么？"

  PYTHONPATH=src python scripts/rag_cli.py --kb /private/tmp/wiki-kb.json \
    --question "服务需要哪些审批信息？" \
    --required-tags perm:sensitive \
    --allowed-permission-tags perm:public,perm:internal,perm:sensitive \
    --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

from openagents_orchestration.rag import (
    MockEmbeddingClient,
    OllamaEmbeddingClient,
    RagQueryRunLog,
    build_pipeline,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb", type=Path, required=True, help="已保存的 RAG KB JSON")
    parser.add_argument("--question", default="", help="要查询的问题")
    parser.add_argument("--mode", choices=("answer", "query"), default="answer")
    parser.add_argument("--embedding", choices=("ollama", "mock"), default="ollama")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--filter-tags", default="", help="逗号分隔,如 service-guide")
    parser.add_argument("--required-tags", default="", help="逗号分隔,如 perm:sensitive")
    parser.add_argument(
        "--allowed-permission-tags",
        default="perm:public,perm:internal",
        help="answer 模式权限上下文;逗号分隔",
    )
    parser.add_argument("--no-route", action="store_true", help="禁用 route classifier")
    parser.add_argument("--no-rewrite", action="store_true", help="禁用受控 query rewrite")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON run log")
    parser.add_argument("--show-trace", action="store_true", help="文本模式输出 top_k trace")
    return parser.parse_args(argv)


async def async_main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.question.strip():
        raise SystemExit("--question is required")
    if not args.kb.exists():
        raise SystemExit(f"kb not found: {args.kb}")

    pipe = build_pipeline(embedding=_embedding(args.embedding))
    pipe.load(args.kb)

    filter_tags = _split_csv(args.filter_tags)
    required_tags = _split_csv(args.required_tags)
    allowed_permission_tags = tuple(_split_csv(args.allowed_permission_tags) or [])

    if args.mode == "answer":
        log = await pipe.answer_with_log(
            args.question,
            top_k=args.top_k,
            filter_tags=filter_tags,
            required_tags=required_tags,
            allowed_permission_tags=allowed_permission_tags,
            use_route_classifier=not args.no_route,
            use_query_rewrite=not args.no_rewrite,
        )
    else:
        _, log = await pipe.query_with_log(
            args.question,
            top_k=args.top_k,
            filter_tags=filter_tags,
            required_tags=required_tags,
            use_route_classifier=not args.no_route,
            use_query_rewrite=not args.no_rewrite,
        )

    if args.json:
        print(json.dumps(_log_to_dict(log), ensure_ascii=False, indent=2))
    else:
        _print_text(log, show_trace=args.show_trace)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


def _embedding(kind: str):
    if kind == "mock":
        return MockEmbeddingClient()
    return OllamaEmbeddingClient(timeout=180, batch_size=1)


def _split_csv(value: str) -> list[str] | None:
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or None


def _log_to_dict(log: RagQueryRunLog) -> dict:
    return log.model_dump(mode="json")


def _print_text(log: RagQueryRunLog, show_trace: bool) -> None:
    print(f"query: {log.query}")
    if log.route is not None:
        print(f"route: {log.route.selected_route}")
    if log.rewrite is not None and log.rewrite.changed:
        print(f"rewrite: {log.rewrite.rewritten_query}")

    if log.answer is not None:
        print(f"answer_status: {log.answer.status}")
        if log.answer.refusal_reason:
            print(f"refusal_reason: {log.answer.refusal_reason}")
        if log.answer.answer_text:
            print(log.answer.answer_text)
        if log.answer.citations:
            print("citations:")
            for citation in log.answer.citations:
                print(f"  [{citation.rank}] {citation.source}")

    if show_trace:
        print("trace:")
        for passage in log.retrieval.passages:
            print(f"  {passage.rank}. score={passage.score:.3f} source={passage.source}")


if __name__ == "__main__":
    raise SystemExit(main())

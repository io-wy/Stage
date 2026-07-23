"""RAG CLI smoke tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys

from openagents_orchestration.rag import DocMetadata, build_pipeline


async def test_rag_cli_answer_json(tmp_path):
    kb = tmp_path / "kb.json"
    pipe = build_pipeline()
    await pipe.ingest_text(
        "SAST Link 是统一身份认证系统。",
        DocMetadata(source="/wiki/SAST Link.md", tags=["SAST Link", "perm:public"]),
    )
    pipe.save(kb)

    result = _run_cli(
        "--kb",
        str(kb),
        "--embedding",
        "mock",
        "--mode",
        "answer",
        "--question",
        "SAST Link 是什么？",
        "--allowed-permission-tags",
        "perm:public,perm:internal",
        "--json",
    )
    payload = json.loads(result.stdout)

    assert payload["answer"]["status"] == "answered"
    assert payload["answer"]["citations"][0]["source"] == "/wiki/SAST Link.md"
    assert payload["retrieval"]["passages"][0]["source"] == "/wiki/SAST Link.md"


async def test_rag_cli_refuses_sensitive_answer_for_public_permission(tmp_path):
    kb = tmp_path / "kb.json"
    pipe = build_pipeline()
    await pipe.ingest_text(
        "服务器 password 内部说明。",
        DocMetadata(source="/wiki/SAST 设施指南/docx/服务器登录.md", tags=["SAST 设施指南"]),
    )
    pipe.save(kb)

    result = _run_cli(
        "--kb",
        str(kb),
        "--embedding",
        "mock",
        "--mode",
        "answer",
        "--question",
        "服务器说明是什么？",
        "--allowed-permission-tags",
        "perm:public",
        "--no-route",
        "--json",
    )
    payload = json.loads(result.stdout)

    assert payload["answer"]["status"] == "refused"
    assert payload["answer"]["citations"] == []


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    return subprocess.run(
        [sys.executable, "scripts/rag_cli.py", *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

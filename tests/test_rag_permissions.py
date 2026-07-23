"""RAG 权限 tag 测试。"""

from __future__ import annotations

from openagents_orchestration.rag import (
    DocMetadata,
    InMemoryCosineStore,
    build_pipeline,
)


async def test_ingest_adds_permission_tags_to_chunks():
    store = InMemoryCosineStore()
    pipe = build_pipeline(store=store)

    await pipe.ingest_text(
        "公开部门介绍",
        DocMetadata(source="/wiki/SAST 说明书 Public 版/docx/部门介绍.md"),
    )
    await pipe.ingest_text(
        "内部设施指南",
        DocMetadata(source="/wiki/SAST 设施指南/docx/设施指南.md"),
    )
    await pipe.ingest_text(
        "服务器登录密码和 SecretKey",
        DocMetadata(source="/wiki/SAST 设施指南/docx/服务器登录指南.md"),
    )

    tags_by_source = {
        chunk.metadata.source: set(chunk.metadata.tags)
        for chunk in store.iter_chunks()
    }

    assert "perm:public" in tags_by_source["/wiki/SAST 说明书 Public 版/docx/部门介绍.md"]
    assert "perm:internal" in tags_by_source["/wiki/SAST 设施指南/docx/设施指南.md"]
    assert "perm:sensitive" in tags_by_source["/wiki/SAST 设施指南/docx/服务器登录指南.md"]


async def test_query_required_tags_filters_permissions():
    pipe = build_pipeline()
    await pipe.ingest_text(
        "服务器 公开介绍",
        DocMetadata(source="/wiki/SAST 说明书 Public 版/docx/服务器公开.md"),
    )
    await pipe.ingest_text(
        "服务器 登录密码",
        DocMetadata(source="/wiki/SAST 设施指南/docx/服务器登录指南.md"),
    )

    public = await pipe.query("服务器", top_k=3, required_tags=["perm:public"])
    sensitive = await pipe.query("服务器", top_k=3, required_tags=["perm:sensitive"])

    assert public.passages
    assert all("perm:public" in passage.metadata.tags for passage in public.passages)
    assert sensitive.passages
    assert all(
        "perm:sensitive" in passage.metadata.tags for passage in sensitive.passages
    )

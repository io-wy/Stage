"""Tests for ArtifactStore abstraction."""

from __future__ import annotations

from pathlib import Path

import pytest

from openagents_orchestration.store.artifact_store import LocalArtifactStore


@pytest.fixture
def store(tmp_path: Path) -> LocalArtifactStore:
    return LocalArtifactStore(tmp_path)


@pytest.mark.asyncio
async def test_put_and_get_text(store: LocalArtifactStore) -> None:
    await store.put("t1", "src/main.py", "hello = 'world'\n")
    content = await store.get("t1", "src/main.py")
    assert content == "hello = 'world'\n"


@pytest.mark.asyncio
async def test_put_and_get_bytes(store: LocalArtifactStore) -> None:
    await store.put("t1", "image.png", b"\x89PNG\r\n\x1a\n")
    content = await store.get("t1", "image.png")
    assert content == b"\x89PNG\r\n\x1a\n"


@pytest.mark.asyncio
async def test_exists(store: LocalArtifactStore) -> None:
    assert await store.exists("t1", "foo.py") is False
    await store.put("t1", "foo.py", "x")
    assert await store.exists("t1", "foo.py") is True


@pytest.mark.asyncio
async def test_list(store: LocalArtifactStore) -> None:
    await store.put("t1", "a.py", "1")
    await store.put("t1", "b/c.py", "2")
    keys = sorted(await store.list("t1"))
    assert "t1/a.py" in keys
    assert "t1/b/c.py" in keys


@pytest.mark.asyncio
async def test_list_empty_task(store: LocalArtifactStore) -> None:
    keys = await store.list("nonexistent", "")
    assert keys == []


@pytest.mark.asyncio
async def test_delete(store: LocalArtifactStore) -> None:
    await store.put("t1", "del.py", "x")
    assert await store.delete("t1", "del.py") is True
    assert await store.exists("t1", "del.py") is False
    assert await store.delete("t1", "del.py") is False


@pytest.mark.asyncio
async def test_get_missing_raises(store: LocalArtifactStore) -> None:
    with pytest.raises(FileNotFoundError):
        await store.get("t1", "missing.py")


@pytest.mark.asyncio
async def test_put_overwrite(store: LocalArtifactStore) -> None:
    await store.put("t1", "file.py", "v1")
    await store.put("t1", "file.py", "v2")
    assert await store.get("t1", "file.py") == "v2"


@pytest.mark.asyncio
async def test_return_key(store: LocalArtifactStore) -> None:
    key = await store.put("t1", "nested/file.py", "x")
    assert key == "t1/nested/file.py"


@pytest.mark.asyncio
async def test_list_with_prefix(store: LocalArtifactStore) -> None:
    await store.put("t1", "src/a.py", "1")
    await store.put("t1", "tests/b.py", "2")
    keys = sorted(await store.list("t1", "src"))
    assert all(k.startswith("t1/src") for k in keys)

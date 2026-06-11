"""ArtifactStore — shared storage abstraction for inter-agent artifact exchange.

Inspired by HiClaw's MinIO shared object storage: agents push artifacts to a
store instead of relying on file-system paths, which eliminates path-resolution
bugs and prepares the ground for remote/shared backends (S3, MinIO, etc.).

The default implementation is LocalArtifactStore, backed by the local file
system under a configurable base directory.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class ArtifactStore(ABC):
    """Abstract interface for storing and retrieving agent artifacts."""

    @abstractmethod
    async def put(self, task_id: str, path: str, content: str | bytes) -> str:
        """Store an artifact. Returns the storage key (relative path)."""

    @abstractmethod
    async def get(self, task_id: str, path: str) -> str | bytes:
        """Retrieve an artifact. Raises FileNotFoundError if missing."""

    @abstractmethod
    async def exists(self, task_id: str, path: str) -> bool:
        """Check whether an artifact exists."""

    @abstractmethod
    async def list(self, task_id: str, prefix: str = "") -> list[str]:
        """List artifact keys under a task/prefix."""

    @abstractmethod
    async def delete(self, task_id: str, path: str) -> bool:
        """Delete an artifact. Returns True if it existed."""


class LocalArtifactStore(ArtifactStore):
    """File-system backed artifact store."""

    def __init__(self, base_dir: str | Path):
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)

    def _resolve(self, task_id: str, path: str) -> Path:
        # Strip leading slashes to keep everything under base_dir
        safe_path = path.lstrip("/")
        return self._base / task_id / safe_path

    async def put(self, task_id: str, path: str, content: str | bytes) -> str:
        full = self._resolve(task_id, path)
        full.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            full.write_bytes(content)
        else:
            full.write_text(content, encoding="utf-8")
        return str(full.relative_to(self._base))

    async def get(self, task_id: str, path: str) -> str | bytes:
        full = self._resolve(task_id, path)
        if not full.exists():
            raise FileNotFoundError(f"Artifact not found: {task_id}/{path}")
        try:
            return full.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return full.read_bytes()

    async def exists(self, task_id: str, path: str) -> bool:
        return self._resolve(task_id, path).exists()

    async def list(self, task_id: str, prefix: str = "") -> list[str]:
        task_dir = self._base / task_id / prefix.lstrip("/")
        if not task_dir.exists():
            return []
        return [
            str(p.relative_to(self._base))
            for p in task_dir.rglob("*")
            if p.is_file()
        ]

    async def delete(self, task_id: str, path: str) -> bool:
        full = self._resolve(task_id, path)
        if full.exists():
            full.unlink()
            return True
        return False

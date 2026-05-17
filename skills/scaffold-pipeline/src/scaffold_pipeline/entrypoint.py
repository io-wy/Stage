"""scaffold-pipeline entrypoint.

Batch-create directories and files in one shot.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


async def run_openagent_skill(payload: dict[str, Any]) -> dict[str, Any]:
    """Execute the scaffold pipeline."""
    base_path = payload.get("base_path", ".")
    dirs = payload.get("dirs", []) or []
    files = payload.get("files", {}) or {}

    base = Path(base_path).resolve()
    base.mkdir(parents=True, exist_ok=True)

    created_dirs: list[str] = []
    created_files: list[str] = []
    skipped: list[str] = []

    # Create directories
    for d in dirs:
        if not isinstance(d, str):
            skipped.append(f"non-string dir: {d}")
            continue
        target = base / d
        if not target.exists():
            target.mkdir(parents=True, exist_ok=True)
            created_dirs.append(str(target.relative_to(base)))
        else:
            skipped.append(f"dir already exists: {d}")

    # Write files
    for path_str, content in files.items():
        if not isinstance(path_str, str):
            skipped.append(f"non-string path: {path_str}")
            continue
        target = base / path_str
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            existed = target.exists()
            target.write_text(str(content), encoding="utf-8")
            if existed:
                skipped.append(f"overwritten: {path_str}")
            else:
                created_files.append(path_str)
        except Exception as exc:
            skipped.append(f"failed to write {path_str}: {exc}")

    return {
        "base_path": str(base),
        "created_dirs": created_dirs,
        "created_files": created_files,
        "skipped": skipped,
    }

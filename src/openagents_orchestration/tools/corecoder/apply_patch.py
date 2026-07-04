"""Multi-file patch tool using unified-diff format.

More flexible than edit_file: supports multi-line changes, fuzzy matching
via context lines, and batch edits across multiple files in one call.

Patch format (simplified unified diff):

--- a/src/main.py
+++ b/src/main.py
@@ -10,5 +10,5 @@
 def foo():
-    pass
+    return 42
     x = 1

Multiple files can appear in one patch, separated by `---` lines.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openagents.errors.exceptions import ToolError
from openagents.interfaces.run_context import RunContext
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin

from openagents_orchestration.tools.corecoder._paths import resolve_agent_path


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    old_lines: list[str] = field(default_factory=list)
    new_lines: list[str] = field(default_factory=list)
    context_before: list[str] = field(default_factory=list)
    context_after: list[str] = field(default_factory=list)


@dataclass
class FilePatch:
    path: str
    hunks: list[Hunk] = field(default_factory=list)


class ApplyPatchTool(ToolPlugin):
    """Apply a unified-diff style patch to one or more files."""

    name = "apply_patch"
    description = (
        "Apply a unified-diff patch to one or more files. "
        "Best for multi-line or multi-file changes.\n\n"
        "Format:\n"
        "    --- a/src/main.py\n"
        "    +++ b/src/main.py\n"
        "    @@ -10,3 +10,3 @@\n"
        "     unchanged context line\n"
        "    -removed line\n"
        "    +added line\n\n"
        "Rules:\n"
        "- One file per '--- / +++' header.\n"
        "- a/ and b/ prefixes are stripped automatically.\n"
        "- For a single small edit use edit_file; for a full rewrite use write_file."
    )
    durable_idempotent = False

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(
            concurrency_safe=False,
            side_effects="writes_filesystem",
            writes_files=True,
            default_timeout_ms=30_000,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "patch": {
                    "type": "string",
                    "description": (
                        "Unified diff text. Can patch multiple files. "
                        "Each file starts with '--- a/path' and '+++ b/path'. "
                        "Hunks start with '@@ -old_start,old_count +new_start,new_count @@'."
                    ),
                },
            },
            "required": ["patch"],
        }

    async def invoke(
        self, params: dict[str, Any], context: RunContext[Any] | None
    ) -> dict[str, Any]:
        patch_text = str(params.get("patch", "")).strip()
        if not patch_text:
            raise ToolError("patch is required", tool_name=self.name)

        file_patches = _parse_patch(patch_text)
        if not file_patches:
            raise ToolError("No file patches found in patch text", tool_name=self.name)

        results: list[dict[str, Any]] = []
        dirty: set[str] = set()

        for fp in file_patches:
            # Resolve against the agent work dir so patched files land in the same
            # place as write_file/edit_file, not the bare process cwd.
            fp.path = str(resolve_agent_path(fp.path, context))
            try:
                result = _apply_file_patch(fp)
                results.append(result)
                if result.get("applied"):
                    dirty.add(str(Path(fp.path).resolve()))
            except Exception as exc:
                results.append(
                    {
                        "file": fp.path,
                        "applied": False,
                        "error": str(exc),
                    }
                )

        if context is not None:
            scratch_dirty = context.scratch.setdefault("dirty_files", set())
            if isinstance(scratch_dirty, set):
                scratch_dirty.update(dirty)

        applied = sum(1 for r in results if r.get("applied"))
        failed = len(results) - applied

        return {
            "files_patched": len(file_patches),
            "applied": applied,
            "failed": failed,
            "details": results,
            "message": (
                f"Patched {applied}/{len(file_patches)} file(s). "
                + (
                    "All succeeded."
                    if failed == 0
                    else f"Failed: {[r['file'] for r in results if not r.get('applied')]}"
                )
            ),
        }


def _parse_patch(text: str) -> list[FilePatch]:
    """Parse unified diff text into FilePatch list."""
    lines = text.splitlines()
    file_patches: list[FilePatch] = []
    i = 0

    while i < len(lines):
        # Look for --- line
        if not lines[i].startswith("--- "):
            i += 1
            continue

        old_path = lines[i][4:].strip()
        # Strip a/ prefix if present
        if old_path.startswith("a/"):
            old_path = old_path[2:]
        i += 1

        # Expect +++ line
        if i >= len(lines) or not lines[i].startswith("+++ "):
            continue
        new_path = lines[i][4:].strip()
        if new_path.startswith("b/"):
            new_path = new_path[2:]
        i += 1

        path = new_path or old_path
        fp = FilePatch(path=path)

        # Parse hunks
        while i < len(lines):
            if lines[i].startswith("--- "):
                break  # Next file
            if not lines[i].startswith("@@"):
                i += 1
                continue

            hunk_match = re.match(
                r"@@\s*-(\d+)(?:,(\d+))?\s*\+(\d+)(?:,(\d+))?\s*@@",
                lines[i],
            )
            if not hunk_match:
                i += 1
                continue

            old_start = int(hunk_match.group(1))
            old_count = int(hunk_match.group(2) or "1")
            new_start = int(hunk_match.group(3))
            new_count = int(hunk_match.group(4) or "1")
            i += 1

            hunk_lines: list[tuple[str, str]] = []  # (prefix, line)
            while i < len(lines) and not (
                lines[i].startswith("@@") or lines[i].startswith("--- ")
            ):
                line = lines[i]
                if line:
                    prefix = line[0]
                    if prefix in (" ", "-", "+"):
                        hunk_lines.append((prefix, line[1:]))
                    elif prefix == "\\":
                        pass  # "No newline at end of file" marker
                i += 1

            # Split into old_lines (space + minus) and new_lines (space + plus)
            old_lines = [ln for p, ln in hunk_lines if p in (" ", "-")]
            new_lines = [ln for p, ln in hunk_lines if p in (" ", "+")]
            context_before = []
            context_after = []
            # Extract leading/trailing context for fuzzy matching
            for p, ln in hunk_lines:
                if p == " ":
                    if not old_lines or len(context_before) < 3:
                        context_before.append(ln)
                    else:
                        context_after.append(ln)

            fp.hunks.append(
                Hunk(
                    old_start=old_start,
                    old_count=old_count,
                    new_start=new_start,
                    new_count=new_count,
                    old_lines=old_lines,
                    new_lines=new_lines,
                    context_before=context_before,
                    context_after=context_after,
                )
            )

        if fp.hunks:
            file_patches.append(fp)

    return file_patches


def _apply_file_patch(fp: FilePatch) -> dict[str, Any]:
    """Apply all hunks in a FilePatch to the file on disk."""
    path = Path(fp.path)
    if not path.exists():
        # Create new file if all hunks are insertions at line 0
        if all(h.old_start == 0 and h.old_count == 0 for h in fp.hunks):
            content_lines: list[str] = []
            for h in fp.hunks:
                content_lines.extend(h.new_lines)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(content_lines) + "\n", encoding="utf-8")
            return {
                "file": fp.path,
                "applied": True,
                "hunks": len(fp.hunks),
                "message": f"Created {fp.path} with {len(fp.hunks)} hunk(s).",
            }
        raise FileNotFoundError(f"File not found: {fp.path}")

    original = path.read_text(encoding="utf-8")
    original_lines = original.splitlines()
    # Preserve trailing newline behavior
    ends_with_newline = original.endswith("\n")

    # Apply hunks in reverse order (bottom-up) so line numbers stay valid
    modified_lines = list(original_lines)
    applied_hunks = 0
    errors: list[str] = []

    for hunk in reversed(fp.hunks):
        try:
            _apply_hunk(modified_lines, hunk)
            applied_hunks += 1
        except Exception as exc:
            errors.append(f"Hunk @-{hunk.old_start},{hunk.old_count}: {exc}")

    if errors:
        raise ValueError("; ".join(errors))

    # Write back
    new_content = "\n".join(modified_lines)
    if ends_with_newline or not new_content:
        new_content += "\n"
    path.write_text(new_content, encoding="utf-8")

    return {
        "file": fp.path,
        "applied": True,
        "hunks": applied_hunks,
        "lines_before": len(original_lines),
        "lines_after": len(modified_lines),
        "message": f"Applied {applied_hunks} hunk(s) to {fp.path}.",
    }


def _apply_hunk(lines: list[str], hunk: Hunk) -> None:
    """Apply a single hunk to the line list (in-place)."""
    # Try 1: Context-based fuzzy match
    match_idx = _find_hunk_by_context(lines, hunk)

    # Try 2: Exact old_lines match at old_start
    if match_idx is None:
        match_idx = _find_hunk_by_lines(lines, hunk)

    # Try 3: Line number fallback
    if match_idx is None:
        match_idx = hunk.old_start - 1  # 0-based
        if match_idx < 0:
            match_idx = 0
        if match_idx > len(lines):
            match_idx = len(lines)
        # Verify the content at this position roughly matches
        end_idx = match_idx + len(hunk.old_lines)
        if end_idx <= len(lines):
            actual = lines[match_idx:end_idx]
            if actual != hunk.old_lines:
                raise ValueError(
                    f"Could not match hunk. Context lines not found. "
                    f"Expected {len(hunk.old_lines)} lines around {hunk.old_start}."
                )
        else:
            raise ValueError(
                f"Hunk line range {hunk.old_start}-{hunk.old_start + len(hunk.old_lines)} "
                f"exceeds file length {len(lines)}."
            )

    # Replace old_lines with new_lines
    end_idx = match_idx + len(hunk.old_lines)
    lines[match_idx:end_idx] = hunk.new_lines


def _find_hunk_by_context(lines: list[str], hunk: Hunk) -> int | None:
    """Find hunk position by matching context_before lines."""
    if not hunk.context_before:
        return None

    ctx = hunk.context_before
    # Search for ctx as a consecutive block
    for i in range(len(lines) - len(ctx) + 1):
        if lines[i : i + len(ctx)] == ctx:
            # Found context. Check if old_lines follow.
            candidate = i + len(ctx)
            end = candidate + len(hunk.old_lines)
            if end <= len(lines) and lines[candidate:end] == hunk.old_lines:
                return candidate
    return None


def _find_hunk_by_lines(lines: list[str], hunk: Hunk) -> int | None:
    """Find exact old_lines match anywhere in the file."""
    if not hunk.old_lines:
        return None
    for i in range(len(lines) - len(hunk.old_lines) + 1):
        if lines[i : i + len(hunk.old_lines)] == hunk.old_lines:
            return i
    return None

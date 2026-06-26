"""Shared work-dir-aware path resolution for CoreCoder file tools.

Single source of truth for mapping an agent-supplied path (absolute or
relative) onto the orchestration working directory. This logic used to be
copy-pasted into read_file / write_file / edit_file / semantic_edit /
list_directory, while apply_patch bypassed it and resolved against the
process cwd -- so the same relative path could land in two different places.
Centralising it keeps every file tool on one base directory.

Resolution order:
1. Absolute path               -> used as-is.
2. context.scratch["bash_cwd"] -> where the agent's bash tool last cd'd.
3. runner._current_work_dir    -> the orchestration working directory.
4. Path.cwd()                  -> process fallback.

It also strips a leading work-dir-basename segment: agents sometimes prepend
the work dir's own name (e.g. ".eval_work/src/foo.py" when cwd is already
".eval_work"), which would otherwise create a nested directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openagents.interfaces.run_context import RunContext


def resolve_agent_path(path_str: str, context: RunContext[Any] | None) -> Path:
    """Resolve *path_str* against the agent's working directory.

    See the module docstring for the full resolution order and the
    work-dir-basename stripping behaviour.
    """
    path = Path(path_str)
    if path.is_absolute():
        return path

    base: Path | None = None
    if context is not None:
        cached = context.scratch.get("bash_cwd")
        if isinstance(cached, str):
            base = Path(cached)
        else:
            runner = getattr(getattr(context, "deps", None), "runner", None)
            cwd = getattr(runner, "_current_work_dir", None)
            if cwd is not None:
                base = Path(cwd)
    if base is None:
        base = Path.cwd()

    # Strip an accidental leading work-dir-basename segment to avoid nesting.
    parts = path.parts
    if parts and parts[0] == base.name:
        path = Path(*parts[1:])

    return base / path

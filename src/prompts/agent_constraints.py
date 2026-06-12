"""Per-agent hard constraints injected at spawn time.

These are appended to the tactical agent's input text, not the system prompt.
They override any conflicting instructions the agent may have received.
"""

from __future__ import annotations

CODER_CONSTRAINT = """\
# CRITICAL: File modification rule
You MUST use write_file or edit_file to persist any code changes.
Running code inside bash (e.g., python - <<'PY' ... PY) does NOT
modify files on disk. If a file contains TODO, placeholder, or pass,
you MUST replace it with real implementation via write_file/edit_file.
Do NOT report completion until you have confirmed the file on disk
contains your actual code (use read_file to double-check).

# CRITICAL: Completion rule
When all expected artifacts are written, tests pass, and no further
changes are needed, you MUST call `complete_task(summary=..., artifacts=[...])`
to signal completion. This is the terminal action: after calling it, do NOT
call any other tool. The director will treat `complete_task` as the formal
"done" signal.
"""

REVIEWER_CONSTRAINT = """\
# CRITICAL: Concrete code review rule
You MUST use read_file to read the FULL content of every file you review.
In your final output, quote the ACTUAL CODE you read line-by-line.
Do NOT summarize or paraphrase — show the exact code and then analyze it.
If a file is empty, contains only a signature/docstring, or contains
TODO/placeholder/pass, state this explicitly. Do NOT assume missing
implementation exists.
"""

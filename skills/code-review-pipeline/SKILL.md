---
name: code-review-pipeline
description: Run a static code review over a target directory. Scans Python/JS/TS files for common issues (long functions, TODOs, missing docstrings, unused imports, security smells) and produces a structured markdown report.
---

# Code Review Pipeline

Use this skill when you need a quick static code review of a directory or single file. It does not run tests, lint, or type-check — it produces a focused "what should a reviewer notice" report.

## Inputs

- `target_path` (required, str) — absolute or workspace-relative path to a file or directory.
- `output_path` (required, str) — where to write the review markdown report.
- `focus` (optional, str) — one of `quality`, `security`, `both`. Default `both`.
- `max_files` (optional, int) — cap on files to scan. Default 50.

## Outputs

```json
{
  "report_path": "/path/to/review.md",
  "files_analyzed": 12,
  "issues_count": 7,
  "issues_by_severity": {"high": 1, "medium": 3, "low": 3}
}
```

## What it checks

| Category | Examples |
|----------|----------|
| Length | Functions > 80 lines, files > 800 lines |
| Documentation | Missing module/function docstrings |
| TODOs | TODO/FIXME/XXX/HACK comments |
| Security smells | `eval`, `exec`, `os.system`, hardcoded passwords/keys |
| Imports | Unused imports, wildcard imports |

This is a heuristic pass — false positives are possible. It is a reviewer's checklist, not a verdict.

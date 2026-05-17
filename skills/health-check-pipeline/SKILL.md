---
name: health-check-pipeline
description: Run a suite of health checks (bash commands, file existence, content matching) and produce a structured pass/fail report. Pure Python, no LLM dependency.
---

# Health Check Pipeline

Use this skill when a monitor agent needs to verify system state, validate artifacts, or confirm that previous steps produced the expected output.

## Inputs

- `checks` (required, list[dict]) — health check items.
  - `type`: `"bash"` | `"file_exists"` | `"file_contains"`
  - `target`: command string (for bash) or file path
  - `expected` (optional): expected content substring (for file_contains) or exit code (for bash, default 0)
- `output_path` (required, str) — where to write the markdown report.

## Outputs

```json
{
  "report_path": "/path/to/health_report.md",
  "total": 5,
  "passed": 4,
  "failed": 1,
  "details": [
    {"check": "todo.py exists", "status": "passed", "elapsed_ms": 0.1},
    {"check": "pytest passes", "status": "failed", "elapsed_ms": 2300, "error": "1 test failed"}
  ]
}
```

## Check types

| Type | Target | Expected | Behaviour |
|------|--------|----------|-----------|
| `bash` | shell command | exit code (default 0) | Runs command, checks return code |
| `file_exists` | file path | — | Checks path.exists() |
| `file_contains` | file path | substring | Reads file, checks if substring present |

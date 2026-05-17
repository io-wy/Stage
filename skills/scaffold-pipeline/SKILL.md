---
name: scaffold-pipeline
description: Batch-create directory structures and files in one shot. Given a directory tree and file contents, creates everything atomically. Use this when a coder agent needs to scaffold a multi-file project skeleton without burning steps on individual write_file calls.
---

# Scaffold Pipeline

Use this skill when you need to create a project skeleton with many directories and files at once. It saves LLM steps compared to calling `write_file` repeatedly.

## Inputs

- `base_path` (optional, str) — root directory; default is current working directory.
- `dirs` (required, list[str]) — list of directory paths to create (relative to base_path).
- `files` (required, dict[str, str]) — mapping of relative file paths to their contents.

## Outputs

```json
{
  "created_dirs": ["app", "app/core", "app/api"],
  "created_files": ["app/main.py", "app/core/config.py"],
  "skipped": [],
  "base_path": "/path/to/project"
}
```

## Behavior

- Creates all directories first (parents=True, exist_ok=True).
- Writes all files; overwrites if they already exist.
- Reports exactly what was created vs skipped.

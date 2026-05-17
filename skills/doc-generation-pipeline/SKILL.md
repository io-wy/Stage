---
name: doc-generation-pipeline
description: Generate API documentation (markdown) from a Python module by AST parsing. Extracts module docstrings, function signatures, class hierarchies, and type hints. Pure Python, no LLM dependency.
---

# Doc Generation Pipeline

Use this skill when a reviewer or coder agent needs to produce API documentation for a Python module. It reads the source, extracts structural information, and renders it as markdown.

## Inputs

- `target_path` (required, str) — path to a `.py` file.
- `output_path` (required, str) — where to write the markdown doc.

## Outputs

```json
{
  "doc_path": "/path/to/todo.md",
  "functions_documented": 7,
  "classes_documented": 2,
  "module_docstring": "A tiny CLI TODO manager."
}
```

## What it generates

| Section | Content |
|---------|---------|
| Overview | Module docstring + file path |
| Functions | Signature, docstring, parameter table |
| Classes | Class docstring, methods, attributes |
| Types | TypedDict / dataclass fields (if present) |

It is structural documentation — the agent should add usage examples and architectural notes.

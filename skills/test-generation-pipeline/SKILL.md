---
name: test-generation-pipeline
description: Generate pytest test skeletons for a Python module by AST parsing. Extracts function signatures and produces parametrised tests, edge-case stubs, and fixture hints. Pure Python, no LLM dependency.
---

# Test Generation Pipeline

Use this skill when a tester agent needs a quick pytest scaffold for a module. It does not write deep behavioural tests — it produces the mechanical 80%: imports, parametrised signatures, edge-case placeholders, and fixture hints.

## Inputs

- `target_path` (required, str) — path to a `.py` file.
- `output_path` (required, str) — where to write the generated test file.
- `style` (optional, str) — `pytest` (default) or `unittest`.

## Outputs

```json
{
  "test_path": "/path/to/test_foo.py",
  "functions_tested": ["add", "subtract", "divide"],
  "test_count": 7,
  "fixture_hints": ["tmp_path", "monkeypatch"],
  "notes": "Edge cases marked with # TODO"
}
```

## What it generates

| Category | Behaviour |
|----------|-----------|
| Imports | `import` target module + `import pytest` |
| Signature mapping | One `test_<func>` per public function |
| Parametrised cases | 2–3 basic cases per function (happy path + zero/empty) |
| Edge stubs | `# TODO: edge case — overflow, NaN, unicode, etc.` |
| Fixture hints | Suggests fixtures when file does I/O or env vars |

It is a starting scaffold — the tester agent must fill in meaningful assertions.

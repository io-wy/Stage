---
name: data-processing-pipeline
description: Clean and transform tabular data (CSV/JSON). Performs deduplication, null-row removal, string trimming, and column normalization. Useful for ETL tasks that don't need pandas — uses only Python standard library.
---

# Data Processing Pipeline

Use this skill when you need to clean a CSV or JSON-array file without spinning up a full pandas workflow. It handles the common 80%: dedupe, drop nulls, normalize.

## Inputs

- `input_path` (required, str) — path to a `.csv` or `.json` file.
- `output_path` (required, str) — where to write the cleaned data.
- `operations` (optional, list[str]) — any of `dedupe`, `drop_empty_rows`, `trim_strings`, `normalize_columns`. Default: all four.
- `output_format` (optional, str) — `csv` or `json`. Default: same as input.

## Outputs

```json
{
  "output_path": "/path/to/cleaned.csv",
  "rows_in": 1000,
  "rows_out": 875,
  "operations_applied": ["dedupe", "drop_empty_rows", "trim_strings", "normalize_columns"],
  "duplicates_removed": 100,
  "empty_rows_removed": 25
}
```

## Operations

| Name | Behavior |
|------|----------|
| `dedupe` | Remove exact-duplicate rows (all columns equal). |
| `drop_empty_rows` | Drop rows where every value is empty/whitespace-only. |
| `trim_strings` | `.strip()` on every string value. |
| `normalize_columns` | Column names → lowercase, spaces/dashes → underscores. |

JSON input must be a top-level array of objects; non-conforming JSON raises ValueError.

"""data-processing-pipeline entrypoint.

Tabular data cleaning using only Python standard library. Supports CSV and
JSON array input. Operations: dedupe, drop_empty_rows, trim_strings,
normalize_columns.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

_DEFAULT_OPERATIONS = ["dedupe", "drop_empty_rows", "trim_strings", "normalize_columns"]
_VALID_OPERATIONS = set(_DEFAULT_OPERATIONS)
_NORMALIZE_PATTERN = re.compile(r"[\s\-]+")


def _load_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        columns = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    return columns, rows


def _load_json(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("JSON input must be a top-level array of objects")
    if not data:
        return [], []
    if not all(isinstance(row, dict) for row in data):
        raise ValueError("JSON array elements must all be objects")
    columns: list[str] = []
    seen: set[str] = set()
    for row in data:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                columns.append(key)
    return columns, data


def _normalize_column(name: str) -> str:
    return _NORMALIZE_PATTERN.sub("_", name.strip().lower())


def _apply_normalize_columns(
    columns: list[str], rows: list[dict[str, Any]]
) -> tuple[list[str], list[dict[str, Any]]]:
    new_columns = [_normalize_column(c) for c in columns]
    rename_map = dict(zip(columns, new_columns))
    new_rows = [
        {rename_map.get(k, k): v for k, v in row.items()}
        for row in rows
    ]
    return new_columns, new_rows


def _apply_trim_strings(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {k: (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
        for row in rows
    ]


def _is_empty_row(row: dict[str, Any]) -> bool:
    for v in row.values():
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return False
    return True


def _apply_drop_empty_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    kept = [r for r in rows if not _is_empty_row(r)]
    removed = len(rows) - len(kept)
    return kept, removed


def _apply_dedupe(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    seen: set[str] = set()
    kept: list[dict[str, Any]] = []
    for row in rows:
        key = json.dumps(row, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        kept.append(row)
    return kept, len(rows) - len(kept)


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in columns})


def _write_json(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


async def run_openagent_skill(payload: dict[str, Any]) -> dict[str, Any]:
    """Execute the data processing pipeline."""
    input_path = payload.get("input_path")
    output_path = payload.get("output_path")
    operations = payload.get("operations") or list(_DEFAULT_OPERATIONS)
    output_format = payload.get("output_format")

    if not input_path:
        raise ValueError("data-processing-pipeline: payload must include 'input_path'")
    if not output_path:
        raise ValueError("data-processing-pipeline: payload must include 'output_path'")

    invalid = [op for op in operations if op not in _VALID_OPERATIONS]
    if invalid:
        raise ValueError(f"Unknown operations: {invalid}. Valid: {sorted(_VALID_OPERATIONS)}")

    input_p = Path(input_path)
    if not input_p.exists():
        raise FileNotFoundError(f"input_path does not exist: {input_path}")

    suffix = input_p.suffix.lower()
    if suffix == ".csv":
        columns, rows = _load_csv(input_p)
        source_format = "csv"
    elif suffix == ".json":
        columns, rows = _load_json(input_p)
        source_format = "json"
    else:
        raise ValueError(f"Unsupported input format: {suffix}. Use .csv or .json.")

    rows_in = len(rows)
    duplicates_removed = 0
    empty_rows_removed = 0

    if "normalize_columns" in operations:
        columns, rows = _apply_normalize_columns(columns, rows)
    if "trim_strings" in operations:
        rows = _apply_trim_strings(rows)
    if "drop_empty_rows" in operations:
        rows, empty_rows_removed = _apply_drop_empty_rows(rows)
    if "dedupe" in operations:
        rows, duplicates_removed = _apply_dedupe(rows)

    target_format = output_format or source_format
    if target_format not in ("csv", "json"):
        raise ValueError(f"Unsupported output_format: {target_format}")

    output_p = Path(output_path)
    if target_format == "csv":
        _write_csv(output_p, columns, rows)
    else:
        _write_json(output_p, rows)

    return {
        "output_path": str(output_p),
        "rows_in": rows_in,
        "rows_out": len(rows),
        "operations_applied": [op for op in _DEFAULT_OPERATIONS if op in operations],
        "duplicates_removed": duplicates_removed,
        "empty_rows_removed": empty_rows_removed,
    }

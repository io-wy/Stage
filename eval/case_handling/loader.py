"""Loader for local wiki-derived case specs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from eval.case_handling.schema import CaseSpec


@dataclass(frozen=True, slots=True)
class CaseLoadError:
    """Structured case-load failure."""

    path: Path
    message: str


def _iter_case_files(root: Path) -> list[Path]:
    files = list(root.rglob("*.yaml")) + list(root.rglob("*.yml"))
    return sorted({path.resolve() for path in files})


def load_case_specs(
    root: str | Path,
    *,
    limit: int | None = None,
    family: str | None = None,
    difficulty: str | None = None,
    errors_out: list[CaseLoadError] | None = None,
) -> list[CaseSpec]:
    """Load normalized case specs from YAML fixtures."""

    root_path = Path(root)
    cases: list[CaseSpec] = []
    for path in _iter_case_files(root_path):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("case file must contain a mapping")
            case = CaseSpec(**raw)
        except Exception as exc:  # pragma: no cover - exercised via tests
            error = CaseLoadError(path=path, message=str(exc))
            if errors_out is not None:
                errors_out.append(error)
                continue
            raise ValueError(f"{path.name}: {exc}") from exc

        if family is not None and case.family != family:
            continue
        if difficulty is not None and case.difficulty != difficulty:
            continue

        cases.append(case)
        if limit is not None and len(cases) >= limit:
            break

    return cases


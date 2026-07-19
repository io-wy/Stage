"""Whole-output safety scanning for Stage governance."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from openagents_orchestration.governance.models import SafetyFinding

_DEFAULT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("credential_marker", "sastsast"),
    ("credential_marker", "sast_forever"),
    ("credential_marker", "saster"),
    ("credential_marker", "shared account"),
    ("credential_marker", "credential"),
)


@dataclass(frozen=True, slots=True)
class SafetyScanResult:
    blocked: bool
    findings: list[SafetyFinding]


def scan_public_output(
    payload: Any,
    *,
    forbidden_patterns: Iterable[str] | None = None,
) -> SafetyScanResult:
    """Scan a structured output object for sensitive strings."""

    findings: list[SafetyFinding] = []
    patterns = tuple(forbidden_patterns or (pattern for _, pattern in _DEFAULT_PATTERNS))
    normalized_patterns = [(name, pattern.lower()) for name, pattern in _DEFAULT_PATTERNS]
    extra_patterns = [pattern.lower() for pattern in patterns if pattern.lower() not in {p for _, p in normalized_patterns}]
    normalized_patterns.extend(("custom_pattern", pattern) for pattern in extra_patterns)

    def visit(value: Any, path: list[str]) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                visit(child, path + [str(key)])
            return
        if isinstance(value, (list, tuple)):
            for idx, child in enumerate(value):
                visit(child, path + [str(idx)])
            return
        if isinstance(value, str):
            lowered = value.lower()
            for finding_type, pattern in normalized_patterns:
                if pattern and pattern in lowered:
                    findings.append(
                        SafetyFinding(
                            case_id="",
                            run_id="",
                            surface=".".join(path) or "$",
                            severity="high",
                            finding_type=finding_type,
                            blocked=True,
                            path=path,
                            summary="sensitive marker found in public output",
                        )
                    )
                    break

    visit(payload, [])
    return SafetyScanResult(blocked=bool(findings), findings=findings)

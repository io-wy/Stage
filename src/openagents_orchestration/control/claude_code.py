"""Claude Code backend import adapter for Stage governance."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openagents_orchestration.control.audit import AuditStore
from openagents_orchestration.control.models import (
    CaseAuditEvent,
    ToolInvocationRecord,
)
from openagents_orchestration.control.safety import scan_public_output

_FORBIDDEN_PATTERNS = (
    "sastsast",
    "sast_forever",
    "saster",
    "shared account",
    "credential",
)


@dataclass(frozen=True, slots=True)
class ClaudeCodeImport:
    tool_invocation: ToolInvocationRecord
    public_case_result: dict[str, Any]
    raw_case_result_path: str
    timing: dict[str, Any] = field(default_factory=dict)
    grading: dict[str, Any] = field(default_factory=dict)


class ClaudeCodeAdapter:
    """Import-first adapter for Claude Code baseline artifacts."""

    def __init__(self, audit_store: AuditStore | None = None):
        self._audit_store = audit_store

    def import_run(
        self,
        *,
        case_id: str,
        run_id: str,
        artifact_dir: str | Path,
    ) -> ClaudeCodeImport:
        base = Path(artifact_dir)
        case_result_path = self._find_required(base, "case_result.json")
        timing_path = self._find_optional(base, "timing.json")
        grading_path = self._find_optional(base, "grading.json")

        raw_case_result = json.loads(case_result_path.read_text(encoding="utf-8"))
        public_case_result = self._sanitize_value(raw_case_result)
        timing = self._read_json(timing_path)
        grading = self._read_json(grading_path)

        tool_invocation = ToolInvocationRecord(
            case_id=case_id,
            run_id=run_id,
            backend="claude_code",
            input_ref=str(base),
            output_ref=str(case_result_path),
            duration_seconds=timing.get("total_duration_seconds"),
            token_cost=timing.get("total_tokens"),
            status="imported",
            metadata={
                "grading_summary": grading.get("summary", {}),
                "artifact_dir": str(base),
            },
        )

        if self._audit_store is not None:
            self._audit_store.append(
                CaseAuditEvent(
                    case_id=case_id,
                    run_id=run_id,
                    event_type="tool_invoked",
                    payload={
                        "backend": "claude_code",
                        "output_ref": str(case_result_path),
                        "duration_seconds": timing.get("total_duration_seconds"),
                    },
                )
            )

        return ClaudeCodeImport(
            tool_invocation=tool_invocation,
            public_case_result=public_case_result,
            raw_case_result_path=str(case_result_path),
            timing=timing,
            grading=grading,
        )

    @staticmethod
    def _read_json(path: Path | None) -> dict[str, Any]:
        if path is None or not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _find_required(base: Path, filename: str) -> Path:
        for path in base.rglob(filename):
            return path
        raise FileNotFoundError(f"{filename} not found under {base}")

    @staticmethod
    def _find_optional(base: Path, filename: str) -> Path | None:
        for path in base.rglob(filename):
            return path
        return None

    def _sanitize_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self._sanitize_value(child) for key, child in value.items()}
        if isinstance(value, list):
            return [self._sanitize_value(child) for child in value]
        if isinstance(value, str):
            sanitized = value
            for pattern in _FORBIDDEN_PATTERNS:
                sanitized = re.sub(
                    re.escape(pattern),
                    "[redacted]",
                    sanitized,
                    flags=re.IGNORECASE,
                )
            if scan_public_output(sanitized, forbidden_patterns=_FORBIDDEN_PATTERNS).blocked:
                return "[redacted]"
            return sanitized
        return value

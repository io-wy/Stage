"""code-review-pipeline entrypoint.

Heuristic static code review. Scans a target path, flags common issues,
writes a markdown report. No LLM dependency — pure Python.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_CODE_EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java"}

_SECURITY_PATTERNS = [
    (re.compile(r"\beval\s*\("), "high", "use of eval() — code injection risk"),
    (re.compile(r"\bexec\s*\("), "high", "use of exec() — code injection risk"),
    (re.compile(r"\bos\.system\s*\("), "high", "os.system() — prefer subprocess with explicit args"),
    (re.compile(r"shell\s*=\s*True"), "medium", "subprocess with shell=True"),
    (re.compile(r"password\s*=\s*[\"']\w+[\"']", re.IGNORECASE), "high", "hardcoded password literal"),
    (re.compile(r"api[_-]?key\s*=\s*[\"'][^\"']+[\"']", re.IGNORECASE), "high", "hardcoded API key literal"),
    (re.compile(r"secret\s*=\s*[\"'][^\"']+[\"']", re.IGNORECASE), "high", "hardcoded secret literal"),
]

_TODO_PATTERN = re.compile(r"#\s*(TODO|FIXME|XXX|HACK)[:\s](.{0,80})", re.IGNORECASE)
_WILDCARD_IMPORT = re.compile(r"^from\s+\S+\s+import\s+\*")


@dataclass
class _Issue:
    file: str
    line: int
    severity: str
    category: str
    message: str


@dataclass
class _ReviewResult:
    files_analyzed: int = 0
    issues: list[_Issue] = field(default_factory=list)

    @property
    def issues_by_severity(self) -> dict[str, int]:
        counts: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
        for issue in self.issues:
            counts[issue.severity] = counts.get(issue.severity, 0) + 1
        return counts


def _iter_code_files(target: Path, max_files: int) -> list[Path]:
    if target.is_file():
        return [target] if target.suffix in _CODE_EXTENSIONS else []
    found: list[Path] = []
    for path in target.rglob("*"):
        if len(found) >= max_files:
            break
        if path.is_file() and path.suffix in _CODE_EXTENSIONS:
            found.append(path)
    return found


def _scan_file(path: Path, focus: str) -> list[_Issue]:
    issues: list[_Issue] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return issues
    lines = text.splitlines()

    check_quality = focus in ("quality", "both")
    check_security = focus in ("security", "both")

    # File length
    if check_quality and len(lines) > 800:
        issues.append(_Issue(
            file=str(path), line=1, severity="medium",
            category="length",
            message=f"File is {len(lines)} lines (> 800). Consider splitting.",
        ))

    # Per-line checks
    in_function = False
    function_start = 0
    function_name = ""
    for idx, line in enumerate(lines, start=1):
        # Function length tracking (Python-ish heuristic)
        stripped = line.strip()
        if path.suffix == ".py" and stripped.startswith("def ") or stripped.startswith("async def "):
            if in_function and check_quality:
                length = idx - function_start
                if length > 80:
                    issues.append(_Issue(
                        file=str(path), line=function_start, severity="low",
                        category="length",
                        message=f"Function '{function_name}' is {length} lines (> 80).",
                    ))
            in_function = True
            function_start = idx
            m = re.match(r"\s*(?:async\s+)?def\s+(\w+)", line)
            function_name = m.group(1) if m else "?"

        # Security patterns
        if check_security:
            for pattern, severity, message in _SECURITY_PATTERNS:
                if pattern.search(line):
                    issues.append(_Issue(
                        file=str(path), line=idx, severity=severity,
                        category="security", message=message,
                    ))

        # TODO comments
        if check_quality:
            m = _TODO_PATTERN.search(line)
            if m:
                kind = m.group(1).upper()
                snippet = m.group(2).strip()
                issues.append(_Issue(
                    file=str(path), line=idx, severity="low",
                    category="todo",
                    message=f"{kind}: {snippet}" if snippet else kind,
                ))

            # Wildcard imports
            if path.suffix == ".py" and _WILDCARD_IMPORT.match(line):
                issues.append(_Issue(
                    file=str(path), line=idx, severity="medium",
                    category="imports",
                    message="Wildcard import — explicit names are clearer.",
                ))

    # Final function check
    if in_function and check_quality:
        length = len(lines) - function_start + 1
        if length > 80:
            issues.append(_Issue(
                file=str(path), line=function_start, severity="low",
                category="length",
                message=f"Function '{function_name}' is {length} lines (> 80).",
            ))

    # Docstring check (Python)
    if check_quality and path.suffix == ".py" and lines:
        first_meaningful = next((ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")), "")
        if not first_meaningful.startswith(('"""', "'''")):
            issues.append(_Issue(
                file=str(path), line=1, severity="low",
                category="documentation",
                message="Module is missing a docstring.",
            ))

    return issues


def _render_report(target: str, result: _ReviewResult, focus: str) -> str:
    lines = [
        f"# Code Review Report",
        "",
        f"- **Target:** `{target}`",
        f"- **Focus:** {focus}",
        f"- **Files analyzed:** {result.files_analyzed}",
        f"- **Issues found:** {len(result.issues)}",
        "",
        "## Severity breakdown",
        "",
    ]
    counts = result.issues_by_severity
    for sev in ("high", "medium", "low"):
        lines.append(f"- **{sev}:** {counts.get(sev, 0)}")

    if not result.issues:
        lines.extend(["", "No issues found. Clean pass."])
        return "\n".join(lines)

    lines.extend(["", "## Issues", "", "| Severity | Category | File:Line | Message |", "|----------|----------|-----------|---------|"])
    for issue in sorted(result.issues, key=lambda i: ({"high": 0, "medium": 1, "low": 2}[i.severity], i.file, i.line)):
        rel_file = issue.file.replace("|", r"\|")
        msg = issue.message.replace("|", r"\|")
        lines.append(f"| {issue.severity} | {issue.category} | `{rel_file}:{issue.line}` | {msg} |")

    return "\n".join(lines)


async def run_openagent_skill(payload: dict[str, Any]) -> dict[str, Any]:
    """Execute the code review pipeline.

    Returns a dict with keys: report_path, files_analyzed, issues_count, issues_by_severity.
    Raises ValueError on missing required keys.
    """
    target_path = payload.get("target_path")
    output_path = payload.get("output_path")
    focus = payload.get("focus", "both")
    max_files = int(payload.get("max_files", 50))

    if not target_path:
        raise ValueError("code-review-pipeline: payload must include 'target_path'")
    if not output_path:
        raise ValueError("code-review-pipeline: payload must include 'output_path'")
    if focus not in ("quality", "security", "both"):
        raise ValueError(f"code-review-pipeline: invalid focus '{focus}'")

    target = Path(target_path)
    if not target.exists():
        raise FileNotFoundError(f"target_path does not exist: {target_path}")

    files = _iter_code_files(target, max_files)
    result = _ReviewResult(files_analyzed=len(files))
    for path in files:
        result.issues.extend(_scan_file(path, focus))

    report_text = _render_report(target_path, result, focus)
    output_p = Path(output_path)
    output_p.parent.mkdir(parents=True, exist_ok=True)
    output_p.write_text(report_text, encoding="utf-8")

    return {
        "report_path": str(output_p),
        "files_analyzed": result.files_analyzed,
        "issues_count": len(result.issues),
        "issues_by_severity": result.issues_by_severity,
    }

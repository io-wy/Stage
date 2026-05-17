"""health-check-pipeline entrypoint.

Runs a suite of health checks and produces a structured report.
Supports: bash commands, file existence, file content matching.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any


_REPORT_TEMPLATE = """\
# Health Check Report

| # | Check | Status | Time (ms) | Details |
|---|-------|--------|-----------|---------|
{rows}

---

**Summary:** {passed}/{total} passed
"""


async def run_openagent_skill(payload: dict[str, Any]) -> dict[str, Any]:
    """Execute the health check pipeline."""
    checks = payload.get("checks")
    output_path = payload.get("output_path")

    if not checks or not isinstance(checks, list):
        raise ValueError("health-check-pipeline: payload must include 'checks' as a list")
    if not output_path:
        raise ValueError("health-check-pipeline: payload must include 'output_path'")

    details: list[dict[str, Any]] = []
    passed = 0
    failed = 0

    for i, check in enumerate(checks, 1):
        if not isinstance(check, dict):
            details.append({
                "check": f"check #{i}",
                "status": "failed",
                "elapsed_ms": 0.0,
                "error": "check item is not a dict",
            })
            failed += 1
            continue

        check_type = check.get("type", "")
        target = check.get("target", "")
        expected = check.get("expected")
        name = check.get("name") or f"{check_type}: {target}"

        start = time.perf_counter()
        try:
            if check_type == "bash":
                expected_code = int(expected) if expected is not None else 0
                proc = subprocess.run(
                    target,
                    shell=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60.0,
                )
                ok = proc.returncode == expected_code
                if not ok:
                    err = f"exit code {proc.returncode} (expected {expected_code})"
                    if proc.stderr:
                        err += f", stderr: {proc.stderr[:200]}"
                    details.append({
                        "check": name,
                        "status": "failed",
                        "elapsed_ms": round((time.perf_counter() - start) * 1000, 1),
                        "error": err,
                    })
                    failed += 1
                else:
                    details.append({
                        "check": name,
                        "status": "passed",
                        "elapsed_ms": round((time.perf_counter() - start) * 1000, 1),
                    })
                    passed += 1

            elif check_type == "file_exists":
                ok = Path(target).exists()
                if ok:
                    details.append({
                        "check": name,
                        "status": "passed",
                        "elapsed_ms": round((time.perf_counter() - start) * 1000, 1),
                    })
                    passed += 1
                else:
                    details.append({
                        "check": name,
                        "status": "failed",
                        "elapsed_ms": round((time.perf_counter() - start) * 1000, 1),
                        "error": f"file not found: {target}",
                    })
                    failed += 1

            elif check_type == "file_contains":
                if expected is None:
                    details.append({
                        "check": name,
                        "status": "failed",
                        "elapsed_ms": round((time.perf_counter() - start) * 1000, 1),
                        "error": "'expected' substring required for file_contains",
                    })
                    failed += 1
                else:
                    p = Path(target)
                    if not p.exists():
                        details.append({
                            "check": name,
                            "status": "failed",
                            "elapsed_ms": round((time.perf_counter() - start) * 1000, 1),
                            "error": f"file not found: {target}",
                        })
                        failed += 1
                    else:
                        content = p.read_text(encoding="utf-8", errors="replace")
                        ok = str(expected) in content
                        if ok:
                            details.append({
                                "check": name,
                                "status": "passed",
                                "elapsed_ms": round((time.perf_counter() - start) * 1000, 1),
                            })
                            passed += 1
                        else:
                            details.append({
                                "check": name,
                                "status": "failed",
                                "elapsed_ms": round((time.perf_counter() - start) * 1000, 1),
                                "error": f"substring not found in {target}",
                            })
                            failed += 1

            else:
                details.append({
                    "check": name,
                    "status": "failed",
                    "elapsed_ms": round((time.perf_counter() - start) * 1000, 1),
                    "error": f"unknown check type: {check_type}",
                })
                failed += 1

        except subprocess.TimeoutExpired:
            details.append({
                "check": name,
                "status": "failed",
                "elapsed_ms": round((time.perf_counter() - start) * 1000, 1),
                "error": "command timed out after 60s",
            })
            failed += 1
        except Exception as exc:
            details.append({
                "check": name,
                "status": "failed",
                "elapsed_ms": round((time.perf_counter() - start) * 1000, 1),
                "error": str(exc),
            })
            failed += 1

    # Render report
    rows: list[str] = []
    for i, d in enumerate(details, 1):
        status_icon = "✓" if d["status"] == "passed" else "✗"
        err = d.get("error", "")
        rows.append(
            f"| {i} | {d['check']} | {status_icon} {d['status']} | {d['elapsed_ms']} | {err} |"
        )

    report = _REPORT_TEMPLATE.format(
        rows="\n".join(rows),
        passed=passed,
        total=len(details),
    )

    output_p = Path(output_path)
    output_p.parent.mkdir(parents=True, exist_ok=True)
    output_p.write_text(report, encoding="utf-8")

    return {
        "report_path": str(output_p),
        "total": len(details),
        "passed": passed,
        "failed": failed,
        "details": details,
    }

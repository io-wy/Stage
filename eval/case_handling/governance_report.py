"""Comparison report for Stage governance vs the Claude Code baseline."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def build_governance_comparison_report(
    stage_summary: dict[str, Any],
    baseline_summary: dict[str, Any],
    *,
    output_path: str | Path | None = None,
    markdown_path: str | Path | None = None,
) -> dict[str, Any]:
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "baseline": _baseline_snapshot(baseline_summary),
        "stage": _stage_snapshot(stage_summary),
        "delta": _delta_snapshot(stage_summary, baseline_summary),
        "hard_gate_pass_rates": _hard_gate_pass_rates(stage_summary),
        "failure_classes": _failure_classes(stage_summary),
        "top_regressions": _top_regressions(stage_summary, baseline_summary),
        "audit_completeness": _audit_completeness(stage_summary),
        "per_eval": _per_eval_comparison(stage_summary, baseline_summary),
    }

    if output_path is not None:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps({**report, "report_path": str(output)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        report["report_path"] = str(output)
    else:
        report["report_path"] = None

    if markdown_path is not None:
        md_output = Path(markdown_path)
        md_output.parent.mkdir(parents=True, exist_ok=True)
        md_output.write_text(_render_markdown(report), encoding="utf-8")
        report["markdown_path"] = str(md_output)
    else:
        report["markdown_path"] = None

    return report


def _baseline_snapshot(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "total_evals": summary.get("total_evals", 0),
        "total_passed": summary.get("total_passed", 0),
        "total_assertions": summary.get("total_assertions", 0),
        "overall_pass_rate": summary.get("overall_pass_rate", 0.0),
        "closed_correct": summary.get("closed_correct", 0),
        "family_correct": summary.get("family_correct", 0),
        "secret_safe": summary.get("secret_safe", 0),
    }


def _stage_snapshot(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "total_evals": summary.get("total_evals", 0),
        "total_passed": summary.get("total_passed", 0),
        "total_assertions": summary.get("total_assertions", 0),
        "overall_pass_rate": summary.get("overall_pass_rate", 0.0),
        "closed_correct": summary.get("closed_correct", 0),
        "family_correct": summary.get("family_correct", 0),
        "secret_safe": summary.get("secret_safe", 0),
        "intent_correct": summary.get("intent_correct", 0),
        "domain_correct": summary.get("domain_correct", 0),
        "route_correct": summary.get("route_correct", 0),
        "safety_correct": summary.get("safety_correct", 0),
        "closure_correct": summary.get("closure_correct", 0),
        "evidence_correct": summary.get("evidence_correct", 0),
        "traceability_correct": summary.get("traceability_correct", 0),
        "human_handoff_correct": summary.get("human_handoff_correct", 0),
        "audit_complete": summary.get("audit_complete", 0),
        "label_leakage_safe": summary.get("label_leakage_safe", 0),
    }


def _delta_snapshot(stage_summary: dict[str, Any], baseline_summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "overall_pass_rate": round(
            stage_summary.get("overall_pass_rate", 0.0)
            - baseline_summary.get("overall_pass_rate", 0.0),
            2,
        ),
        "closed_correct": stage_summary.get("closed_correct", 0) - baseline_summary.get(
            "closed_correct", 0
        ),
        "family_correct": stage_summary.get("family_correct", 0) - baseline_summary.get(
            "family_correct", 0
        ),
        "secret_safe": stage_summary.get("secret_safe", 0) - baseline_summary.get(
            "secret_safe", 0
        ),
    }


def _hard_gate_pass_rates(summary: dict[str, Any]) -> dict[str, float]:
    total = max(summary.get("total_evals", 0), 1)
    return {
        "intent": round(summary.get("intent_correct", 0) / total, 2),
        "domain": round(summary.get("domain_correct", 0) / total, 2),
        "route": round(summary.get("route_correct", 0) / total, 2),
        "safety": round(summary.get("safety_correct", 0) / total, 2),
        "closure": round(summary.get("closure_correct", 0) / total, 2),
        "evidence": round(summary.get("evidence_correct", 0) / total, 2),
        "traceability": round(summary.get("traceability_correct", 0) / total, 2),
        "human_handoff": round(summary.get("human_handoff_correct", 0) / total, 2),
        "audit_complete": round(summary.get("audit_complete", 0) / total, 2),
        "label_leakage_safe": round(summary.get("label_leakage_safe", 0) / total, 2),
    }


def _failure_classes(summary: dict[str, Any]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for item in summary.get("evals", []):
        failure_mode = item.get("grade", {}).get("failure_mode")
        if failure_mode and item.get("grade", {}).get("actual_closed") is False:
            counts[str(failure_mode)] += 1
    return dict(counts)


def _top_regressions(
    stage_summary: dict[str, Any],
    baseline_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    baseline_by_id = {
        item.get("eval_id"): item for item in baseline_summary.get("evals", [])
    }
    regressions: list[dict[str, Any]] = []
    for item in stage_summary.get("evals", []):
        grade = item.get("grade", {})
        base = baseline_by_id.get(grade.get("eval_id"))
        if not base:
            continue
        delta = round(grade.get("pass_rate", 0.0) - base.get("pass_rate", 0.0), 2)
        if delta < 0:
            regressions.append(
                {
                    "eval_id": grade.get("eval_id"),
                    "eval_name": grade.get("eval_name"),
                    "baseline_pass_rate": base.get("pass_rate", 0.0),
                    "stage_pass_rate": grade.get("pass_rate", 0.0),
                    "delta": delta,
                    "failure_mode": grade.get("failure_mode"),
                }
            )
    regressions.sort(key=lambda item: item["delta"])
    return regressions[:5]


def _per_eval_comparison(
    stage_summary: dict[str, Any],
    baseline_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    baseline_by_id = {
        item.get("eval_id"): item for item in baseline_summary.get("evals", [])
    }
    rows: list[dict[str, Any]] = []
    for item in stage_summary.get("evals", []):
        grade = item.get("grade", {})
        base = baseline_by_id.get(grade.get("eval_id"), {})
        rows.append(
            {
                "eval_id": grade.get("eval_id"),
                "eval_name": grade.get("eval_name"),
                "stage_pass_rate": grade.get("pass_rate", 0.0),
                "baseline_pass_rate": base.get("pass_rate", 0.0),
                "delta": round(grade.get("pass_rate", 0.0) - base.get("pass_rate", 0.0), 2),
                "stage_closed": grade.get("actual_closed"),
                "baseline_closed": base.get("actual_closed"),
                "stage_family": grade.get("actual_family"),
                "baseline_family": base.get("actual_family"),
            }
        )
    return rows


def _audit_completeness(summary: dict[str, Any]) -> float:
    total = max(summary.get("total_evals", 0), 1)
    return round(summary.get("audit_complete", 0) / total, 2)


def _render_markdown(report: dict[str, Any]) -> str:
    baseline = report["baseline"]
    stage = report["stage"]
    delta = report["delta"]
    gates = report["hard_gate_pass_rates"]
    lines = [
        "# Stage Governance Comparison Report",
        "",
        f"Generated at: {report['generated_at']}",
        "",
        "## Summary",
        "",
        f"- Baseline overall pass rate: {baseline['overall_pass_rate']}",
        f"- Stage overall pass rate: {stage['overall_pass_rate']}",
        f"- Delta: {delta['overall_pass_rate']}",
        f"- Baseline closed correct: {baseline['closed_correct']}/{baseline['total_evals']}",
        f"- Stage closed correct: {stage['closed_correct']}/{stage['total_evals']}",
        f"- Baseline family correct: {baseline['family_correct']}/{baseline['total_evals']}",
        f"- Stage family correct: {stage['family_correct']}/{stage['total_evals']}",
        "",
        "## Hard Gates",
        "",
    ]
    for key, value in gates.items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## Top Regressions",
            "",
        ]
    )
    regressions = report.get("top_regressions", [])
    if not regressions:
        lines.append("- None")
    else:
        for item in regressions:
            lines.append(
                f"- Eval {item['eval_id']} {item['eval_name']}: {item['delta']}"
            )
    return "\n".join(lines) + "\n"

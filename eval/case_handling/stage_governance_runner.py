"""Stage-governed runner for the hard-v2 case-handling benchmark."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from eval.case_handling.governance_report import build_governance_comparison_report
from eval.case_handling.stage_governance_grading import (
    build_stage_governance_summary,
    extract_case_prompt,
    grade_hard_v2_eval,
)
from eval.case_handling.stage_governance_types import (
    HardV2EvalSpec,
    StageGovernanceBenchmarkSummary,
    StageGovernanceEvalResult,
)
from openagents_orchestration.governance.pipeline import (
    ClaudeCodeReplayBackend,
    StageGovernancePipeline,
)


def load_hard_v2_eval_specs(
    evals_json: str | Path,
    *,
    eval_ids: Iterable[int] | None = None,
) -> list[HardV2EvalSpec]:
    path = Path(evals_json)
    raw = json.loads(path.read_text(encoding="utf-8"))
    selected = set(eval_ids) if eval_ids is not None else None
    specs: list[HardV2EvalSpec] = []
    for item in raw.get("evals", []):
        if selected is not None and item.get("id") not in selected:
            continue
        specs.append(
            HardV2EvalSpec(
                eval_id=int(item["id"]),
                eval_name=str(item["name"]),
                family=str(item["family"]),
                business_process=str(item["business_process"]),
                expected_closed=bool(item["expected_closed"]),
                prompt=str(item["prompt"]),
                assertions=list(item.get("assertions", [])),
                files=[str(file) for file in item.get("files", [])],
            )
        )
    return specs


def run_stage_governance_benchmark(
    *,
    evals_json: str | Path,
    baseline_workspace: str | Path,
    output_root: str | Path,
    eval_ids: Iterable[int] | None = None,
    baseline_summary_path: str | Path | None = None,
) -> StageGovernanceBenchmarkSummary:
    baseline_workspace_path = Path(baseline_workspace)
    output_root_path = Path(output_root)
    output_root_path.mkdir(parents=True, exist_ok=True)

    specs = load_hard_v2_eval_specs(evals_json, eval_ids=eval_ids)
    eval_results = [
        run_stage_governance_eval(
            spec,
            baseline_workspace=baseline_workspace_path,
            output_root=output_root_path,
        )
        for spec in specs
    ]

    summary = build_stage_governance_summary(eval_results, output_root_path)
    summary_path = output_root_path / "stage_grading_summary.json"
    summary.summary_path = str(summary_path)
    summary_path.write_text(
        json.dumps(summary.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    baseline_summary_file = (
        Path(baseline_summary_path)
        if baseline_summary_path is not None
        else baseline_workspace_path / "grading_summary.json"
    )
    if baseline_summary_file.exists():
        report = build_governance_comparison_report(
            summary.to_dict(),
            json.loads(baseline_summary_file.read_text(encoding="utf-8")),
            output_path=output_root_path / "governance_report.json",
            markdown_path=output_root_path / "governance_report.md",
        )
        summary.report_path = report["report_path"]

    return summary


def run_stage_governance_eval(
    spec: HardV2EvalSpec,
    *,
    baseline_workspace: str | Path,
    output_root: str | Path,
) -> StageGovernanceEvalResult:
    baseline_workspace_path = Path(baseline_workspace)
    baseline_eval_dir = baseline_workspace_path / f"eval-{spec.eval_id}-{spec.eval_name}"
    baseline_outputs_dir = baseline_eval_dir / "outputs"
    if not baseline_outputs_dir.exists():
        raise FileNotFoundError(f"baseline outputs missing: {baseline_outputs_dir}")

    run_dir = Path(output_root) / f"eval-{spec.eval_id}-{spec.eval_name}"
    outputs_dir = run_dir / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    audit_path = run_dir / "audit.jsonl"
    governance_path = run_dir / "governance.json"
    case_result_path = outputs_dir / "case_result.json"

    case_id = f"hard-v2-{spec.eval_id}"
    run_id = f"stage-hard-v2-{spec.eval_id}"
    pipeline_result = StageGovernancePipeline().run(
        prompt=spec.prompt,
        routing_prompt=extract_case_prompt(spec.prompt),
        case_id=case_id,
        run_id=run_id,
        backend=ClaudeCodeReplayBackend(baseline_outputs_dir),
        audit_path=audit_path,
    )

    governed_case_result = pipeline_result.governed_case_result
    governance_payload = {
        **pipeline_result.governance_payload,
        "case_result_path": str(case_result_path),
    }
    governance_path.write_text(
        json.dumps(governance_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    case_result_path.write_text(
        json.dumps(governed_case_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    grade = grade_hard_v2_eval(spec, governed_case_result)
    return StageGovernanceEvalResult(
        eval_id=spec.eval_id,
        eval_name=spec.eval_name,
        expected_family=spec.family,
        expected_closed=spec.expected_closed,
        expected_business_process=spec.business_process,
        baseline_case_result_path=str(baseline_outputs_dir / "case_result.json"),
        case_result_path=str(case_result_path),
        governance_path=str(governance_path),
        audit_path=str(audit_path),
        intent_frame=pipeline_result.intent_frame.to_dict(),
        domain_profile=pipeline_result.domain_profile.to_dict(),
        route_plan=pipeline_result.route_plan.to_dict(),
        governance=governance_payload,
        governed_case_result=governed_case_result,
        grade=grade,
    )

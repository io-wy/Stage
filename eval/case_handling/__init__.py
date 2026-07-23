"""Case-handling benchmark package."""

from eval.case_handling.governance_report import build_governance_comparison_report
from eval.case_handling.schema import CaseSpec
from eval.case_handling.stage_governance_demo import (
    DEFAULT_DEMO_EVAL_IDS,
    build_stage_governance_demo,
)
from eval.case_handling.stage_governance_runner import (
    HardV2EvalSpec,
    StageGovernanceBenchmarkSummary,
    StageGovernanceEvalResult,
    load_hard_v2_eval_specs,
    run_stage_governance_benchmark,
    run_stage_governance_eval,
)

__all__ = [
    "CaseSpec",
    "DEFAULT_DEMO_EVAL_IDS",
    "HardV2EvalSpec",
    "StageGovernanceBenchmarkSummary",
    "StageGovernanceEvalResult",
    "build_stage_governance_demo",
    "build_governance_comparison_report",
    "load_hard_v2_eval_specs",
    "run_stage_governance_benchmark",
    "run_stage_governance_eval",
]

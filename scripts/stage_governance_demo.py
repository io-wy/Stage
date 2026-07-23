"""Run the Stage governance service demo."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> None:
    from eval.case_handling.stage_governance_demo import build_stage_governance_demo

    parser = argparse.ArgumentParser(description="Run the Stage governance demo.")
    parser.add_argument(
        "--evals-json",
        default="skills/case-handling-baseline/evals/evals.json",
    )
    parser.add_argument(
        "--baseline-workspace",
        default="skills/case-handling-baseline-workspace/hard-v2-baseline",
    )
    parser.add_argument("--output-root", default="docs/reports/stage-governance-demo")
    args = parser.parse_args()

    report = build_stage_governance_demo(
        evals_json=Path(args.evals_json),
        baseline_workspace=Path(args.baseline_workspace),
        output_root=Path(args.output_root),
    )
    print(report["markdown_path"])


if __name__ == "__main__":
    main()

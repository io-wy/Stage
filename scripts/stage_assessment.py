#!/usr/bin/env python
"""Run the comprehensive Stage assessment suite."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from eval.stage_assessment import (  # noqa: E402
    default_stage_assessment_cases,
    run_stage_assessment,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        default="docs/reports/stage-assessment",
        help="Directory for assessment artifacts.",
    )
    args = parser.parse_args()

    summary = run_stage_assessment(
        cases=default_stage_assessment_cases(),
        output_root=Path(args.output_root),
    )
    print(
        json.dumps(
            {
                "total_cases": summary.total_cases,
                "passed_cases": summary.passed_cases,
                "metrics": summary.metrics,
                "summary_path": summary.summary_path,
                "report_path": summary.report_path,
                "feedback_artifact_count": summary.feedback_artifact_count,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

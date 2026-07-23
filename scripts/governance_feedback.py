#!/usr/bin/env python
"""Record governance feedback for an observed case."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
for path in (REPO_ROOT, SRC_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def main() -> None:
    from openagents_orchestration.governance.feedback import (
        CaseFeedbackRecord,
        write_feedback_artifacts,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--governance", required=True, help="Path to governance.json")
    parser.add_argument("--case-result", required=True, help="Path to case_result.json")
    parser.add_argument("--case-id", help="Override case id")
    parser.add_argument("--prompt", help="Original online case prompt")
    parser.add_argument("--label", action="append", default=[], help="Feedback label")
    parser.add_argument("--note", default="", help="Human note for the feedback record")
    parser.add_argument(
        "--output-root",
        default="docs/feedback",
        help="Directory where feedback artifacts will be written",
    )
    args = parser.parse_args()

    governance = _read_json(args.governance)
    case_result = _read_json(args.case_result)
    case_id = args.case_id or str(governance.get("case_id") or case_result.get("case_id"))
    if not case_id or case_id == "None":
        raise SystemExit("case id is required when it is absent from governance/case result")
    prompt = args.prompt or str(governance.get("routing_prompt", ""))
    record = CaseFeedbackRecord.from_governance(
        case_id=case_id,
        prompt=prompt,
        governance=governance,
        governed_case_result=case_result,
        labels=list(args.label),
        note=args.note,
    )
    paths = write_feedback_artifacts(record, output_dir=args.output_root)
    print(paths["markdown"])


def _read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()

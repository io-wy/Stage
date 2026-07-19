"""Tests for Claude Code artifact import into Stage governance."""

from __future__ import annotations

import json

from openagents_orchestration.governance.claude_code import ClaudeCodeAdapter


def test_import_run_captures_tool_metadata_and_public_output(tmp_path) -> None:
    artifact_dir = tmp_path / "run"
    outputs_dir = artifact_dir / "outputs"
    run_dir = artifact_dir / "run-1"
    outputs_dir.mkdir(parents=True)
    run_dir.mkdir(parents=True)

    (outputs_dir / "case_result.json").write_text(
        json.dumps(
            {
                "closed": True,
                "family": "knowledge",
                "answer": "Use SSO instead of sharing credentials.",
                "evidence": [
                    {
                        "file": "/docs/nas.md",
                        "summary": "deprecated shared account marker",
                    }
                ],
                "redactions": [
                    {"item": "shared account secret", "reason": "redacted"}
                ],
                "actions": ["answer_user"],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "timing.json").write_text(
        json.dumps({"total_duration_seconds": 4.2, "total_tokens": 123}),
        encoding="utf-8",
    )
    (run_dir / "grading.json").write_text(
        json.dumps({"summary": {"pass_rate": 1.0}}),
        encoding="utf-8",
    )

    adapter = ClaudeCodeAdapter()
    imported = adapter.import_run(
        case_id="case-1",
        run_id="run-1",
        artifact_dir=artifact_dir,
    )

    assert imported.tool_invocation.backend == "claude_code"
    assert imported.tool_invocation.duration_seconds == 4.2
    assert imported.tool_invocation.token_cost == 123
    assert imported.tool_invocation.output_ref.endswith("case_result.json")
    assert imported.public_case_result["closed"] is True
    assert imported.public_case_result["family"] == "knowledge"
    assert "[redacted]" in imported.public_case_result["evidence"][0]["summary"]
    assert "shared account" not in imported.public_case_result["evidence"][0]["summary"].lower()

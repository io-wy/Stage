"""Tests for Stage governance safety scanning."""

from __future__ import annotations

from openagents_orchestration.control.safety import scan_public_output


def test_scan_public_output_accepts_clean_payload() -> None:
    payload = {
        "answer": "Provide the NAS retry path.",
        "evidence": [{"summary": "NAS registration path exists"}],
        "redactions": [],
    }

    result = scan_public_output(payload)

    assert result.blocked is False
    assert result.findings == []


def test_scan_public_output_finds_nested_secret_paths() -> None:
    payload = {
        "answer": "Use SSO instead of sharing credentials.",
        "evidence": [
            {"summary": "safe summary"},
            {"summary": "deprecated credential placeholder"},
        ],
        "redactions": [
            {"item": "shared account secret", "reason": "redacted"},
        ],
    }

    result = scan_public_output(payload)

    assert result.blocked is True
    assert any(f.path == ["evidence", "1", "summary"] for f in result.findings)
    assert any(f.path == ["redactions", "0", "item"] for f in result.findings)

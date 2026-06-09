"""Tests for deterministic verification reports."""

from __future__ import annotations

from openagents_orchestration.reporting.verifier import build_verification_report
from openagents_orchestration.state_board import StateBoard


def test_verification_report_detects_tests_and_artifacts(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_ok():\n    assert True\n")
    (tmp_path / "alembic" / "versions").mkdir(parents=True)
    (tmp_path / "alembic" / "versions" / "0001_init.py").write_text("revision = '0001'\n")

    board = StateBoard("obj", echo=False)
    board.verify_artifact(str(tmp_path / "app.py"), exists=True)
    report = build_verification_report(board, work_dir=tmp_path)

    assert report["signals"]["has_verified_artifacts"] is True
    assert report["signals"]["has_tests"] is True
    assert report["signals"]["has_migrations"] is True
    assert any(path.endswith("test_app.py") for path in report["test_files_present"])
    assert any(path.endswith("0001_init.py") for path in report["migration_files_present"])

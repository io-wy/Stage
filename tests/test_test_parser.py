"""Tests for test output parser utility."""

from __future__ import annotations

import pytest

from openagents_orchestration.tools.corecoder.test_parser import (
    ParseTestOutputTool,
    parse_pytest_output,
)


def test_parse_pytest_failure():
    output = """
============================= test session starts ==============================
platform linux -- Python 3.11.0

tests/test_foo.py::test_one PASSED
tests/test_foo.py::test_two FAILED

=================================== FAILURES ===================================
________________________________ test_two ________________________________

    def test_two():
>       assert 1 == 2
E       AssertionError: assert 1 == 2

tests/test_foo.py:8: AssertionError
=========================== short test summary info ============================
FAILED tests/test_foo.py::test_two - AssertionError: assert 1 == 2
"""
    failures = parse_pytest_output(output)
    assert len(failures) >= 1
    assert any(
        f.get("file") == "tests/test_foo.py" and f.get("test") == "test_two"
        for f in failures
    )


def test_parse_ruff_output():
    output = """src/main.py:42:5: E999 SyntaxError: invalid syntax
src/utils.py:10:1: F401 imported but unused
Found 2 errors.
"""
    failures = parse_pytest_output(output)
    assert len(failures) == 2
    assert failures[0]["file"] == "src/main.py"
    assert failures[0]["line"] == 42
    assert "E999" in failures[0]["error"]


def test_parse_empty_output():
    assert parse_pytest_output("") == []


@pytest.mark.asyncio
async def test_parse_test_output_tool():
    tool = ParseTestOutputTool()
    result = await tool.invoke(
        {"output": "src/foo.py:10: error: something\n"}, context=None
    )
    assert result["count"] == 1
    assert result["failures"][0]["file"] == "src/foo.py"
    assert result["failures"][0]["line"] == 10

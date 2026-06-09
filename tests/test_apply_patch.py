"""Tests for apply_patch tool."""

from __future__ import annotations

import pytest

from openagents_orchestration.tools.corecoder.apply_patch import (
    ApplyPatchTool,
    _parse_patch,
)


class MockContext:
    def __init__(self):
        self.scratch: dict[str, object] = {}


@pytest.fixture
def tmp_file(tmp_path):
    f = tmp_path / "test.py"
    f.write_text(
        "def hello():\n    pass\n\ndef world():\n    return 1\n",
        encoding="utf-8",
    )
    return f


class TestApplyPatchTool:
    def test_single_file_single_hunk(self, tmp_file):
        import asyncio

        tool = ApplyPatchTool()
        path = str(tmp_file)
        patch = (
            f"--- a/{path}\n"
            f"+++ b/{path}\n"
            f"@@ -1,2 +1,2 @@\n"
            f" def hello():\n"
            f"-    pass\n"
            f"+    return 'hello'\n"
        )
        ctx = MockContext()
        result = asyncio.run(tool.invoke({"patch": patch}, ctx))
        assert result["applied"] == 1
        assert result["failed"] == 0
        content = tmp_file.read_text(encoding="utf-8")
        assert "return 'hello'" in content
        assert "pass" not in content

    def test_multi_file_patch(self, tmp_path):
        import asyncio
        import os

        f1 = tmp_path / "a.py"
        f1.write_text("x = 1\n")
        f2 = tmp_path / "b.py"
        f2.write_text("y = 2\n")

        p1 = str(f1)
        p2 = str(f2)
        patch = (
            f"--- a/{p1}\n"
            f"+++ b/{p1}\n"
            f"@@ -1,1 +1,1 @@\n"
            f"-x = 1\n"
            f"+x = 10\n"
            f"--- a/{p2}\n"
            f"+++ b/{p2}\n"
            f"@@ -1,1 +1,1 @@\n"
            f"-y = 2\n"
            f"+y = 20\n"
        )
        tool = ApplyPatchTool()
        ctx = MockContext()
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            result = asyncio.run(tool.invoke({"patch": patch}, ctx))
            assert result["applied"] == 2
            assert "x = 10" in f1.read_text(encoding="utf-8")
            assert "y = 20" in f2.read_text(encoding="utf-8")
        finally:
            os.chdir(old_cwd)

    def test_create_new_file(self, tmp_path):
        import asyncio
        import os

        patch = (
            "--- a/new.py\n"
            "+++ b/new.py\n"
            "@@ -0,0 +1,2 @@\n"
            "+def foo():\n"
            "+    pass\n"
        )
        tool = ApplyPatchTool()
        ctx = MockContext()
        # Need to run in the right directory
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            result = asyncio.run(tool.invoke({"patch": patch}, ctx))
            assert result["applied"] == 1
            assert (tmp_path / "new.py").exists()
        finally:
            os.chdir(old_cwd)

    def test_fuzzy_match_with_context(self, tmp_file):
        import asyncio

        tool = ApplyPatchTool()
        path = str(tmp_file)
        # Context lines help locate the change
        patch = (
            f"--- a/{path}\n"
            f"+++ b/{path}\n"
            f"@@ -3,2 +3,2 @@\n"
            f" def world():\n"
            f"-    return 1\n"
            f"+    return 42\n"
        )
        ctx = MockContext()
        result = asyncio.run(tool.invoke({"patch": patch}, ctx))
        assert result["applied"] == 1
        content = tmp_file.read_text(encoding="utf-8")
        assert "return 42" in content

    def test_mismatch_fails(self, tmp_path):
        import asyncio

        f = tmp_path / "bad.py"
        f.write_text("a = 1\n")

        patch = (
            "--- a/bad.py\n"
            "+++ b/bad.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-this does not exist\n"
            "+replaced\n"
        )
        tool = ApplyPatchTool()
        ctx = MockContext()
        result = asyncio.run(tool.invoke({"patch": patch}, ctx))
        assert result["failed"] == 1
        assert result["applied"] == 0


class TestParsePatch:
    def test_parse_single_file(self):
        text = (
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def foo():\n"
            "-    pass\n"
            "+    return 1\n"
        )
        patches = _parse_patch(text)
        assert len(patches) == 1
        assert patches[0].path == "src/main.py"
        assert len(patches[0].hunks) == 1
        hunk = patches[0].hunks[0]
        assert hunk.old_start == 1
        assert hunk.old_lines == ["def foo():", "    pass"]
        assert hunk.new_lines == ["def foo():", "    return 1"]

    def test_parse_multiple_files(self):
        text = (
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-x\n"
            "+y\n"
            "--- a/b.py\n"
            "+++ b/b.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-a\n"
            "+b\n"
        )
        patches = _parse_patch(text)
        assert len(patches) == 2
        assert patches[0].path == "a.py"
        assert patches[1].path == "b.py"

    def test_parse_empty(self):
        patches = _parse_patch("some random text")
        assert patches == []

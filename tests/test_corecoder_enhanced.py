"""Tests for enhanced CoreCoderPattern single-agent capabilities.

Covers: planning phase, verification nudge, exploration cache, and structured
error recovery. Uses mocked LLM clients and in-memory tools so no real API
calls are made.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from openagents.interfaces.run_context import RunContext, RunUsage
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin

from openagents_orchestration.patterns.corecoder import CoreCoderPattern


class FakeLLMClient:
    """Minimal fake LLM client for testing CoreCoderPattern."""

    def __init__(self, responses: list[Any]):
        self._responses = list(responses)
        self._index = 0
        self.provider_name = "openai_compatible"

    async def generate(self, **kwargs: Any) -> Any:
        if self._index >= len(self._responses):
            raise RuntimeError("No more fake responses")
        response = self._responses[self._index]
        self._index += 1
        return response


class FakeResponse:
    def __init__(
        self,
        *,
        output_text: str = "",
        content: list[dict[str, Any]] | None = None,
        tool_calls: list[Any] | None = None,
        usage: Any = None,
    ):
        self.output_text = output_text
        self.content = content or []
        self.tool_calls = tool_calls or []
        self.usage = usage


class FakeToolCall:
    def __init__(self, name: str, arguments: dict[str, Any], call_id: str = "call_1"):
        self.name = name
        self.arguments = arguments
        self.id = call_id


class FakeEventBus:
    async def emit(self, *args: Any, **kwargs: Any) -> None:
        pass


class NoOpTool(ToolPlugin):
    name = "no_op"
    description = "Does nothing"

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(concurrency_safe=True, side_effects="readonly")

    def schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        return {"message": "ok"}


class EditTool(ToolPlugin):
    name = "edit_file"
    description = "Edit a file"

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(concurrency_safe=True, side_effects="writes_filesystem")

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
            },
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        return {"message": f"Edited {params.get('file_path')}"}


class ReadTool(ToolPlugin):
    name = "read_file"
    description = "Read a file"

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(concurrency_safe=True, side_effects="readonly")

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "offset": {"type": "integer"},
                "limit": {"type": "integer"},
            },
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        return {"message": f"Read {params.get('file_path')}"}


class FailingTool(ToolPlugin):
    name = "failing_tool"
    description = "Always fails"

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(concurrency_safe=True, side_effects="readonly")

    def schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        raise RuntimeError("intentional failure")


def _make_run_context(
    llm_client: FakeLLMClient,
    tools: dict[str, ToolPlugin] | None = None,
) -> RunContext[Any]:
    return RunContext(
        agent_id="test-agent",
        session_id="test-session",
        run_id="run-1",
        input_text="Do something",
        llm_client=llm_client,
        tools=tools or {},
        event_bus=FakeEventBus(),
        state={},
        scratch={},
        transcript=[],
        system_prompt_fragments=[],
        usage=RunUsage(),
        tool_results=[],
    )


async def _setup_pattern(pattern: CoreCoderPattern, ctx: RunContext[Any]) -> None:
    await pattern.setup(
        agent_id=ctx.agent_id,
        session_id=ctx.session_id,
        input_text=ctx.input_text,
        state=ctx.state,
        tools=ctx.tools,
        llm_client=ctx.llm_client,
        llm_options=ctx.llm_options,
        event_bus=ctx.event_bus,
        transcript=ctx.transcript,
        usage=ctx.usage,
        scratch=ctx.scratch,
        tool_results=ctx.tool_results,
        system_prompt_fragments=ctx.system_prompt_fragments,
    )


@pytest.mark.asyncio
async def test_planning_phase_generates_plan():
    plan_json = json.dumps(
        {
            "files_to_read": ["src/foo.py"],
            "files_to_edit": ["src/foo.py"],
            "tests_to_run": ["pytest"],
            "steps": ["Read foo", "Edit foo", "Run tests"],
        }
    )
    llm = FakeLLMClient(
        [
            # planning response
            FakeResponse(output_text=plan_json),
            # execution: text-only final
            FakeResponse(output_text="Done"),
        ]
    )
    pattern = CoreCoderPattern(config={"enable_planning": True})
    ctx = _make_run_context(llm)
    await _setup_pattern(pattern, ctx)

    result = await pattern.execute()

    assert result.output == "Done"
    plan = ctx.state.get("__plan__")
    assert plan is not None
    assert plan["files_to_read"] == ["src/foo.py"]
    assert plan["steps"] == ["Read foo", "Edit foo", "Run tests"]


@pytest.mark.asyncio
async def test_planning_disabled_respects_flag():
    llm = FakeLLMClient([FakeResponse(output_text="Done")])
    pattern = CoreCoderPattern(config={"enable_planning": False})
    ctx = _make_run_context(llm)
    await _setup_pattern(pattern, ctx)

    await pattern.execute()

    assert "__plan__" not in ctx.state


class BashTool(ToolPlugin):
    name = "bash"
    description = "Run a shell command"

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(concurrency_safe=False, side_effects="external")

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"command": {"type": "string"}},
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        command = str(params.get("command", ""))
        return {
            "command": command,
            "exit_code": 0,
            "stdout": f"Ran: {command}",
            "stderr": "",
            "message": f"Ran: {command}",
        }


class FailingBashTool(ToolPlugin):
    name = "bash"
    description = "Run a shell command that fails"

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(concurrency_safe=False, side_effects="external")

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"command": {"type": "string"}},
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        command = str(params.get("command", ""))
        return {
            "command": command,
            "exit_code": 1,
            "stdout": "",
            "stderr": "FAILED tests/test_a.py::test_x - AssertionError",
            "message": "FAILED tests/test_a.py::test_x - AssertionError",
        }


class FailingEditTool(ToolPlugin):
    name = "edit_file"
    description = "Edit a file that always fails"

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(concurrency_safe=False, side_effects="writes_filesystem")

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
            },
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        from openagents.errors.exceptions import ModelRetryError

        raise ModelRetryError("old_string not found")


class AskHumanTool(ToolPlugin):
    name = "ask_human"
    description = "Ask a human"

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(concurrency_safe=True, side_effects="writes_state")

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"question": {"type": "string"}},
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        return {"message": f"Question recorded: {params.get('question', '')}"}


@pytest.mark.asyncio
async def test_bash_permission_required_blocks_final_text():
    """Destructive bash commands require ask_human approval before the agent
    can finish."""
    from openagents_orchestration.tools.corecoder.bash_tool import BashTool

    llm = FakeLLMClient(
        [
            FakeResponse(output_text='{"steps": ["clean"]}'),
            FakeResponse(tool_calls=[FakeToolCall("bash", {"command": "rm -rf build/"})]),
            FakeResponse(tool_calls=[FakeToolCall("ask_human", {"question": "Approve rm -rf build/?"})]),
            FakeResponse(output_text="Done"),
        ]
    )
    pattern = CoreCoderPattern()
    ctx = _make_run_context(llm, tools={"bash": BashTool(), "ask_human": AskHumanTool()})
    await _setup_pattern(pattern, ctx)

    result = await pattern.execute()

    assert result.output == "Done"
    assert "__bash_permission_required__" not in ctx.state
    user_messages = [m for m in ctx.transcript if m.get("role") == "user"]
    permission_msgs = [
        m
        for m in user_messages
        if "requires permission" in str(m.get("content", "")).lower()
    ]
    assert len(permission_msgs) >= 1


@pytest.mark.asyncio
async def test_edit_failure_suggests_fallback():
    """When edit_file fails the loop should suggest apply_patch/semantic_edit."""
    llm = FakeLLMClient(
        [
            FakeResponse(output_text='{"files_to_edit": ["src/a.py"], "steps": ["edit"]}'),
            FakeResponse(
                tool_calls=[
                    FakeToolCall(
                        "edit_file",
                        {"file_path": "src/a.py", "old_string": "x", "new_string": "y"},
                    )
                ]
            ),
            FakeResponse(output_text="I see the failure"),
        ]
    )
    pattern = CoreCoderPattern()
    ctx = _make_run_context(llm, tools={"edit_file": FailingEditTool()})
    await _setup_pattern(pattern, ctx)

    result = await pattern.execute()

    assert "I see the failure" in result.output
    user_messages = [m for m in ctx.transcript if m.get("role") == "user"]
    recoveries = [
        m
        for m in user_messages
        if "apply_patch" in str(m.get("content", "")) and "semantic_edit" in str(m.get("content", ""))
    ]
    assert len(recoveries) >= 1
    chain = ctx.state.get("__edit_failure_chain__", {})
    assert "src/a.py" in chain
    assert "edit_file" in chain["src/a.py"]


@pytest.mark.asyncio
async def test_verification_failure_parsed_and_retries_capped():
    """Failed verification commands are parsed; after 3 attempts the loop
    gives up and allows a text final answer."""
    llm = FakeLLMClient(
        [
            FakeResponse(output_text='{"files_to_edit": ["src/a.py"], "steps": ["edit"]}'),
            FakeResponse(
                tool_calls=[
                    FakeToolCall(
                        "edit_file",
                        {"file_path": "src/a.py", "old_string": "x", "new_string": "y"},
                    )
                ]
            ),
            # Three failed verification attempts.
            FakeResponse(tool_calls=[FakeToolCall("bash", {"command": "uv run pytest"})]),
            FakeResponse(tool_calls=[FakeToolCall("bash", {"command": "uv run pytest"})]),
            FakeResponse(tool_calls=[FakeToolCall("bash", {"command": "uv run pytest"})]),
            FakeResponse(output_text="Gave up"),
        ]
    )
    pattern = CoreCoderPattern()
    ctx = _make_run_context(llm, tools={"edit_file": EditTool(), "bash": FailingBashTool()})
    await _setup_pattern(pattern, ctx)

    result = await pattern.execute()

    assert "Gave up" in result.output
    # After 3 failed attempts pending verification is dropped.
    assert "__pending_verification__" not in ctx.state
    errors = ctx.state.get("__verification_errors__", [])
    assert any("test_a.py" in str(e.get("file", "")) for e in errors)


@pytest.mark.asyncio
async def test_verification_enforced_after_edit():
    """After editing a file the model must run the verification bash command
    before a text-only final answer is accepted."""
    llm = FakeLLMClient(
        [
            # planning
            FakeResponse(output_text='{"files_to_edit": ["src/a.py"], "steps": ["edit"]}'),
            # execution: edit_file
            FakeResponse(
                tool_calls=[
                    FakeToolCall(
                        "edit_file",
                        {"file_path": "src/a.py", "old_string": "x", "new_string": "y"},
                    )
                ]
            ),
            # execution: bash verification (forced before final text)
            FakeResponse(tool_calls=[FakeToolCall("bash", {"command": "uv run pytest"})]),
            # final text now allowed
            FakeResponse(output_text="Done"),
        ]
    )
    pattern = CoreCoderPattern()
    ctx = _make_run_context(llm, tools={"edit_file": EditTool(), "bash": BashTool()})
    await _setup_pattern(pattern, ctx)

    result = await pattern.execute()

    assert result.output == "Done"
    # pending verification should be cleared by the successful bash call
    assert "__pending_verification__" not in ctx.state
    user_messages = [m for m in ctx.transcript if m.get("role") == "user"]
    enforcements = [
        m
        for m in user_messages
        if "unverified edits" in str(m.get("content", "")).lower()
    ]
    assert len(enforcements) >= 1


@pytest.mark.asyncio
async def test_accept_edits_mode_skips_verification():
    """permission_mode=acceptEdits lets the model finish after edits without
    running tests."""
    llm = FakeLLMClient(
        [
            FakeResponse(output_text='{"files_to_edit": ["src/a.py"], "steps": ["edit"]}'),
            FakeResponse(
                tool_calls=[
                    FakeToolCall(
                        "edit_file",
                        {"file_path": "src/a.py", "old_string": "x", "new_string": "y"},
                    )
                ]
            ),
            FakeResponse(output_text="Done"),
        ]
    )
    pattern = CoreCoderPattern(config={"permission_mode": "acceptEdits"})
    ctx = _make_run_context(llm, tools={"edit_file": EditTool()})
    await _setup_pattern(pattern, ctx)

    result = await pattern.execute()

    assert result.output == "Done"
    assert "__pending_verification__" not in ctx.state


@pytest.mark.asyncio
async def test_auto_mode_allows_destructive_bash():
    """permission_mode=auto does not block destructive bash commands."""
    from openagents_orchestration.tools.corecoder.bash_tool import BashTool

    llm = FakeLLMClient(
        [
            FakeResponse(output_text='{"steps": ["clean"]}'),
            FakeResponse(tool_calls=[FakeToolCall("bash", {"command": "rm -rf build/"})]),
            FakeResponse(output_text="Done"),
        ]
    )
    pattern = CoreCoderPattern(config={"permission_mode": "auto"})
    ctx = _make_run_context(llm, tools={"bash": BashTool()})
    await _setup_pattern(pattern, ctx)

    result = await pattern.execute()

    assert result.output == "Done"
    assert "__bash_permission_required__" not in ctx.state


@pytest.mark.asyncio
async def test_exploration_cache_populated():
    llm = FakeLLMClient(
        [
            FakeResponse(output_text='{"files_to_read": ["src/a.py"], "steps": ["read"]}'),
            FakeResponse(
                tool_calls=[FakeToolCall("read_file", {"file_path": "src/a.py", "limit": 100})]
            ),
            FakeResponse(output_text="Done"),
        ]
    )
    pattern = CoreCoderPattern()
    ctx = _make_run_context(llm, tools={"read_file": ReadTool()})
    await _setup_pattern(pattern, ctx)

    await pattern.execute()

    file_cache = ctx.scratch.get("_file_cache", {})
    assert "src/a.py" in file_cache
    assert file_cache["src/a.py"]["lines"] == 100


@pytest.mark.asyncio
async def test_diagnosis_after_consecutive_failures():
    llm = FakeLLMClient(
        [
            FakeResponse(output_text='{"steps": ["fail twice"]}'),
            # Two consecutive failing tool calls.
            FakeResponse(tool_calls=[FakeToolCall("failing_tool", {})]),
            FakeResponse(tool_calls=[FakeToolCall("failing_tool", {})]),
            # Recovery replanning consumes this planning response.
            FakeResponse(output_text='{"steps": ["diagnose"]}'),
            # Final text after diagnosis.
            FakeResponse(output_text="I see the failures"),
        ]
    )
    pattern = CoreCoderPattern()
    ctx = _make_run_context(llm, tools={"failing_tool": FailingTool()})
    await _setup_pattern(pattern, ctx)

    await pattern.execute()

    assert ctx.state.get("__consecutive_tool_failures__", 0) >= 2
    user_messages = [m for m in ctx.transcript if m.get("role") == "user"]
    diagnoses = [
        m
        for m in user_messages
        if "consecutive tool failures" in str(m.get("content", ""))
    ]
    assert len(diagnoses) >= 1
    recent = ctx.state.get("__recent_errors__", [])
    assert any("failing_tool" in e for e in recent)


class BigOutputTool(ToolPlugin):
    name = "big_output"
    description = "Returns a large output"

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(concurrency_safe=True, side_effects="readonly")

    def schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        return {"message": "line\n" * 3_000}


@pytest.mark.asyncio
async def test_large_tool_output_spilled_to_file(tmp_path: Any, monkeypatch: Any):
    """Very long tool results are spilled to disk instead of filling context."""
    monkeypatch.chdir(tmp_path)
    llm = FakeLLMClient(
        [
            FakeResponse(output_text='{"steps": ["big"]}'),
            FakeResponse(tool_calls=[FakeToolCall("big_output", {})]),
            FakeResponse(output_text="Done"),
        ]
    )
    pattern = CoreCoderPattern()
    ctx = _make_run_context(llm, tools={"big_output": BigOutputTool()})
    await _setup_pattern(pattern, ctx)

    await pattern.execute()

    spilled = ctx.scratch.get("_spilled_outputs", [])
    assert len(spilled) == 1
    assert Path(spilled[0]).exists()
    all_content = "\n".join(
        str(m.get("content", "")) for m in ctx.transcript if "content" in m
    )
    assert "full output written to" in all_content


@pytest.mark.asyncio
async def test_replan_after_consecutive_failures():
    """After 2+ consecutive failures the pattern regenerates the plan."""
    llm = FakeLLMClient(
        [
            FakeResponse(output_text='{"steps": ["fail twice"]}'),
            FakeResponse(tool_calls=[FakeToolCall("failing_tool", {})]),
            FakeResponse(tool_calls=[FakeToolCall("failing_tool", {})]),
            # Recovery replanning call consumes this planning response.
            FakeResponse(output_text='{"steps": ["read logs", "fix"]}'),
            # Final text after replan.
            FakeResponse(output_text="I have a new plan"),
        ]
    )
    pattern = CoreCoderPattern()
    ctx = _make_run_context(llm, tools={"failing_tool": FailingTool()})
    await _setup_pattern(pattern, ctx)

    result = await pattern.execute()

    assert "new plan" in result.output
    assert ctx.state.get("__replan_after_failures__") is True
    plan = ctx.state.get("__plan__")
    assert plan is not None
    assert plan.get("steps") == ["read logs", "fix"]


@pytest.mark.asyncio
async def test_no_planning_for_trivial_transcript():
    # When enable_planning is True but state already has a plan, do not regenerate.
    llm = FakeLLMClient([FakeResponse(output_text="Done")])
    pattern = CoreCoderPattern(config={"enable_planning": True})
    ctx = _make_run_context(llm)
    ctx.state["__plan__"] = {"steps": ["existing"]}
    await _setup_pattern(pattern, ctx)

    await pattern.execute()

    assert ctx.state["__plan__"]["steps"] == ["existing"]


@pytest.mark.asyncio
async def test_thought_tool_tracks_reasoning():
    from openagents_orchestration.tools.corecoder.think import ThinkTool

    llm = FakeLLMClient(
        [
            FakeResponse(output_text='{"steps": ["think"]}'),
            FakeResponse(
                tool_calls=[FakeToolCall("think", {"thought": "I should read the config first."})]
            ),
            FakeResponse(output_text="Done"),
        ]
    )
    pattern = CoreCoderPattern()
    ctx = _make_run_context(llm, tools={"think": ThinkTool()})
    await _setup_pattern(pattern, ctx)

    await pattern.execute()

    thoughts = ctx.scratch.get("_recent_thoughts", [])
    assert any("config" in t for t in thoughts)


@pytest.mark.asyncio
async def test_list_directory_updates_cache():
    from openagents_orchestration.tools.corecoder.list_directory import (
        ListDirectoryTool,
    )

    llm = FakeLLMClient(
        [
            FakeResponse(output_text='{"steps": ["list"]}'),
            FakeResponse(
                tool_calls=[FakeToolCall("list_directory", {"path": "src", "depth": 1})]
            ),
            FakeResponse(output_text="Done"),
        ]
    )
    pattern = CoreCoderPattern()
    ctx = _make_run_context(llm, tools={"list_directory": ListDirectoryTool()})
    await _setup_pattern(pattern, ctx)

    await pattern.execute()

    cache = ctx.scratch.get("_list_dir_cache", {})
    assert "src" in cache


@pytest.mark.asyncio
async def test_recent_thoughts_surface_in_dynamic_prompt():
    from openagents_orchestration.tools.corecoder.think import ThinkTool

    llm = FakeLLMClient(
        [
            FakeResponse(output_text='{"steps": ["think"]}'),
            FakeResponse(
                tool_calls=[FakeToolCall("think", {"thought": "This is my reasoning."})]
            ),
            FakeResponse(output_text="Done"),
        ]
    )
    pattern = CoreCoderPattern()
    ctx = _make_run_context(llm, tools={"think": ThinkTool()})
    ctx.scratch["_recent_thoughts"] = ["Earlier thought.", "This is my reasoning."]
    await _setup_pattern(pattern, ctx)

    prompt = pattern.compose_system_prompt("Base")
    assert "Recent reasoning" in prompt
    assert "This is my reasoning." in prompt


class SlowTool(ToolPlugin):
    name = "slow_tool"
    description = "Sleeps a bit"

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(concurrency_safe=True, side_effects="readonly")

    def schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"delay": {"type": "number"}}}

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        delay = float(params.get("delay", 0.1))
        await asyncio.sleep(delay)
        return {"message": f"slept {delay}"}


@pytest.mark.asyncio
async def test_concurrent_tool_calls_run_in_parallel():
    llm = FakeLLMClient(
        [
            FakeResponse(output_text='{"steps": ["parallel"]}'),
            FakeResponse(
                tool_calls=[
                    FakeToolCall("slow_tool", {"delay": 0.2}),
                    FakeToolCall("slow_tool", {"delay": 0.2}),
                ]
            ),
            FakeResponse(output_text="Done"),
        ]
    )
    pattern = CoreCoderPattern()
    ctx = _make_run_context(llm, tools={"slow_tool": SlowTool()})
    await _setup_pattern(pattern, ctx)

    started = asyncio.get_event_loop().time()
    await pattern.execute()
    elapsed = asyncio.get_event_loop().time() - started

    # If serial, elapsed would be ~0.4s; parallel should be <0.35s.
    assert elapsed < 0.35
    assert ctx.usage.tool_calls == 2


@pytest.mark.asyncio
async def test_mixed_serial_and_parallel_tool_calls():
    """A non-concurrency-safe call forces serial ordering around it."""

    class UnsafeSlowTool(ToolPlugin):
        name = "unsafe_slow"
        description = "Sleeps and is not concurrency safe"

        def execution_spec(self) -> ToolExecutionSpec:
            return ToolExecutionSpec(concurrency_safe=False, side_effects="readonly")

        def schema(self) -> dict[str, Any]:
            return {"type": "object", "properties": {"delay": {"type": "number"}}}

        async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
            delay = float(params.get("delay", 0.1))
            await asyncio.sleep(delay)
            return {"message": f"unsafe {delay}"}

    llm = FakeLLMClient(
        [
            FakeResponse(output_text='{"steps": ["mixed"]}'),
            FakeResponse(
                tool_calls=[
                    FakeToolCall("slow_tool", {"delay": 0.1}),
                    FakeToolCall("unsafe_slow", {"delay": 0.1}),
                    FakeToolCall("slow_tool", {"delay": 0.1}),
                ]
            ),
            FakeResponse(output_text="Done"),
        ]
    )
    pattern = CoreCoderPattern()
    ctx = _make_run_context(
        llm, tools={"slow_tool": SlowTool(), "unsafe_slow": UnsafeSlowTool()}
    )
    await _setup_pattern(pattern, ctx)

    started = asyncio.get_event_loop().time()
    await pattern.execute()
    elapsed = asyncio.get_event_loop().time() - started

    # First two could run in parallel (~0.1), then unsafe serial (~0.1), then last parallel (~0.1)
    # Serial would be ~0.3s; we leave generous headroom for CI overhead.
    assert elapsed < 0.5
    assert ctx.usage.tool_calls == 3

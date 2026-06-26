"""CoreCoderPattern — native-tool-calling ReAct loop.

Faithful port of CoreCoder's ``Agent.chat()`` into the openagents SDK's
``PatternPlugin`` shape:

- Builds an Anthropic-compatible tool schema list from the raw ToolPlugins
  registered for this agent.
- Loops up to ``max_steps`` (default 20) calling the LLM with
  ``tools=schemas``; on ``tool_use`` blocks, dispatches each call through the
  bound-tool layer (so executor timeouts/policies still apply), then appends
  the tool_result blocks to the next user message.
- Catches *all* tool exceptions and feeds them back as ``is_error=True``
  tool_result blocks so the LLM can self-correct. ``ModelRetryError``
  carries the most actionable message; we surface it verbatim.
- Composes the system prompt from :data:`CORE_PRINCIPLES` + a dynamic
  fragment (cwd / git status / dirty files / tool roster).

Why bypass ``PatternPlugin.call_tool``: it converts ``ModelRetryError`` to a
``system`` transcript message and re-raises, which works for text-only loops
but loses the per-tool_call_id binding native tool calling needs. We
reproduce its event emissions (``tool.called``, ``tool.succeeded``,
``tool.failed``) so downstream observers see the same feed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from openagents.errors.exceptions import ModelRetryError, ToolError
from openagents.interfaces.capabilities import PATTERN_EXECUTE
from openagents.interfaces.pattern import PatternPlugin, unwrap_tool_result
from pydantic import BaseModel, Field

from openagents_orchestration.hooks import HookEvent
from openagents_orchestration.models.pattern import (
    FailureGrade,
    PatternError,
    PatternOutcome,
    PatternOutcomeStatus,
)
from openagents_orchestration.models.stream import StreamEvent, StreamEventType
from openagents_orchestration.patterns.stream_parser import (
    PreExecutionCache,
    StreamToolCallParser,
)
from openagents_orchestration.tools.corecoder.test_parser import parse_pytest_output
from openagents_orchestration.utils.runtime_compat import (
    error_details_payload,
    is_retryable_llm_error,
)
from openagents_orchestration.utils.structured_generate import structured_generate
from prompts.core import CORE_PRINCIPLES
from prompts.dynamic import (
    _detect_project_type,
    build_runtime_fragment,
    gather_runtime_context,
)

try:
    from openagents.interfaces.diagnostics import LLMCallMetrics
    from openagents.llm.base import LLMChunk
except ImportError:  # pragma: no cover - older SDKs
    from dataclasses import dataclass, field

    @dataclass
    class LLMCallMetrics:  # type: ignore[no-redef]
        model: str = ""
        latency_ms: float = 0.0
        input_tokens: int = 0
        output_tokens: int = 0
        cached_tokens: int = 0
        error: str | None = None
        metadata: dict[str, Any] = field(default_factory=dict)

        def model_dump(self) -> dict[str, Any]:
            return {
                "model": self.model,
                "latency_ms": self.latency_ms,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cached_tokens": self.cached_tokens,
                "error": self.error,
                "metadata": dict(self.metadata),
            }

    @dataclass
    class LLMChunk:  # type: ignore[no-redef]
        type: str = ""
        delta: dict[str, Any] | str | None = None
        content: dict[str, Any] | None = None
        error: str | None = None
        usage: Any = None


_DEFAULT_MAX_STEPS = 20
_DEFAULT_MAX_TOKENS = 4096
_TOOL_RESULT_CHAR_LIMIT = 8_000


@dataclass
class _ToolCallDescriptor:
    """Internal descriptor for one tool call awaiting dispatch."""

    index: int
    tool_id: str
    params: dict[str, Any]
    call_id: str
    concurrency_safe: bool


@dataclass
class _ToolDispatchResult:
    """Internal result of dispatching one tool call."""

    desc: _ToolCallDescriptor
    success: bool
    error: str | None
    data: Any
    executor_meta: dict[str, Any] | None


def _is_tool_concurrency_safe(tools: dict[str, Any] | None, tool_id: str) -> bool:
    """Return True if the underlying plugin is marked concurrency-safe."""
    if not tools or tool_id not in tools:
        return False
    tool = tools[tool_id]
    raw = getattr(tool, "_tool", tool)
    spec = getattr(raw, "execution_spec", None)
    if callable(spec):
        try:
            return bool(spec().concurrency_safe)
        except Exception:
            return False
    return False


class _PlanSchema(BaseModel):
    """Structured plan output with uncertainty assessment."""

    files_to_read: list[str] = Field(default_factory=list)
    files_to_edit: list[str] = Field(default_factory=list)
    tests_to_run: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    confidence: int = Field(
        ge=0,
        le=10,
        default=5,
        description=(
            "Confidence that the task is well-understood and executable as stated. "
            "0 = completely ambiguous, 10 = perfectly clear."
        ),
    )
    clarification_needed: str | None = Field(
        default=None,
        description=(
            "If confidence is low or the task is ambiguous, state the clarifying "
            "question to ask the human. Otherwise leave null."
        ),
    )


class CoreCoderPattern(PatternPlugin):
    """Native tool-calling ReAct loop with CoreCoder semantics."""

    _PRINCIPLES = CORE_PRINCIPLES
    # _PRINCIPLES 是「底座」（与声明式角色 prompt 叠加）还是「完整层」（被角色
    # prompt 覆盖时不重复追加）。CoreCoder 的 CORE 是底座；Director/TeamLeader 的
    # PRINCIPLES 是完整指引，override 为 False，避免与 prompts 声明双重注入。
    _PRINCIPLES_IS_BASE = True

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})
        self.capabilities = {PATTERN_EXECUTE}
        self._max_steps = int(self.config.get("max_steps", _DEFAULT_MAX_STEPS))
        self._max_tokens = int(self.config.get("max_tokens", _DEFAULT_MAX_TOKENS))
        self._temperature = self.config.get("temperature")
        self._model_override = self.config.get("model")
        self._enable_planning = bool(self.config.get("enable_planning", True))
        self._permission_mode = str(self.config.get("permission_mode", "default"))
        self._plan_mode = bool(self.config.get("plan_mode", False))
        self._max_consecutive_readonly_steps = int(
            self.config.get("max_consecutive_readonly_steps", 5)
        )
        self._step_budget_warning_steps = int(
            self.config.get("step_budget_warning_steps", 5)
        )
        self._enable_clarification = bool(
            self.config.get("enable_clarification", True)
        )
        self._clarification_confidence_threshold = int(
            self.config.get("clarification_confidence_threshold", 4)
        )
        self._enable_tool_gating = bool(
            self.config.get("enable_tool_gating", True)
        )
        self._tool_gating_threshold = int(
            self.config.get("tool_gating_threshold", 3)
        )
        self._original_state: dict[str, Any] | None = None
        self._original_transcript: list[dict[str, Any]] | None = None
        self._original_scratch: dict[str, Any] | None = None
        self._original_tool_results: list[dict[str, Any]] | None = None
        self._original_system_prompt_fragments: list[str] | None = None
        self._original_memory_view: dict[str, Any] | None = None
        # 声明式角色 prompt 引用（agents/<role>.json 的 prompts → pattern.config）。
        # 由 _resolve_prompts() 解析成角色层 system prompt，叠加在 _PRINCIPLES 之上。
        self._prompt_refs: list[str] = list(self.config.get("prompts", []) or [])
        self._resolved_prompt_cache: str | None = None

    def _resolve_prompts(self) -> str:
        """解析 ``self._prompt_refs`` 引用列表为角色层 system prompt（带缓存）。

        每项形如 ``"module.path:SYMBOL"`` 或 ``"module.path.SYMBOL"``，import 后取
        符号（须为 str），按声明顺序 ``\\n\\n`` 拼接。

        容错（X-07 不吞但降级）：单项解析失败 → 跳过该项 + stderr 警告，不中断；
        全部失败或未声明 → 返回 ""（caller 回退到 ``_PRINCIPLES`` 类属性）。
        """
        if self._resolved_prompt_cache is not None:
            return self._resolved_prompt_cache
        parts: list[str] = []
        for ref in self._prompt_refs:
            try:
                module_name, sep, attr = ref.partition(":")
                if not sep:
                    module_name, _, attr = ref.rpartition(".")
                import importlib

                module = importlib.import_module(module_name)
                value = getattr(module, attr)
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())
                else:
                    import sys

                    print(
                        f"[CoreCoder] prompt ref '{ref}' 不是非空字符串，跳过",
                        file=sys.stderr,
                        flush=True,
                    )
            except Exception as exc:  # ImportError / AttributeError 等
                import sys

                print(
                    f"[CoreCoder] prompt ref '{ref}' 解析失败，跳过: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
        self._resolved_prompt_cache = "\n\n".join(parts)
        return self._resolved_prompt_cache

    def compose_system_prompt(self, base_prompt: str) -> str:
        """Inject role prompt + principles + dynamic runtime fragment.

        base_prompt 为空时，自动取声明式 ``prompts`` 解析结果作为角色层（让
        ``agents/<role>.json`` 的 prompts 生效）。最终 system prompt =
        [角色 prompt] + [_PRINCIPLES] + [动态片段]，由 __DYNAMIC_BOUNDARY__ 分隔
        静态/动态以利 prompt 缓存。

        Static content (identity, rules) is separated from dynamic content
        (runtime context, fragments) by __DYNAMIC_BOUNDARY__ so the caller
        can split them into distinct system messages for prompt caching.
        """
        ctx = self.context
        static_fragments: list[str] = []
        dynamic_fragments: list[str] = []

        base = (base_prompt or "").strip()
        resolved_role = self._resolve_prompts()
        if not base:
            base = resolved_role
        if base:
            static_fragments.append(base)
        # _PRINCIPLES 追加规则（避免 director/team_leader 双重注入）：
        # - CoreCoder: _PRINCIPLES=CORE 是底座，与角色 ROLE 叠加（always 追加）
        # - Director/TeamLeader: _PRINCIPLES 是完整指引，已由声明式 prompts 覆盖，
        #   故 _PRINCIPLES_IS_BASE=False 时，只要角色 prompt 非空就不再追加（兜底）
        if self._PRINCIPLES_IS_BASE or not resolved_role:
            static_fragments.append(self._PRINCIPLES.strip())

        if ctx is not None:
            runtime_kwargs = gather_runtime_context(ctx)
            runtime_fragment = build_runtime_fragment(**runtime_kwargs)
            if runtime_fragment.strip():
                dynamic_fragments.append(runtime_fragment.strip())
            dynamic_fragments.extend(
                fragment.strip()
                for fragment in ctx.system_prompt_fragments
                if isinstance(fragment, str) and fragment.strip()
            )

        static = "\n\n".join(f for f in static_fragments if f)
        dynamic = "\n\n".join(f for f in dynamic_fragments if f)
        if dynamic:
            return f"{static}\n\n__DYNAMIC_BOUNDARY__\n\n{dynamic}"
        return static

    async def setup(self, **kwargs: Any) -> None:
        """Capture the original mutable containers so we can write changes back.

        RunContext copies the containers passed to its constructor, so callers
        that reuse the same dict/list (e.g. tests) would not see updates without
        this write-back.
        """
        self._original_state = kwargs.get("state")
        self._original_transcript = kwargs.get("transcript")
        self._original_scratch = kwargs.pop("scratch", None)
        self._original_tool_results = kwargs.pop("tool_results", None)
        self._original_system_prompt_fragments = kwargs.pop("system_prompt_fragments", None)
        self._original_memory_view = kwargs.pop("memory_view", None)
        await super().setup(**kwargs)
        # Seed the pattern's scratch from the caller's scratch so pre-existing
        # context (recent thoughts, cwd, etc.) is visible to compose_system_prompt.
        if self.context is not None and isinstance(self._original_scratch, dict):
            self.context.scratch.update(self._original_scratch)

    async def execute(self) -> PatternOutcome:
        """Run the ReAct loop until the model emits a text-only turn."""
        ctx = self.context
        if ctx is None:
            return PatternOutcome(
                output="",
                status=PatternOutcomeStatus.FAILED,
                error=PatternError(
                    message="CoreCoderPattern.execute requires setup() first",
                    grade=FailureGrade.AGENT_FATAL,
                ),
            )
        if ctx.llm_client is None:
            return PatternOutcome(
                output="",
                status=PatternOutcomeStatus.FAILED,
                error=PatternError(
                    message="CoreCoderPattern needs an llm_client",
                    grade=FailureGrade.AGENT_FATAL,
                ),
            )

        if self._plan_mode:
            ctx.state["__plan_mode_active__"] = True

        tool_schemas = self._build_tool_schemas()

        messages: list[dict[str, Any]] = []
        # Prepend prior transcript turns (assembled context, prior memories, etc.)
        for entry in ctx.transcript:
            normalized = _normalize_history_message(entry)
            if normalized is not None:
                messages.append(normalized)

        # The current user input.
        if not _last_message_is_user(messages):
            messages.append({"role": "user", "content": ctx.input_text})

        # ---- Lightweight planning phase --------------------------------------
        # Generate a structured plan before any tool calls. The plan is stored in
        # ctx.state so the dynamic prompt fragment can surface it every turn.
        if self._enable_planning and not ctx.state.get("__plan__"):
            plan = await self._generate_plan(messages, tool_schemas)
            if plan:
                ctx.state["__plan__"] = plan
                # If the task is ambiguous, ask the human before doing any work.
                clarification = self._clarification_needed(plan)
                if clarification:
                    ctx.state["__clarification_question__"] = clarification
                    return await self._request_clarification_and_pause(clarification)

        # ---- Plan mode: generate plan file and stop for approval --------------
        if self._plan_mode and not ctx.state.get("__plan_approved__"):
            plan = ctx.state.get("__plan__") or {}
            plan_text = json.dumps(plan, ensure_ascii=False, indent=2)
            plan_path = self._write_plan_file(plan_text)
            ctx.state["__plan_mode_active__"] = True
            self._writeback_context()
            return PatternOutcome(
                output=(
                    f"[Plan mode] Generated plan file: {plan_path}\n\n"
                    f"{plan_text}\n\n"
                    "Review the plan and call `approve_plan` to continue execution."
                ),
                status=PatternOutcomeStatus.AWAITING_HUMAN,
            )

        system_prompt = self.compose_system_prompt("")
        system_messages = _split_system_prompt(system_prompt)
        final_text = ""
        consecutive_empty = 0
        max_consecutive_empty = 2
        for step in range(1, self._max_steps + 1):
            # Cap verification-driven retries and reset per-turn attempt tracker.
            pending = ctx.state.get("__pending_verification__")
            if isinstance(pending, dict) and pending.get("attempts", 0) >= 3:
                ctx.state.pop("__pending_verification__", None)
                # Keep __verification_errors__ in state so callers can inspect them.
            ctx.state["__verification_attempted__"] = False

            # Deterministic guardrail: count consecutive read-only turns so we can
            # nudge the model out of endless exploration (Claude Code-style tight loop).
            last_readonly = ctx.state.get("__last_turn_readonly__", False)
            if last_readonly:
                consecutive_readonly = ctx.state.get("__consecutive_readonly_steps__", 0) + 1
            else:
                consecutive_readonly = 0
            ctx.state["__consecutive_readonly_steps__"] = consecutive_readonly
            readonly_nudge = self._build_readonly_budget_message(
                consecutive_readonly, self._max_steps - step
            )
            if readonly_nudge:
                messages.append({"role": "user", "content": readonly_nudge})
            budget_warning = self._build_step_budget_warning(step, self._max_steps)
            if budget_warning:
                messages.append({"role": "user", "content": budget_warning})

            # Tool gating: after too many consecutive read-only turns, remove
            # exploration tools from the schema so the model must act.
            gated = (
                self._enable_tool_gating
                and consecutive_readonly >= self._tool_gating_threshold
            )
            active_schemas = tool_schemas
            if gated:
                active_schemas = self._build_gated_tool_schemas()
                gate_message = self._build_tool_gate_message(
                    consecutive_readonly, self._max_steps - step
                )
                if gate_message:
                    messages.append({"role": "user", "content": gate_message})

            # Hook: pattern.before_step
            hooks = self._get_hooks()
            if hooks is not None:
                step_payload = hooks.run(
                    HookEvent.PATTERN_BEFORE_STEP,
                    {"step": step, "max_steps": self._max_steps, "state": dict(ctx.state)},
                )
                if isinstance(step_payload, dict) and step_payload.get("blocked"):
                    final_text = step_payload.get(
                        "reason", "Step blocked by pattern.before_step hook."
                    )
                    ctx.state["__steps_used__"] = step
                    break

            response = await self._invoke_llm(
                messages=[*system_messages, *messages],
                tools=active_schemas,
            )

            assistant_content = response.content or _wrap_text(response.output_text)
            tool_calls = response.tool_calls or []
            text_part = response.output_text or ""

            # Track whether this turn actually mutated state/files. The next turn
            # uses it to detect runaway read-only exploration.
            ctx.state["__last_turn_readonly__"] = (
                not tool_calls
                or all(self._is_readonly_tool_call(c) for c in tool_calls)
            )

            if not tool_calls:
                stripped = text_part.strip()
                if stripped:
                    # Verification enforcement: do not allow a text-only final
                    # answer while there are unverified edits or destructive bash
                    # commands awaiting permission.
                    pending = ctx.state.get("__pending_verification__")
                    if isinstance(pending, dict) and pending.get("files"):
                        messages.append(self._assistant_text_message(stripped, assistant_content))
                        enforcement = self._verification_enforcement_message()
                        if enforcement:
                            messages.append({"role": "user", "content": enforcement})
                        continue
                    permission_msg = self._build_permission_required_message()
                    if permission_msg:
                        messages.append(self._assistant_text_message(stripped, assistant_content))
                        messages.append({"role": "user", "content": permission_msg})
                        continue

                    # Subclasses can veto text-only responses (e.g. Director
                    # must always end with a tool call or finalize).
                    if await self._should_accept_text_response(stripped):
                        final_text = stripped
                        messages.append(self._assistant_text_message(stripped, assistant_content))
                        ctx.state["__steps_used__"] = step
                        ctx.state["__tool_calls_used__"] = (
                            sum(1 for m in messages if m.get("role") == "assistant" and "tool_calls" in m)
                        )
                        await self.emit(
                            "pattern.completed",
                            steps=step,
                            final_chars=len(final_text),
                        )
                        break

                    # Text was rejected — nudge the model back into the loop.
                    consecutive_text_rejected = ctx.state.get("__consecutive_text_rejected__", 0) + 1
                    ctx.state["__consecutive_text_rejected__"] = consecutive_text_rejected
                    await self.emit(
                        "pattern.text_rejected",
                        step=step,
                        consecutive=consecutive_text_rejected,
                    )
                    if consecutive_text_rejected >= 2:
                        # After two rejections, accept it to avoid infinite loops.
                        final_text = (
                            f"[CoreCoder] text response rejected twice; accepting to avoid loop.\n\n"
                            f"{stripped}"
                        )
                        ctx.state["__steps_used__"] = step
                        ctx.state["__tool_calls_used__"] = (
                            sum(1 for m in messages if m.get("role") == "assistant" and "tool_calls" in m)
                        )
                        break

                    messages.append(self._assistant_text_message(stripped, assistant_content))
                    messages.append({
                        "role": "user",
                        "content": (
                            "Your text response was not accepted. "
                            "You must call a tool on every turn. "
                            "If all tasks are complete, call finalize. "
                            "If a task failed, call replan, retry, ask_human, or spawn_resident. "
                            "Do not output tool-less text."
                        ),
                    })
                    continue
                # Empty response — model didn't emit a tool_call AND didn't say
                # anything. Nudge it back into the loop instead of returning ""
                # as the "final answer". After N consecutive empties, give up.
                consecutive_empty += 1
                ctx.state["__consecutive_empty_responses__"] = consecutive_empty
                await self.emit(
                    "pattern.empty_response",
                    step=step,
                    consecutive=consecutive_empty,
                )
                if consecutive_empty >= max_consecutive_empty:
                    final_text = (
                        f"[CoreCoder] model returned {consecutive_empty} consecutive empty responses; "
                        "stopping without a final answer."
                    )
                    ctx.state["__steps_used__"] = step
                    ctx.state["__tool_calls_used__"] = (
                        sum(1 for m in messages if m.get("role") == "assistant" and "tool_calls" in m)
                    )
                    await self.emit(
                        "pattern.empty_response_budget_exhausted",
                        steps=step,
                    )
                    break
                # Push a corrective user message and continue the loop.
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your previous response was empty. If you have completed all required "
                            "deliverables, respond with a brief text summary. Otherwise, continue "
                            "by calling exactly one of the available tools."
                        ),
                    }
                )
                continue

            # Reset the empty-streak when we get any productive response.
            consecutive_empty = 0
            if self._uses_openai_conversation():
                messages.append(self._assistant_tool_call_message(tool_calls, text_part))
                tool_result_messages = await self._dispatch_tool_calls(
                    tool_calls,
                    step=step,
                    as_openai_messages=True,
                )
                messages.extend(tool_result_messages)
            else:
                messages.append({"role": "assistant", "content": assistant_content})
                tool_result_blocks = await self._dispatch_tool_calls(tool_calls, step=step)
                messages.append({"role": "user", "content": tool_result_blocks})

            # Update exploration cache and optional edit-recovery / verification nudge.
            self._update_exploration_cache(tool_calls, step)
            edit_recovery = self._build_edit_recovery_message()
            if edit_recovery:
                messages.append({"role": "user", "content": edit_recovery})
            permission_msg = self._build_permission_required_message()
            if permission_msg:
                messages.append({"role": "user", "content": permission_msg})
            pending = ctx.state.get("__pending_verification__")
            if isinstance(pending, dict) and pending.get("files"):
                enforcement = self._verification_enforcement_message()
                if enforcement:
                    messages.append({"role": "user", "content": enforcement})
            else:
                verification_nudge = self._build_verification_nudge()
                if verification_nudge:
                    messages.append({"role": "user", "content": verification_nudge})

            # Structured error recovery after repeated failures.
            failure_count = ctx.state.get("__consecutive_tool_failures__", 0)
            if failure_count >= 2:
                diagnosis = self._build_diagnosis_message(failure_count)
                if diagnosis:
                    messages.append({"role": "user", "content": diagnosis})
                    await self.emit(
                        "pattern.error_recovery_triggered",
                        step=step,
                        consecutive_failures=failure_count,
                    )
                # After repeated failures, regenerate the plan to get out of the rut.
                if not ctx.state.get("__replan_after_failures__"):
                    new_plan = await self._generate_plan(messages, tool_schemas)
                    if new_plan:
                        ctx.state["__plan__"] = new_plan
                    ctx.state["__replan_after_failures__"] = True
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Repeated tool failures. I have regenerated the plan. "
                                "Review the updated plan and proceed with the next step."
                            ),
                        }
                    )

            # Allow subclasses to decide whether to keep looping.
            if not await self._should_continue_step(step):
                complete_summary = ctx.state.get("__complete_task_summary__")
                final_text = (
                    complete_summary
                    or "[CoreCoder] loop terminated by pattern condition."
                )
                ctx.state["__steps_used__"] = step
                ctx.state["__tool_calls_used__"] = (
                    sum(1 for m in messages if m.get("role") == "assistant" and "tool_calls" in m)
                )
                await self.emit(
                    "pattern.completed" if complete_summary else "pattern.terminated",
                    steps=step,
                    final_chars=len(final_text),
                )
                break
        else:  # for/else: ran out of steps
            await self.emit(
                "pattern.step_budget_exhausted",
                max_steps=self._max_steps,
            )
            ctx.state["__step_budget_exhausted__"] = True
            ctx.state["__steps_used__"] = self._max_steps
            ctx.state["__tool_calls_used__"] = (
                sum(1 for m in messages if m.get("role") == "assistant" and "tool_calls" in m)
            )
            final_text = (
                final_text
                or "[CoreCoder] step budget exhausted before producing a final answer."
            )

        # Persist the loop transcript so memory writeback / context assembler can see it.
        ctx.transcript[:] = [
            dict(entry)
            for entry in messages
            if entry.get("role") in ("user", "assistant", "tool")
        ]
        self._writeback_context()

        # Determine terminal outcome.
        awaiting_human = ctx.state.get("__awaiting_human_reply__")
        step_budget_exhausted = ctx.state.get("__step_budget_exhausted__")

        if awaiting_human:
            outcome = PatternOutcome(
                output=str(final_text or "").strip(),
                status=PatternOutcomeStatus.AWAITING_HUMAN,
                metadata={"awaiting_human_reply": awaiting_human},
            )
        elif step_budget_exhausted:
            outcome = PatternOutcome(
                output=str(final_text or "").strip(),
                status=PatternOutcomeStatus.MAX_STEPS,
            )
        else:
            outcome = PatternOutcome(
                output=str(final_text or "").strip(),
                status=PatternOutcomeStatus.COMPLETED,
            )

        # NOTE: pattern 层不再发 PATTERN_AFTER_EXECUTE —— 状态同步统一由
        # runner.run_agent 末尾那次触发负责（payload 带 agent_type/task_id，信息齐全）。
        # pattern 层拿不到这两个字段，旧触发点对 StateSyncHooks 永远空转（PIT-002）。
        return outcome

    def _writeback_context(self) -> None:
        """Mirror mutated RunContext containers back to the caller's originals."""
        ctx = self.context
        if ctx is None:
            return
        if isinstance(self._original_state, dict):
            self._original_state.clear()
            self._original_state.update(ctx.state)
        if isinstance(self._original_scratch, dict):
            self._original_scratch.clear()
            self._original_scratch.update(ctx.scratch)
        if isinstance(self._original_transcript, list):
            self._original_transcript[:] = list(ctx.transcript)
        if isinstance(self._original_tool_results, list):
            self._original_tool_results[:] = list(ctx.tool_results)
        if isinstance(self._original_system_prompt_fragments, list):
            self._original_system_prompt_fragments[:] = list(ctx.system_prompt_fragments)
        if isinstance(self._original_memory_view, dict):
            self._original_memory_view.clear()
            self._original_memory_view.update(ctx.memory_view)

    def _build_tool_schemas(self) -> list[dict[str, Any]]:
        """Render tool definitions in the shape expected by the active provider."""
        ctx = self.context
        provider = getattr(getattr(ctx, "llm_client", None), "provider_name", "")
        use_openai_shape = provider.startswith("openai_compatible") or provider.startswith(
            "litellm"
        )
        schemas: list[dict[str, Any]] = []
        # Sort by tool_id to ensure deterministic ordering → stable cache fingerprints
        for tool_id, bound in sorted((ctx.tools or {}).items(), key=lambda item: item[0]):
            raw = getattr(bound, "_tool", bound)
            description = getattr(raw, "description", "") or ""
            schema_fn = getattr(raw, "schema", None)
            if callable(schema_fn):
                input_schema = schema_fn() or {"type": "object", "properties": {}}
            else:
                input_schema = {"type": "object", "properties": {}}
            if use_openai_shape:
                schemas.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool_id,
                            "description": description,
                            "parameters": input_schema,
                        },
                    }
                )
            else:
                schemas.append(
                    {
                        "name": tool_id,
                        "description": description,
                        "input_schema": input_schema,
                    }
                )
        return schemas

    def _is_exploration_tool_id(self, tool_id: str) -> bool:
        """Return True if the tool is read/search/thinking-only.

        These tools do not mutate state or files and can fuel endless
        exploration if the model keeps calling them.
        """
        ctx = self.context
        if ctx is None or not ctx.tools:
            return False
        bound = ctx.tools.get(tool_id)
        if bound is None:
            return False
        raw = getattr(bound, "_tool", bound)
        spec = getattr(raw, "execution_spec", None)
        if not callable(spec):
            return False
        try:
            s = spec()
        except Exception:
            return False
        side_effects = getattr(s, "side_effects", "")
        writes_files = getattr(s, "writes_files", False)
        # Read/search/web/think tools are exploration-only.
        return side_effects in ("readonly", "none") and not writes_files

    def _build_gated_tool_schemas(self) -> list[dict[str, Any]]:
        """Return tool schemas with exploration tools removed.

        Used when the model has exceeded its exploration budget. The only
        available actions are those that mutate state, edit files, run commands,
        ask for clarification, or finish.
        """
        all_schemas = self._build_tool_schemas()
        gated: list[dict[str, Any]] = []
        for schema in all_schemas:
            tool_id = schema.get("name") or schema.get("function", {}).get("name", "")
            if not tool_id:
                continue
            if self._is_exploration_tool_id(tool_id):
                continue
            gated.append(schema)
        return gated

    def _build_tool_gate_message(
        self, consecutive_readonly: int, remaining_steps: int
    ) -> str | None:
        """Hard gate message injected when exploration tools are removed."""
        if not self._enable_tool_gating:
            return None
        if consecutive_readonly < self._tool_gating_threshold:
            return None
        return (
            f"EXPLORATION BUDGET EXHAUSTED: you have used {consecutive_readonly} "
            f"consecutive read/search turns. Only {remaining_steps} steps remain. "
            "Read/search tools are now DISABLED. You MUST choose one of these actions:\n"
            "1. Edit or write a file to fix/implement something.\n"
            "2. Run a verification command with bash.\n"
            "3. Call ask_human if you are still unclear.\n"
            "4. Call complete_task/finalize if the task is finished.\n"
            "Do not call any read/search/list tool again."
        )

    async def _dispatch_tool_calls(
        self,
        tool_calls: list[Any],
        *,
        step: int,
        as_openai_messages: bool = False,
    ) -> list[dict[str, Any]]:
        """Dispatch tool calls, running independent ones concurrently.

        Tool calls whose underlying plugin is marked ``concurrency_safe`` are
        executed with ``asyncio.gather``; ordering-dependent calls (filesystem
        writes, external commands without concurrency guarantees) run serially.
        Side effects on ``ctx`` are applied in the original call order so the
        LLM sees a deterministic transcript.
        """
        ctx = self.context
        if not tool_calls:
            return []

        # Build dispatch descriptors first so we can decide parallelism.
        descriptors: list[_ToolCallDescriptor] = []
        for call in tool_calls:
            tool_id = call.name
            params = call.arguments if isinstance(call.arguments, dict) else {}
            call_id = call.id or f"call_{uuid4().hex[:12]}"
            descriptors.append(
                _ToolCallDescriptor(
                    index=len(descriptors),
                    tool_id=tool_id,
                    params=params,
                    call_id=call_id,
                    concurrency_safe=_is_tool_concurrency_safe(ctx.tools, tool_id),
                )
            )

        # Run consecutive blocks: each non-concurrency-safe call forces a
        # serial point; preceding concurrency-safe calls are gathered together.
        results: list[_ToolDispatchResult | None] = [None] * len(descriptors)
        concurrent_batch: list[_ToolCallDescriptor] = []

        async def _run_batch(batch: list[_ToolCallDescriptor]) -> None:
            if not batch:
                return
            coros = [self._dispatch_single_tool(desc, step) for desc in batch]
            batch_results = await asyncio.gather(*coros, return_exceptions=True)
            for desc, outcome in zip(batch, batch_results, strict=True):
                if isinstance(outcome, Exception):
                    results[desc.index] = _ToolDispatchResult(
                        desc=desc,
                        success=False,
                        error=f"Unexpected dispatch failure: {outcome}",
                        data=None,
                        executor_meta=None,
                    )
                else:
                    results[desc.index] = outcome

        for desc in descriptors:
            if desc.concurrency_safe:
                concurrent_batch.append(desc)
                continue
            await _run_batch(concurrent_batch)
            concurrent_batch.clear()
            results[desc.index] = await self._dispatch_single_tool(desc, step)
        await _run_batch(concurrent_batch)

        # Apply side effects in original order and build result messages.
        result_payloads: list[dict[str, Any]] = []
        for result in results:
            payload = self._apply_tool_dispatch_result(result, step, as_openai_messages)
            result_payloads.append(payload)
        return result_payloads

    async def _dispatch_single_tool(
        self,
        desc: _ToolCallDescriptor,
        step: int,
    ) -> _ToolDispatchResult:
        """Execute a single tool call and return a structured result."""
        ctx = self.context
        tool_id = desc.tool_id
        params = desc.params
        await self.emit("tool.called", tool_id=tool_id, params=params)

        if tool_id not in (ctx.tools or {}):
            err_msg = f"Tool '{tool_id}' is not registered."
            await self.emit("tool.failed", tool_id=tool_id, error=err_msg)
            self._emit_tool_failure_hook(tool_id, err_msg, step=step)
            return _ToolDispatchResult(
                desc=desc,
                success=False,
                error=err_msg,
                data=None,
                executor_meta=None,
            )

        # Tool gating safety net: if exploration tools were removed from the
        # schema but the model still tries to call one, reject it.
        consecutive_readonly = ctx.state.get("__consecutive_readonly_steps__", 0)
        if (
            self._enable_tool_gating
            and consecutive_readonly >= self._tool_gating_threshold
            and self._is_exploration_tool_id(tool_id)
        ):
            err_msg = (
                f"Tool '{tool_id}' is disabled because the exploration budget is exhausted. "
                "Use edit_file, bash, ask_human, or complete_task instead."
            )
            await self.emit("tool.failed", tool_id=tool_id, error=err_msg)
            self._emit_tool_failure_hook(tool_id, err_msg, step=step)
            return _ToolDispatchResult(
                desc=desc,
                success=False,
                error=err_msg,
                data=None,
                executor_meta=None,
            )

        tool = ctx.tools[tool_id]

        hooks = self._get_hooks()

        # Hook: pattern.before_tool
        if hooks is not None:
            before_pattern_payload = hooks.run(
                HookEvent.PATTERN_BEFORE_TOOL,
                {"tool_id": tool_id, "params": dict(params), "step": step},
            )
            if isinstance(before_pattern_payload, dict) and before_pattern_payload.get("blocked"):
                reason = before_pattern_payload.get("reason", "blocked by pattern.before_tool hook")
                err_msg = f"Hook blocked: {reason}"
                await self.emit("tool.failed", tool_id=tool_id, error=reason)
                self._emit_tool_failure_hook(tool_id, err_msg, step=step)
                return _ToolDispatchResult(
                    desc=desc,
                    success=False,
                    error=err_msg,
                    data=None,
                    executor_meta=None,
                )
            params = before_pattern_payload.get("params", params)

        # Hook: tool.before_invoke
        if hooks is not None:
            before_payload = hooks.run(
                HookEvent.TOOL_BEFORE_INVOKE,
                {"tool_id": tool_id, "params": dict(params), "step": step},
            )
            if before_payload.get("blocked"):
                reason = before_payload.get("reason", "blocked by hook")
                err_msg = f"Hook blocked: {reason}"
                await self.emit("tool.failed", tool_id=tool_id, error=reason)
                self._emit_tool_failure_hook(tool_id, err_msg, step=step)
                return _ToolDispatchResult(
                    desc=desc,
                    success=False,
                    error=err_msg,
                    data=None,
                    executor_meta=None,
                )
            params = before_payload.get("params", params)

        try:
            raw_result = await tool.invoke(params, ctx)
        except ModelRetryError as retry_exc:
            await self.emit(
                "tool.retry_requested",
                tool_id=tool_id,
                error=str(retry_exc),
            )
            self._emit_tool_failure_hook(tool_id, str(retry_exc), exception=retry_exc, step=step)
            return _ToolDispatchResult(
                desc=desc,
                success=False,
                error=str(retry_exc),
                data=None,
                executor_meta=None,
            )
        except ToolError as tool_exc:
            await self.emit(
                "tool.failed",
                tool_id=tool_id,
                error=str(tool_exc),
                error_details=error_details_payload(tool_exc),
            )
            self._emit_tool_failure_hook(tool_id, str(tool_exc), exception=tool_exc, step=step)
            return _ToolDispatchResult(
                desc=desc,
                success=False,
                error=f"Tool error: {tool_exc}",
                data=None,
                executor_meta=None,
            )
        except Exception as exc:  # pragma: no cover - safety net
            await self.emit(
                "tool.failed",
                tool_id=tool_id,
                error=str(exc),
                error_details=error_details_payload(exc),
            )
            self._emit_tool_failure_hook(tool_id, str(exc), exception=exc, step=step)
            return _ToolDispatchResult(
                desc=desc,
                success=False,
                error=f"Unexpected tool failure: {exc}",
                data=None,
                executor_meta=None,
            )

        data, executor_meta = unwrap_tool_result(raw_result)

        # Hook: tool.after_invoke
        if hooks is not None:
            after_payload = hooks.run(
                HookEvent.TOOL_AFTER_INVOKE,
                {
                    "tool_id": tool_id,
                    "params": dict(params),
                    "result": data,
                    "executor_meta": executor_meta,
                    "step": step,
                },
            )
            data = after_payload.get("result", data)
            executor_meta = after_payload.get("executor_meta", executor_meta)

        # Hook: pattern.after_tool
        if hooks is not None:
            hooks.run(
                HookEvent.PATTERN_AFTER_TOOL,
                {
                    "tool_id": tool_id,
                    "params": dict(params),
                    "result": data,
                    "success": True,
                    "step": step,
                },
            )

        await self.emit(
            "tool.succeeded",
            tool_id=tool_id,
            result=data,
            executor_metadata=executor_meta,
        )
        return _ToolDispatchResult(
            desc=desc,
            success=True,
            error=None,
            data=data,
            executor_meta=executor_meta,
        )

    def _emit_tool_failure_hook(
        self,
        tool_id: str,
        error: str,
        exception: BaseException | None = None,
        *,
        step: int = 0,
    ) -> None:
        """Emit the ``tool.failure`` hook so consumers can grade the failure."""
        hooks = self._get_hooks()
        if hooks is None:
            return
        ctx = self.context
        consecutive = 0
        if ctx is not None:
            consecutive = int(ctx.state.get("__consecutive_tool_failures__", 0))
        hooks.run(
            HookEvent.TOOL_FAILURE,
            {
                "tool_id": tool_id,
                "error": error,
                "exception": exception,
                "step": step,
                "consecutive_failures": consecutive,
                "agent_id": getattr(ctx, "agent_id", None) if ctx is not None else None,
            },
        )

    def _apply_tool_dispatch_result(
        self,
        result: _ToolDispatchResult | None,
        step: int,
        as_openai_messages: bool,
    ) -> dict[str, Any]:
        """Apply side effects for one tool result and build the payload."""
        ctx = self.context
        if result is None:
            return self._tool_result_message(
                tool_call_id="unknown",
                tool_name="unknown",
                content="Tool dispatch returned no result.",
                is_error=True,
                as_openai_message=as_openai_messages,
            )

        desc = result.desc
        tool_id = desc.tool_id
        call_id = desc.call_id
        params = desc.params

        if not result.success:
            ctx.state["__consecutive_tool_failures__"] = (
                ctx.state.get("__consecutive_tool_failures__", 0) + 1
            )
            _record_recent_error(ctx, tool_id, result.error or "unknown error")
            if tool_id in ("edit_file", "apply_patch", "semantic_edit"):
                self._record_edit_failure(tool_id, params, result.error)
            return self._tool_result_message(
                tool_call_id=call_id,
                tool_name=tool_id,
                content=result.error or "Tool failed.",
                is_error=True,
                as_openai_message=as_openai_messages,
            )

        # Reset tool failure counter on success.
        ctx.state["__consecutive_tool_failures__"] = 0
        ctx.tool_results.append({"tool_id": tool_id, "result": result.data})
        if ctx.usage is not None:
            ctx.usage.tool_calls += 1

        # Track file edits for verification nudge and enforcement.
        if tool_id in ("write_file", "edit_file", "apply_patch", "semantic_edit"):
            self._record_pending_verification(tool_id, params, step)
            self._clear_edit_failure(tool_id, params)

        # A bash command may clear pending verification if it matches the
        # recommended test/lint command and exits cleanly.
        if tool_id == "bash":
            self._try_clear_verification(params, result.data)
            if (
                isinstance(result.data, dict)
                and result.data.get("requires_permission")
                and self._permission_mode != "auto"
            ):
                ctx.state["__bash_permission_required__"] = {
                    "command": result.data.get("command", ""),
                    "reason": result.data.get("reason", ""),
                }

        # Clearing a permission request.
        if tool_id == "ask_human":
            ctx.state.pop("__bash_permission_required__", None)

        # Very long outputs are spilled to disk so they don't bloat context.
        raw_payload = _format_tool_result(result.data, truncate=False)
        spill_path = self._spill_tool_output_if_large(tool_id, raw_payload)
        if spill_path:
            payload = (
                f"{raw_payload[:_SPILL_OUTPUT_HEAD]}\n\n"
                f"... ({len(raw_payload)} chars total; full output written to {spill_path})\n"
                "Use read_file to inspect the full content if needed."
            )
        else:
            payload = _format_tool_result(result.data)
        return self._tool_result_message(
            tool_call_id=call_id,
            tool_name=tool_id,
            content=payload,
            is_error=False,
            as_openai_message=as_openai_messages,
        )

    async def _invoke_llm(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Any:
        """Call ctx.llm_client.generate() with usage tracking + events."""
        ctx = self.context
        model = self._model_override
        await self.emit("llm.called", model=model)

        # Hook: pattern.before_llm
        hooks = self._get_hooks()
        if hooks is not None:
            before_payload = hooks.run(
                HookEvent.PATTERN_BEFORE_LLM,
                {
                    "messages": list(messages),
                    "tools": list(tools),
                    "model": model,
                },
            )
            messages = before_payload.get("messages", messages)
            tools = before_payload.get("tools", tools)

        started = time.monotonic()
        try:
            response = None
            last_exc: BaseException | None = None
            for attempt in range(3):
                try:
                    response = await ctx.llm_client.generate(
                        messages=messages,
                        model=model,
                        temperature=self._temperature,
                        max_tokens=self._max_tokens,
                        tools=tools,
                    )
                    break
                except BaseException as exc:
                    last_exc = exc
                    if attempt == 2 or not is_retryable_llm_error(exc):
                        raise
                    await asyncio.sleep(1.5 * (attempt + 1))
            if response is None and last_exc is not None:
                raise last_exc
        except asyncio.CancelledError:
            # Resident was stopped while an LLM call was in flight. This is a
            # normal lifecycle event, not an API failure. Emit a distinct event
            # and re-raise so the caller (resident loop) can shut down cleanly.
            latency_ms = (time.monotonic() - started) * 1000.0
            metrics = LLMCallMetrics(
                model=model or "",
                latency_ms=latency_ms,
                input_tokens=0,
                output_tokens=0,
                cached_tokens=0,
                error="cancelled",
            )
            await self.emit(
                "llm.cancelled",
                model=model,
                _metrics=metrics,
            )
            raise
        except BaseException as exc:
            latency_ms = (time.monotonic() - started) * 1000.0
            ctx.state["__api_error_count__"] = ctx.state.get("__api_error_count__", 0) + 1
            metrics = LLMCallMetrics(
                model=model or "",
                latency_ms=latency_ms,
                input_tokens=0,
                output_tokens=0,
                cached_tokens=0,
                error=str(exc),
            )
            await self.emit(
                "llm.failed",
                model=model,
                error=str(exc),
                _metrics=metrics,
                error_details=error_details_payload(exc),
            )
            raise
        latency_ms = (time.monotonic() - started) * 1000.0

        cached_read = 0
        if ctx.usage is not None:
            ctx.usage.llm_calls += 1
            if response.usage is not None:
                ctx.usage.input_tokens += response.usage.input_tokens
                ctx.usage.output_tokens += response.usage.output_tokens
                ctx.usage.total_tokens += response.usage.total_tokens
                meta = response.usage.metadata or {}
                cached_read = int(
                    meta.get("cache_read_input_tokens", meta.get("cached_tokens", 0)) or 0
                )
                cache_creation = int(meta.get("cache_creation_input_tokens", 0) or 0)
                ctx.usage.input_tokens_cached += cached_read
                ctx.usage.input_tokens_cache_creation += cache_creation
                call_cost = meta.get("cost_usd")
                if call_cost is None:
                    ctx.scratch["__cost_unavailable__"] = True
                    ctx.usage.cost_usd = None
                else:
                    current = ctx.usage.cost_usd if ctx.usage.cost_usd is not None else 0.0
                    ctx.usage.cost_usd = current + float(call_cost)
                    for bucket, amount in (meta.get("cost_breakdown") or {}).items():
                        ctx.usage.cost_breakdown[bucket] = (
                            ctx.usage.cost_breakdown.get(bucket, 0.0) + float(amount)
                        )

        metrics = LLMCallMetrics(
            model=model or "",
            latency_ms=latency_ms,
            input_tokens=response.usage.input_tokens if response.usage else 0,
            output_tokens=response.usage.output_tokens if response.usage else 0,
            cached_tokens=cached_read,
        )

        # Hooks: llm.after_call + pattern.after_llm
        hooks = self._get_hooks()
        if hooks is not None:
            hooks.run(
                HookEvent.LLM_AFTER_CALL,
                {
                    "agent_id": getattr(ctx, "agent_id", None),
                    "metrics": metrics,
                    "model": model,
                },
            )
            hooks.run(
                HookEvent.PATTERN_AFTER_LLM,
                {
                    "messages": list(messages),
                    "tools": list(tools),
                    "response": response,
                    "model": model,
                },
            )

        await self.emit(
            "usage.updated",
            usage=ctx.usage.model_dump() if ctx.usage else None,
        )
        await self.emit("llm.succeeded", model=model, _metrics=metrics)
        return response

    def _provider_name(self) -> str:
        ctx = self.context
        return str(getattr(getattr(ctx, "llm_client", None), "provider_name", "") or "")

    def _uses_openai_conversation(self) -> bool:
        return self._provider_name() != "anthropic"

    async def _should_continue_step(self, step: int) -> bool:
        """Hook for subclasses to terminate the ReAct loop early.

        Return False to stop looping before max_steps is reached.
        Called after each tool-dispatch turn (not after text-only turns).
        """
        ctx = self.context
        if ctx is None:
            return True
        if ctx.state.get("__complete_task_summary__"):
            return False
        return not ctx.state.get("__awaiting_human_reply__")

    async def _should_accept_text_response(self, text: str) -> bool:
        """Hook for subclasses to reject text-only responses.

        Return False to force the model back into the tool-calling loop.
        Useful for agents (like Director) that must always end with a tool call.
        """
        return True

    # ---- streaming support -------------------------------------------------

    async def _invoke_llm_stream(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Any:
        """Yield streaming chunks from the LLM, with fallback to non-streaming.

        If the underlying client exposes ``complete_stream``, use it; otherwise
        call ``generate`` and synthesize chunks so callers always see a uniform
        stream shape.
        """
        ctx = self.context
        model = self._model_override
        llm_client = ctx.llm_client if ctx else None
        if llm_client is None:
            raise RuntimeError("CoreCoderPattern needs an llm_client")

        await self.emit("llm.called", model=model)
        hooks = self._get_hooks()
        if hooks is not None:
            before_payload = hooks.run(
                HookEvent.PATTERN_BEFORE_LLM,
                {
                    "messages": list(messages),
                    "tools": list(tools),
                    "model": model,
                },
            )
            messages = before_payload.get("messages", messages)
            tools = before_payload.get("tools", tools)

        # Prefer native streaming when available.
        stream_fn = getattr(llm_client, "complete_stream", None)
        if callable(stream_fn):
            try:
                async for chunk in stream_fn(
                    messages=messages,
                    model=model,
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                    tools=tools,
                ):
                    yield chunk
            except BaseException as exc:
                ctx.state["__api_error_count__"] = ctx.state.get("__api_error_count__", 0) + 1
                await self.emit("llm.failed", model=model, error=str(exc))
                raise
            return

        # Fallback: non-streaming generate() → synthetic chunks.
        response = await self._invoke_llm(messages=messages, tools=tools)
        if response.output_text:
            yield LLMChunk(
                type="content_block_delta",
                delta={"type": "text_delta", "text": response.output_text},
                content={"type": "text", "text": response.output_text},
            )
        for call in (response.tool_calls or []):
            arguments = call.arguments if isinstance(call.arguments, dict) else {}
            try:
                args_json = json.dumps(arguments, ensure_ascii=False)
            except (TypeError, ValueError):
                args_json = "{}"
            yield LLMChunk(
                type="content_block_start",
                content={
                    "type": "tool_use",
                    "id": call.id or f"call_{uuid4().hex[:12]}",
                    "name": call.name,
                },
            )
            yield LLMChunk(
                type="content_block_delta",
                delta={"type": "input_json_delta", "partial_json": args_json},
            )
        if response.usage is not None:
            yield LLMChunk(
                type="message_stop",
                content={"stop_reason": response.stop_reason or "stop"},
                usage=response.usage,
            )
        else:
            yield LLMChunk(
                type="message_stop",
                content={"stop_reason": response.stop_reason or "stop"},
            )

    async def _preexecute_readonly_tool(
        self,
        tool_id: str,
        params: dict[str, Any],
        cache: PreExecutionCache,
    ) -> None:
        """Pre-execute a read-only tool and cache its result.

        Failures are swallowed; the main loop will retry on the real call.
        """
        ctx = self.context
        if ctx is None:
            return
        if tool_id not in (ctx.tools or {}):
            return
        if not _is_tool_concurrency_safe(ctx.tools, tool_id):
            return
        if tool_id in ("write_file", "edit_file", "apply_patch", "semantic_edit", "bash"):
            return

        if cache.get(tool_id, params) is not None:
            return

        try:
            tool = ctx.tools[tool_id]
            raw_result = await tool.invoke(params, ctx)
            data, _ = unwrap_tool_result(raw_result)
            cache.set(tool_id, params, data)
        except Exception:
            # Pre-execution is best-effort; let the main loop handle real errors.
            pass

    async def execute_stream(self) -> AsyncGenerator[StreamEvent, None]:
        """Run the ReAct loop as an async generator of StreamEvent."""
        ctx = self.context
        if ctx is None:
            raise RuntimeError("CoreCoderPattern.execute_stream requires setup() first")
        if ctx.llm_client is None:
            raise RuntimeError("CoreCoderPattern needs an llm_client")

        if self._plan_mode:
            ctx.state["__plan_mode_active__"] = True

        tool_schemas = self._build_tool_schemas()

        messages: list[dict[str, Any]] = []
        for entry in ctx.transcript:
            normalized = _normalize_history_message(entry)
            if normalized is not None:
                messages.append(normalized)

        if not _last_message_is_user(messages):
            messages.append({"role": "user", "content": ctx.input_text})

        if self._enable_planning and not ctx.state.get("__plan__"):
            plan = await self._generate_plan(messages, tool_schemas)
            if plan:
                ctx.state["__plan__"] = plan

        if self._plan_mode and not ctx.state.get("__plan_approved__"):
            plan = ctx.state.get("__plan__") or {}
            plan_text = json.dumps(plan, ensure_ascii=False, indent=2)
            plan_path = self._write_plan_file(plan_text)
            ctx.state["__plan_mode_active__"] = True
            self._writeback_context()
            yield StreamEvent(
                type=StreamEventType.complete,
                text=(
                    f"[Plan mode] Generated plan file: {plan_path}\n\n"
                    f"{plan_text}\n\n"
                    "Review the plan and call `approve_plan` to continue execution."
                ),
            )
            return

        system_prompt = self.compose_system_prompt("")
        system_messages = _split_system_prompt(system_prompt)
        final_text = ""
        consecutive_empty = 0
        max_consecutive_empty = 2
        preexec_cache = PreExecutionCache()

        for step in range(1, self._max_steps + 1):
            pending = ctx.state.get("__pending_verification__")
            if isinstance(pending, dict) and pending.get("attempts", 0) >= 3:
                ctx.state.pop("__pending_verification__", None)
            ctx.state["__verification_attempted__"] = False

            parser = StreamToolCallParser()
            collected_text = ""
            tool_calls_for_dispatch: list[Any] = []
            preexec_tasks: list[asyncio.Task[Any]] = []

            async for chunk in self._invoke_llm_stream(
                messages=[*system_messages, *messages],
                tools=tool_schemas,
            ):
                chunk_type = getattr(chunk, "type", None)
                delta = getattr(chunk, "delta", None) or {}
                content = getattr(chunk, "content", None) or {}

                if chunk_type == "content_block_delta" and delta.get("type") == "text_delta":
                    text_delta = delta.get("text", "")
                    if text_delta:
                        collected_text += text_delta
                        yield StreamEvent(
                            type=StreamEventType.text,
                            text=text_delta,
                            step=step,
                        )

                if chunk_type == "content_block_start" and content.get("type") == "tool_use":
                    yield StreamEvent(
                        type=StreamEventType.tool_call_start,
                        tool_id=content.get("id"),
                        tool_name=content.get("name"),
                        step=step,
                    )

                newly_completed = parser.feed_chunk(chunk)
                for call in newly_completed:
                    yield StreamEvent(
                        type=StreamEventType.tool_call_complete,
                        tool_id=call.get("id"),
                        tool_name=call.get("name"),
                        params=call.get("arguments", {}),
                        step=step,
                    )
                    # Kick off pre-execution for read-only tools immediately.
                    task = asyncio.create_task(
                        self._preexecute_readonly_tool(
                            call.get("name", ""),
                            call.get("arguments", {}),
                            preexec_cache,
                        )
                    )
                    preexec_tasks.append(task)

                if chunk_type == "message_stop":
                    pass

            # Ensure all pre-execution attempts finish before dispatch.
            if preexec_tasks:
                await asyncio.gather(*preexec_tasks, return_exceptions=True)

            tool_calls_for_dispatch = parser.finalize()

            if not tool_calls_for_dispatch:
                stripped = collected_text.strip()
                if stripped:
                    pending = ctx.state.get("__pending_verification__")
                    if isinstance(pending, dict) and pending.get("files"):
                        messages.append(self._assistant_text_message(stripped, []))
                        enforcement = self._verification_enforcement_message()
                        if enforcement:
                            messages.append({"role": "user", "content": enforcement})
                        continue
                    permission_msg = self._build_permission_required_message()
                    if permission_msg:
                        messages.append(self._assistant_text_message(stripped, []))
                        messages.append({"role": "user", "content": permission_msg})
                        continue

                    if await self._should_accept_text_response(stripped):
                        final_text = stripped
                        messages.append(self._assistant_text_message(stripped, []))
                        ctx.state["__steps_used__"] = step
                        ctx.state["__tool_calls_used__"] = (
                            sum(1 for m in messages if m.get("role") == "assistant" and "tool_calls" in m)
                        )
                        await self.emit("pattern.completed", steps=step, final_chars=len(final_text))
                        yield StreamEvent(
                            type=StreamEventType.complete,
                            text=final_text,
                            step=step,
                        )
                        break

                    consecutive_text_rejected = ctx.state.get("__consecutive_text_rejected__", 0) + 1
                    ctx.state["__consecutive_text_rejected__"] = consecutive_text_rejected
                    await self.emit("pattern.text_rejected", step=step, consecutive=consecutive_text_rejected)
                    if consecutive_text_rejected >= 2:
                        final_text = (
                            f"[CoreCoder] text response rejected twice; accepting to avoid loop.\n\n{stripped}"
                        )
                        ctx.state["__steps_used__"] = step
                        ctx.state["__tool_calls_used__"] = (
                            sum(1 for m in messages if m.get("role") == "assistant" and "tool_calls" in m)
                        )
                        yield StreamEvent(
                            type=StreamEventType.complete,
                            text=final_text,
                            step=step,
                        )
                        break

                    messages.append(self._assistant_text_message(stripped, []))
                    messages.append({
                        "role": "user",
                        "content": (
                            "Your text response was not accepted. "
                            "You must call a tool on every turn. "
                            "If all tasks are complete, call finalize. "
                            "If a task failed, call replan, retry, ask_human, or spawn_resident. "
                            "Do not output tool-less text."
                        ),
                    })
                    continue

                consecutive_empty += 1
                ctx.state["__consecutive_empty_responses__"] = consecutive_empty
                await self.emit("pattern.empty_response", step=step, consecutive=consecutive_empty)
                if consecutive_empty >= max_consecutive_empty:
                    final_text = (
                        f"[CoreCoder] model returned {consecutive_empty} consecutive empty responses; "
                        "stopping without a final answer."
                    )
                    ctx.state["__steps_used__"] = step
                    ctx.state["__tool_calls_used__"] = (
                        sum(1 for m in messages if m.get("role") == "assistant" and "tool_calls" in m)
                    )
                    yield StreamEvent(
                        type=StreamEventType.complete,
                        text=final_text,
                        step=step,
                    )
                    break
                messages.append({
                    "role": "user",
                    "content": (
                        "Your previous response was empty. If you have completed all required "
                        "deliverables, respond with a brief text summary. Otherwise, continue "
                        "by calling exactly one of the available tools."
                    ),
                })
                continue

            # We have tool calls to dispatch.
            consecutive_empty = 0

            # Convert parsed tool calls to the shape expected by _dispatch_tool_calls.
            class _ParsedToolCall:
                def __init__(self, data: dict[str, Any]):
                    self.name = data.get("name", "")
                    self.arguments = data.get("arguments", {})
                    self.id = data.get("id")

            parsed_calls = [_ParsedToolCall(c) for c in tool_calls_for_dispatch]

            # Check pre-execution cache and build result messages directly for hits.
            remaining_calls: list[Any] = []
            cached_results: list[dict[str, Any]] = []
            for call in parsed_calls:
                cached = preexec_cache.get(call.name, call.arguments)
                if cached is not None:
                    payload = _format_tool_result(cached)
                    cached_results.append(
                        self._tool_result_message(
                            tool_call_id=call.id or f"call_{uuid4().hex[:12]}",
                            tool_name=call.name,
                            content=payload,
                            is_error=False,
                            as_openai_message=self._uses_openai_conversation(),
                        )
                    )
                    yield StreamEvent(
                        type=StreamEventType.tool_result,
                        tool_id=call.id,
                        tool_name=call.name,
                        result=cached,
                        step=step,
                        metadata={"preexecuted": True},
                    )
                else:
                    remaining_calls.append(call)

            if remaining_calls:
                if self._uses_openai_conversation():
                    messages.append(self._assistant_tool_call_message(remaining_calls, collected_text))
                    tool_result_messages = await self._dispatch_tool_calls(
                        remaining_calls,
                        step=step,
                        as_openai_messages=True,
                    )
                    messages.extend(tool_result_messages)
                else:
                    messages.append({"role": "assistant", "content": _wrap_text(collected_text)})
                    tool_result_blocks = await self._dispatch_tool_calls(remaining_calls, step=step)
                    messages.append({"role": "user", "content": tool_result_blocks})

                # Yield tool_result events for dispatched calls.
                for call in remaining_calls:
                    # Find the result message for this call.
                    result_content = ""
                    for msg in messages:
                        if msg.get("role") == "tool" and msg.get("tool_call_id") == call.id:
                            result_content = msg.get("content", "")
                            break
                    yield StreamEvent(
                        type=StreamEventType.tool_result,
                        tool_id=call.id,
                        tool_name=call.name,
                        result=result_content,
                        step=step,
                    )

            messages.extend(cached_results)

            self._update_exploration_cache(parsed_calls, step)
            edit_recovery = self._build_edit_recovery_message()
            if edit_recovery:
                messages.append({"role": "user", "content": edit_recovery})
            permission_msg = self._build_permission_required_message()
            if permission_msg:
                messages.append({"role": "user", "content": permission_msg})
            pending = ctx.state.get("__pending_verification__")
            if isinstance(pending, dict) and pending.get("files"):
                enforcement = self._verification_enforcement_message()
                if enforcement:
                    messages.append({"role": "user", "content": enforcement})
            else:
                verification_nudge = self._build_verification_nudge()
                if verification_nudge:
                    messages.append({"role": "user", "content": verification_nudge})

            failure_count = ctx.state.get("__consecutive_tool_failures__", 0)
            if failure_count >= 2:
                diagnosis = self._build_diagnosis_message(failure_count)
                if diagnosis:
                    messages.append({"role": "user", "content": diagnosis})
                    await self.emit("pattern.error_recovery_triggered", step=step, consecutive_failures=failure_count)
                if not ctx.state.get("__replan_after_failures__"):
                    new_plan = await self._generate_plan(messages, tool_schemas)
                    if new_plan:
                        ctx.state["__plan__"] = new_plan
                    ctx.state["__replan_after_failures__"] = True
                    messages.append({
                        "role": "user",
                        "content": (
                            "Repeated tool failures. I have regenerated the plan. "
                            "Review the updated plan and proceed with the next step."
                        ),
                    })

            if not await self._should_continue_step(step):
                complete_summary = ctx.state.get("__complete_task_summary__")
                final_text = complete_summary or "[CoreCoder] loop terminated by pattern condition."
                ctx.state["__steps_used__"] = step
                ctx.state["__tool_calls_used__"] = (
                    sum(1 for m in messages if m.get("role") == "assistant" and "tool_calls" in m)
                )
                await self.emit("pattern.completed" if complete_summary else "pattern.terminated", steps=step)
                yield StreamEvent(
                    type=StreamEventType.complete,
                    text=final_text,
                    step=step,
                )
                break
        else:
            await self.emit("pattern.step_budget_exhausted", max_steps=self._max_steps)
            ctx.state["__step_budget_exhausted__"] = True
            ctx.state["__steps_used__"] = self._max_steps
            ctx.state["__tool_calls_used__"] = (
                sum(1 for m in messages if m.get("role") == "assistant" and "tool_calls" in m)
            )
            final_text = final_text or "[CoreCoder] step budget exhausted before producing a final answer."
            yield StreamEvent(
                type=StreamEventType.complete,
                text=final_text,
                step=self._max_steps,
            )

        ctx.transcript[:] = [
            dict(entry)
            for entry in messages
            if entry.get("role") in ("user", "assistant", "tool")
        ]
        self._writeback_context()

    # ---- planning / verification / cache / error-recovery helpers ---------

    async def _generate_plan(
        self,
        messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Generate a lightweight execution plan before the ReAct loop.

        The planning call is tool-less and short. It returns a structured plan
        stored in ctx.state["__plan__"] and rendered in the dynamic prompt.
        """
        ctx = self.context
        if ctx is None or ctx.llm_client is None:
            return None

        system_prompt = self.compose_system_prompt("")
        system_messages = _split_system_prompt(system_prompt)
        planning_messages = [
            *system_messages,
            *messages,
            {
                "role": "user",
                "content": (
                    "Before taking any action, produce a concise execution plan. "
                    "Respond with valid JSON only, no markdown, no extra text. "
                    "Rate your confidence (0-10) in the task as stated. "
                    "If confidence is low (< 5), include a clarifying question to ask the human."
                ),
            },
        ]
        try:
            plan, _ = await structured_generate(
                messages=planning_messages,
                response_model=_PlanSchema,
                llm_client=ctx.llm_client,
                temperature=self._temperature if self._temperature is not None else 0.2,
                max_tokens=512,
            )
            return plan.model_dump(exclude_none=True)
        except Exception:
            # Planning is best-effort; do not fail the whole run.
            pass
        return None

    def _clarification_needed(self, plan: dict[str, Any]) -> str | None:
        """Return a clarification question if the plan is too uncertain."""
        if not self._enable_clarification:
            return None
        confidence = plan.get("confidence", 5)
        question = plan.get("clarification_needed", "")
        if (
            isinstance(question, str)
            and question.strip()
            and isinstance(confidence, (int, float))
            and confidence < self._clarification_confidence_threshold
        ):
            return question.strip()
        return None

    async def _request_clarification_and_pause(
        self,
        question: str,
    ) -> PatternOutcome:
        """Record a clarifying question and pause the loop for a human reply."""
        ctx = self.context
        if ctx is None:
            return PatternOutcome(
                output="[Awaiting human reply] No RunContext available.",
                status=PatternOutcomeStatus.AWAITING_HUMAN,
            )

        # Try to use the registered ask_human tool so the question is recorded
        # consistently with the rest of the system.
        ask_tool = None
        if ctx.tools is not None:
            for tool_id, bound in ctx.tools.items():
                if tool_id == "ask_human":
                    raw = getattr(bound, "_tool", bound)
                    ask_tool = raw
                    break

        if ask_tool is not None:
            try:
                await ask_tool.invoke({"question": question}, ctx)
            except Exception:
                # Fallback to direct state recording if the tool fails.
                ctx.state["__pending_human_question__"] = {
                    "qid": None,
                    "question": question,
                    "options": "",
                    "from_agent": getattr(ctx, "agent_id", "unknown"),
                }
                ctx.state["__awaiting_human_reply__"] = {
                    "qid": None,
                    "question": question,
                    "options": "",
                    "from_agent": getattr(ctx, "agent_id", "unknown"),
                }
        else:
            ctx.state["__pending_human_question__"] = {
                "qid": None,
                "question": question,
                "options": "",
                "from_agent": getattr(ctx, "agent_id", "unknown"),
            }
            ctx.state["__awaiting_human_reply__"] = {
                "qid": None,
                "question": question,
                "options": "",
                "from_agent": getattr(ctx, "agent_id", "unknown"),
            }

        self._writeback_context()
        return PatternOutcome(
            output=f"[Awaiting human reply] {question}\n\nPlease reply to continue.",
            status=PatternOutcomeStatus.AWAITING_HUMAN,
        )

    def _update_exploration_cache(
        self,
        tool_calls: list[Any],
        step: int,
    ) -> None:
        """Cache recent read/grep/glob results to reduce redundant exploration."""
        ctx = self.context
        if ctx is None:
            return

        for call in tool_calls:
            tool_id = call.name
            params = call.arguments if isinstance(call.arguments, dict) else {}
            if tool_id == "read_file":
                path = str(params.get("file_path", ""))
                if path:
                    cache = ctx.scratch.setdefault("_file_cache", {})
                    cache[path] = {
                        "lines": int(params.get("limit", 0)) or int(params.get("offset", 0)) or 0,
                        "read_at_step": step,
                    }
                    # Simple LRU cap at 50 entries.
                    if len(cache) > 50:
                        oldest = min(cache, key=lambda k: cache[k].get("read_at_step", 0))
                        cache.pop(oldest, None)
            elif tool_id == "glob":
                pattern = str(params.get("pattern", ""))
                if pattern:
                    cache = ctx.scratch.setdefault("_glob_cache", {})
                    cache[pattern] = {"read_at_step": step}
                    if len(cache) > 50:
                        oldest = min(cache, key=lambda k: cache[k].get("read_at_step", 0))
                        cache.pop(oldest, None)
            elif tool_id == "grep":
                pattern = str(params.get("pattern", ""))
                if pattern:
                    cache = ctx.scratch.setdefault("_grep_cache", {})
                    cache[pattern] = {"read_at_step": step}
                    if len(cache) > 50:
                        oldest = min(cache, key=lambda k: cache[k].get("read_at_step", 0))
                        cache.pop(oldest, None)
            elif tool_id == "list_directory":
                path = str(params.get("path", "")) or "."
                if path:
                    cache = ctx.scratch.setdefault("_list_dir_cache", {})
                    cache[path] = {"read_at_step": step}
                    if len(cache) > 50:
                        oldest = min(cache, key=lambda k: cache[k].get("read_at_step", 0))
                        cache.pop(oldest, None)

    def _build_verification_nudge(self) -> str | None:
        """Return a nudge when the agent just edited files and should verify."""
        ctx = self.context
        if ctx is None:
            return None
        pending = ctx.state.get("__pending_verification__")
        if not isinstance(pending, dict) or not pending.get("files"):
            return None

        files = pending.get("files", [])
        tool = pending.get("tool", "")
        attempts = pending.get("attempts", 0)
        if attempts >= 3:
            # Cap verification-driven retries.
            ctx.state.pop("__pending_verification__", None)
            return None

        pending["attempts"] = attempts + 1
        return (
            f"You just used `{tool}` on {', '.join(str(f) for f in files)}. "
            "Verification: run the relevant tests or lint now. If they fail, diagnose and fix "
            "before continuing. If there are no tests, run the code to confirm it works."
        )

    def _get_hooks(self) -> Any:
        """Return the optional HookManager from context deps."""
        ctx = self.context
        if ctx is None:
            return None
        deps = getattr(ctx, "deps", None)
        if deps is None:
            return None
        return getattr(deps, "hooks", None)

    def _record_pending_verification(
        self, tool_id: str, params: dict[str, Any], step: int
    ) -> None:
        """Record a file edit so the loop can enforce verification before finishing."""
        ctx = self.context
        if ctx is None:
            return

        if self._permission_mode in ("acceptEdits", "auto"):
            return

        edit_path = params.get("file_path") or ""
        if tool_id == "apply_patch":
            # apply_patch returns details with patched files; we can't easily extract
            # them from params here, so we rely on the tool having recorded dirty_files.
            dirty = ctx.scratch.get("dirty_files")
            files = sorted(dirty) if isinstance(dirty, set) else []
        else:
            files = [edit_path] if edit_path else []

        if not files:
            return

        cwd = ctx.scratch.get("bash_cwd") or os.getcwd()
        project = _detect_project_type(cwd) or {}

        pending: dict[str, Any] = ctx.state.setdefault("__pending_verification__", {})
        pending["tool"] = tool_id
        pending.setdefault("files", [])
        for f in files:
            if f and f not in pending["files"]:
                pending["files"].append(f)
        pending["step"] = step
        pending["test_cmd"] = project.get("test_cmd")
        pending["lint_cmd"] = project.get("lint_cmd")
        pending.setdefault("attempts", 0)

    def _try_clear_verification(
        self, params: dict[str, Any], result_data: Any
    ) -> None:
        """If the bash command was the recommended verification command, evaluate it."""
        ctx = self.context
        if ctx is None:
            return
        pending = ctx.state.get("__pending_verification__")
        if not isinstance(pending, dict):
            return

        command = str(params.get("command", "")).strip()
        test_cmd = pending.get("test_cmd")
        lint_cmd = pending.get("lint_cmd")

        matched = False
        if test_cmd and command.startswith(test_cmd) or lint_cmd and command.startswith(lint_cmd):
            matched = True

        if not matched:
            return

        ctx.state["__verification_attempted__"] = True

        if not isinstance(result_data, dict):
            return

        exit_code = result_data.get("exit_code")
        if exit_code == 0:
            ctx.state.pop("__pending_verification__", None)
            ctx.state.pop("__verification_errors__", None)
            return

        # Verification failed: parse output and keep pending active.
        pending["attempts"] = pending.get("attempts", 0) + 1
        output = ""
        for key in ("stdout", "stderr", "message"):
            val = result_data.get(key)
            if isinstance(val, str):
                output += val + "\n"
        errors = parse_pytest_output(output)
        ctx.state["__verification_errors__"] = errors

    def _verification_enforcement_message(self) -> str | None:
        """Return a message that forces the model to verify pending edits."""
        ctx = self.context
        if ctx is None:
            return None
        pending = ctx.state.get("__pending_verification__")
        if not isinstance(pending, dict) or not pending.get("files"):
            return None

        files = pending.get("files", [])
        test_cmd = pending.get("test_cmd")
        lint_cmd = pending.get("lint_cmd")
        errors = ctx.state.get("__verification_errors__", [])

        lines = [
            f"You have unverified edits on {', '.join(str(f) for f in files)}. "
            "You MUST run the appropriate verification command before continuing."
        ]
        if test_cmd:
            lines.append(f"Run tests: `{test_cmd}`")
        if lint_cmd:
            lines.append(f"Run lint: `{lint_cmd}`")
        if errors:
            lines.append("Previous verification failed with:")
            for e in errors[:5]:
                loc = f"{e.get('file')}:{e.get('line')}" if e.get("line") else e.get("file")
                lines.append(f"  - {loc}: {e.get('error')}")
        lines.append("Use the `bash` tool to run the command, then continue.")
        return "\n".join(lines)

    def _record_edit_failure(
        self, tool_id: str, params: dict[str, Any], error: str | None
    ) -> None:
        """Track failed edit attempts so the next turn can suggest a fallback."""
        ctx = self.context
        if ctx is None:
            return

        file_path = self._edit_tool_target_file(tool_id, params)
        if not file_path:
            return

        chain: dict[str, list[str]] = ctx.state.setdefault("__edit_failure_chain__", {})
        chain.setdefault(file_path, [])
        if tool_id not in chain[file_path]:
            chain[file_path].append(tool_id)

        ctx.state["__last_edit_error__"] = {
            "file": file_path,
            "tool": tool_id,
            "error": error or "unknown error",
        }

    def _clear_edit_failure(self, tool_id: str, params: dict[str, Any]) -> None:
        """Clear the failure chain for a file once an edit succeeds."""
        ctx = self.context
        if ctx is None:
            return
        file_path = self._edit_tool_target_file(tool_id, params)
        if not file_path:
            return
        chain = ctx.state.get("__edit_failure_chain__")
        if isinstance(chain, dict) and file_path in chain:
            del chain[file_path]
        last = ctx.state.get("__last_edit_error__")
        if isinstance(last, dict) and last.get("file") == file_path:
            ctx.state.pop("__last_edit_error__", None)

    def _edit_tool_target_file(self, tool_id: str, params: dict[str, Any]) -> str | None:
        """Best-effort file path extraction from edit-tool params."""
        if tool_id in ("edit_file", "semantic_edit", "write_file"):
            return str(params.get("file_path", "")) or None
        if tool_id == "apply_patch":
            dirty = self.context.scratch.get("dirty_files") if self.context else None
            if isinstance(dirty, set) and dirty:
                # Return the most recently dirtied file as the best proxy.
                return sorted(dirty)[-1]
        return None

    def _build_edit_recovery_message(self) -> str | None:
        """Suggest the next fallback when an edit tool just failed."""
        ctx = self.context
        if ctx is None:
            return None
        last = ctx.state.get("__last_edit_error__")
        if not isinstance(last, dict):
            return None

        file_path = last.get("file", "")
        tool = last.get("tool", "")
        error = last.get("error", "")
        chain = ctx.state.get("__edit_failure_chain__", {})
        attempts = chain.get(file_path, [])

        if len(attempts) >= 3:
            return (
                f"Editing {file_path} has failed with {', '.join(attempts)}. "
                "You have exhausted the automatic fallbacks. Read the full file, "
                "then use `ask_human` for guidance or `write_file` to rewrite it entirely."
            )

        if tool == "edit_file":
            return (
                f"`edit_file` failed for {file_path}: {error}\n"
                "Fallback: use `apply_patch` with a unified diff that includes more "
                "surrounding context, or use `semantic_edit` to describe the change in "
                "natural language."
            )
        if tool == "apply_patch":
            return (
                f"`apply_patch` failed for {file_path}: {error}\n"
                "Fallback: use `semantic_edit` with a precise natural-language instruction, "
                "or read the full file and use `write_file`."
            )
        if tool == "semantic_edit":
            return (
                f"`semantic_edit` failed for {file_path}: {error}\n"
                "Fallback: read the full file with `read_file`, then use `write_file` "
                "to produce the complete corrected content."
            )
        return None

    def _build_permission_required_message(self) -> str | None:
        """Return a message when a destructive bash command needs human approval."""
        ctx = self.context
        if ctx is None:
            return None
        if self._permission_mode == "auto":
            return None
        req = ctx.state.get("__bash_permission_required__")
        if not isinstance(req, dict):
            return None
        command = req.get("command", "")
        reason = req.get("reason", "")
        return (
            f"The bash command requires permission: {reason}\n"
            f"Command: `{command}`\n"
            "Use `ask_human` to request approval, or rephrase the command to avoid "
            "the destructive operation."
        )

    def _spill_tool_output_if_large(
        self, tool_id: str, content: str
    ) -> str | None:
        """Write very long tool output to disk and return the spill file path."""
        if len(content) <= _SPILL_OUTPUT_THRESHOLD:
            return None
        ctx = self.context
        if ctx is None:
            return None

        cwd = ctx.scratch.get("bash_cwd") or os.getcwd()
        out_dir = Path(cwd) / ".agent_output"
        out_dir.mkdir(parents=True, exist_ok=True)

        file_name = f"{tool_id}_{uuid4().hex[:8]}.md"
        out_path = out_dir / file_name
        try:
            out_path.write_text(content, encoding="utf-8")
        except (OSError, ValueError):
            return None

        # Remember the spill path so follow-up tools can find it.
        spills = ctx.scratch.setdefault("_spilled_outputs", [])
        if isinstance(spills, list):
            spills.append(str(out_path))
            if len(spills) > 20:
                spills.pop(0)
        return str(out_path)

    def _write_plan_file(self, plan_text: str) -> str:
        """Write the current plan to disk for human review."""
        ctx = self.context
        cwd = ctx.scratch.get("bash_cwd") or os.getcwd() if ctx else os.getcwd()
        plan_path = Path(cwd) / ".agent_plan.md"
        with contextlib.suppress(OSError, ValueError):
            plan_path.write_text(plan_text, encoding="utf-8")
        return str(plan_path)

    def _build_diagnosis_message(self, failure_count: int) -> str | None:
        """Build a structured diagnosis message after consecutive tool failures."""
        ctx = self.context
        if ctx is None:
            return None
        recent = ctx.state.get("__recent_errors__", [])[-5:]
        error_text = "\n".join(f"- {e}" for e in recent)
        return (
            f"You have had {failure_count} consecutive tool failures. Pause and diagnose:\n"
            f"{error_text}\n\n"
            "Common causes: (1) wrong file path — use glob to confirm; "
            "(2) edit_file old_string not exact/unique — include more context; "
            "(3) invalid tool parameters. Suggest a corrected approach before continuing."
        )

    def _is_readonly_tool_call(self, tool_call: Any) -> bool:
        """Return True if a tool call only reads state/files and does not mutate."""
        ctx = self.context
        if ctx is None:
            return False
        tool_id = getattr(tool_call, "name", "")
        tool = (ctx.tools or {}).get(tool_id)
        if tool is None:
            return False
        try:
            spec = tool.execution_spec()
        except Exception:
            return False
        side_effects = getattr(spec, "side_effects", "")
        writes_files = getattr(spec, "writes_files", False)
        return not writes_files and side_effects in ("none", "readonly")

    def _build_readonly_budget_message(
        self, consecutive: int, remaining_steps: int
    ) -> str | None:
        """Nudge the model out of endless read-only exploration.

        Claude Code keeps the loop tight by forcing action after a bounded
        amount of exploration. This deterministic guardrail does the same.
        """
        if consecutive <= 0 or consecutive < self._max_consecutive_readonly_steps:
            return None
        return (
            f"You have used {consecutive} consecutive exploration-only steps. "
            f"Only {remaining_steps} steps remain. "
            "Stop exploring. Choose ONE concrete action now: "
            "(1) edit/write a file, (2) run a verification command with bash, "
            "(3) call ask_human if you need clarification, or "
            "(4) call complete_task if the task is finished. "
            "Do not call another read-only tool."
        )

    def _build_step_budget_warning(self, step: int, max_steps: int) -> str | None:
        """Warn the model when it is running out of steps."""
        remaining = max_steps - step
        if remaining > self._step_budget_warning_steps:
            return None
        if remaining <= 0:
            return None
        return (
            f"Step budget warning: {remaining} step(s) remain out of {max_steps}. "
            "Finish the task or make the final edit now. No more exploration."
        )

    def _assistant_text_message(
        self,
        text: str,
        assistant_content: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if self._uses_openai_conversation():
            return {"role": "assistant", "content": text}
        if assistant_content:
            return {"role": "assistant", "content": assistant_content}
        return {"role": "assistant", "content": text}

    def _assistant_tool_call_message(
        self,
        tool_calls: list[Any],
        text_part: str,
    ) -> dict[str, Any]:
        payload_calls: list[dict[str, Any]] = []
        for call in tool_calls:
            arguments = call.arguments if isinstance(call.arguments, dict) else {}
            payload_calls.append(
                {
                    "id": call.id or f"call_{uuid4().hex[:12]}",
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(arguments, ensure_ascii=False),
                    },
                }
            )
        return {
            "role": "assistant",
            "content": text_part or None,
            "tool_calls": payload_calls,
        }

    def _tool_result_message(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        content: str,
        is_error: bool,
        as_openai_message: bool,
    ) -> dict[str, Any]:
        if as_openai_message:
            return {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": tool_name,
                "content": content,
            }
        return _tool_result_block(tool_call_id, content, is_error=is_error)


def _wrap_text(text: str) -> list[dict[str, Any]]:
    if not text:
        return []
    return [{"type": "text", "text": text}]


def _normalize_history_message(entry: dict[str, Any]) -> dict[str, Any] | None:
    role = entry.get("role")
    if role not in ("user", "assistant", "system", "tool"):
        return None
    normalized: dict[str, Any] = {"role": role}
    if "content" in entry:
        normalized["content"] = entry.get("content")
    if role == "assistant" and "tool_calls" in entry:
        normalized["tool_calls"] = entry.get("tool_calls")
    if role == "tool":
        if "tool_call_id" in entry:
            normalized["tool_call_id"] = entry.get("tool_call_id")
        if "name" in entry:
            normalized["name"] = entry.get("name")
    return normalized


def _last_message_is_user(messages: list[dict[str, Any]]) -> bool:
    for entry in reversed(messages):
        role = entry.get("role")
        if role in ("user", "assistant"):
            return role == "user"
    return False


def _tool_result_block(
    tool_use_id: str, content: str, *, is_error: bool
) -> dict[str, Any]:
    block: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
    }
    if is_error:
        block["is_error"] = True
    return block


def _format_tool_result(data: Any, *, truncate: bool = True) -> str:
    """Render a tool's return into the string we feed back as tool_result.

    Prefer the tool's own ``message`` field when present (CoreCoder tools all
    surface a human-readable message), fall back to JSON dump, finally str().
    """
    if isinstance(data, dict):
        message = data.get("message")
        if isinstance(message, str) and message.strip():
            return _truncate(message) if truncate else message
        try:
            rendered = json.dumps(data, ensure_ascii=False, default=str)
            return _truncate(rendered) if truncate else rendered
        except (TypeError, ValueError):
            return _truncate(str(data)) if truncate else str(data)
    if isinstance(data, str):
        return _truncate(data) if truncate else data
    return _truncate(str(data)) if truncate else str(data)


_SPILL_OUTPUT_THRESHOLD = 12_000
_SPILL_OUTPUT_HEAD = 1_000


def _record_recent_error(ctx: Any, tool_id: str, message: str) -> None:
    """Append a bounded list of recent tool errors to ctx.state."""
    recent = ctx.state.setdefault("__recent_errors__", [])
    recent.append(f"[{tool_id}] {message}")
    if len(recent) > 10:
        recent.pop(0)


def _truncate(text: str) -> str:
    if len(text) <= _TOOL_RESULT_CHAR_LIMIT:
        return text
    head = text[: _TOOL_RESULT_CHAR_LIMIT - 200]
    return head + "\n... (tool output truncated)"


# ---- prompt-cache boundary helpers ---------------------------------------

_SYSTEM_PROMPT_BOUNDARY = "__DYNAMIC_BOUNDARY__"


def _split_system_prompt(system_prompt: str) -> list[dict[str, Any]]:
    """Split a system prompt into cacheable blocks.

    Splits at ``__DYNAMIC_BOUNDARY__`` (static vs dynamic) and then at
    ``__CATEGORY_BOUNDARY__`` inside the dynamic section so each category
    gets its own system message. This lets the model locate information
    more easily and improves prefix caching of the static prefix.
    """
    if _SYSTEM_PROMPT_BOUNDARY not in system_prompt:
        return [{"role": "system", "content": system_prompt}]

    static_part, dynamic_part = system_prompt.split(_SYSTEM_PROMPT_BOUNDARY, 1)
    messages: list[dict[str, Any]] = []
    static = static_part.strip()
    if static:
        messages.append({"role": "system", "content": static})

    dynamic = dynamic_part.strip()
    if dynamic:
        for category in dynamic.split("__CATEGORY_BOUNDARY__"):
            category = category.strip()
            if category:
                messages.append({"role": "system", "content": category})
    return messages

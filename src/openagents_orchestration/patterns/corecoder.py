"""CoreCoderPattern — native-tool-calling ReAct loop.

Faithful port of CoreCoder's ``Agent.chat()`` into the openagents SDK's
``PatternPlugin`` shape:

- Builds an Anthropic-compatible tool schema list from the raw ToolPlugins
  registered for this agent (excluding ``sub_agent`` for the sub-agent role).
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
import json
import time
from typing import Any
from uuid import uuid4

from openagents.errors.exceptions import ModelRetryError, ToolError
from openagents.interfaces.pattern import PatternPlugin, unwrap_tool_result

from openagents_orchestration.prompts import CORE_PRINCIPLES, build_runtime_fragment, gather_runtime_context
from openagents_orchestration.utils.runtime_compat import (
    error_details_payload,
    is_retryable_llm_error,
)

try:
    from openagents.interfaces.diagnostics import LLMCallMetrics
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


_DEFAULT_MAX_STEPS = 20
_DEFAULT_MAX_TOKENS = 4096
_TOOL_RESULT_CHAR_LIMIT = 8_000


class CoreCoderPattern(PatternPlugin):
    """Native tool-calling ReAct loop with CoreCoder semantics."""

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})
        self._max_steps = int(self.config.get("max_steps", _DEFAULT_MAX_STEPS))
        self._max_tokens = int(self.config.get("max_tokens", _DEFAULT_MAX_TOKENS))
        self._temperature = self.config.get("temperature")
        self._model_override = self.config.get("model")

    def compose_system_prompt(self, base_prompt: str) -> str:
        """Inject CoreCoder principles + dynamic runtime fragment."""
        ctx = self.context
        fragments: list[str] = []
        base = (base_prompt or "").strip()
        if base:
            fragments.append(base)
        fragments.append(CORE_PRINCIPLES.strip())
        if ctx is not None:
            runtime_kwargs = gather_runtime_context(ctx)
            runtime_fragment = build_runtime_fragment(**runtime_kwargs)
            if runtime_fragment.strip():
                fragments.append(runtime_fragment.strip())
            fragments.extend(
                fragment.strip()
                for fragment in ctx.system_prompt_fragments
                if isinstance(fragment, str) and fragment.strip()
            )
        return "\n\n".join(f for f in fragments if f)

    async def execute(self) -> str:
        """Run the ReAct loop until the model emits a text-only turn."""
        ctx = self.context
        if ctx is None:
            raise RuntimeError("CoreCoderPattern.execute requires setup() first")
        if ctx.llm_client is None:
            raise RuntimeError("CoreCoderPattern needs an llm_client")

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

        system_prompt = self.compose_system_prompt("")
        final_text = ""
        consecutive_empty = 0
        max_consecutive_empty = 2
        for step in range(1, self._max_steps + 1):
            response = await self._invoke_llm(
                messages=[{"role": "system", "content": system_prompt}, *messages],
                tools=tool_schemas,
            )

            assistant_content = response.content or _wrap_text(response.output_text)
            tool_calls = response.tool_calls or []
            text_part = response.output_text or ""

            if not tool_calls:
                stripped = text_part.strip()
                if stripped:
                    # Real text-only final answer.
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
                    as_openai_messages=True,
                )
                messages.extend(tool_result_messages)
            else:
                messages.append({"role": "assistant", "content": assistant_content})
                tool_result_blocks = await self._dispatch_tool_calls(tool_calls)
                messages.append({"role": "user", "content": tool_result_blocks})
        else:  # for/else: ran out of steps
            await self.emit(
                "pattern.step_budget_exhausted",
                max_steps=self._max_steps,
            )
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
        return final_text

    def _build_tool_schemas(self) -> list[dict[str, Any]]:
        """Render tool definitions in the shape expected by the active provider."""
        ctx = self.context
        provider = getattr(getattr(ctx, "llm_client", None), "provider_name", "")
        use_openai_shape = provider.startswith("openai_compatible") or provider.startswith(
            "litellm"
        )
        schemas: list[dict[str, Any]] = []
        for tool_id, bound in (ctx.tools or {}).items():
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

    async def _dispatch_tool_calls(
        self,
        tool_calls: list[Any],
        *,
        as_openai_messages: bool = False,
    ) -> list[dict[str, Any]]:
        """Run each tool call serially and build provider-native result messages."""
        ctx = self.context
        result_payloads: list[dict[str, Any]] = []
        for call in tool_calls:
            tool_id = call.name
            params = call.arguments if isinstance(call.arguments, dict) else {}
            call_id = call.id or f"call_{uuid4().hex[:12]}"
            await self.emit("tool.called", tool_id=tool_id, params=params)

            if tool_id not in (ctx.tools or {}):
                err_msg = f"Tool '{tool_id}' is not registered."
                ctx.state["__consecutive_tool_failures__"] = ctx.state.get("__consecutive_tool_failures__", 0) + 1
                await self.emit("tool.failed", tool_id=tool_id, error=err_msg)
                result_payloads.append(
                    self._tool_result_message(
                        tool_call_id=call_id,
                        tool_name=tool_id,
                        content=err_msg,
                        is_error=True,
                        as_openai_message=as_openai_messages,
                    )
                )
                continue

            tool = ctx.tools[tool_id]
            before_calls = ctx.usage.tool_calls if ctx.usage is not None else None
            try:
                raw_result = await tool.invoke(params, ctx)
            except ModelRetryError as retry_exc:
                ctx.state["__consecutive_tool_failures__"] = ctx.state.get("__consecutive_tool_failures__", 0) + 1
                await self.emit(
                    "tool.retry_requested",
                    tool_id=tool_id,
                    error=str(retry_exc),
                )
                result_payloads.append(
                    self._tool_result_message(
                        tool_call_id=call_id,
                        tool_name=tool_id,
                        content=str(retry_exc),
                        is_error=True,
                        as_openai_message=as_openai_messages,
                    )
                )
                continue
            except ToolError as tool_exc:
                ctx.state["__consecutive_tool_failures__"] = ctx.state.get("__consecutive_tool_failures__", 0) + 1
                await self.emit(
                    "tool.failed",
                    tool_id=tool_id,
                    error=str(tool_exc),
                    error_details=error_details_payload(tool_exc),
                )
                result_payloads.append(
                    self._tool_result_message(
                        tool_call_id=call_id,
                        tool_name=tool_id,
                        content=f"Tool error: {tool_exc}",
                        is_error=True,
                        as_openai_message=as_openai_messages,
                    )
                )
                continue
            except Exception as exc:  # pragma: no cover - safety net
                ctx.state["__consecutive_tool_failures__"] = ctx.state.get("__consecutive_tool_failures__", 0) + 1
                await self.emit(
                    "tool.failed",
                    tool_id=tool_id,
                    error=str(exc),
                    error_details=error_details_payload(exc),
                )
                result_payloads.append(
                    self._tool_result_message(
                        tool_call_id=call_id,
                        tool_name=tool_id,
                        content=f"Unexpected tool failure: {exc}",
                        is_error=True,
                        as_openai_message=as_openai_messages,
                    )
                )
                continue

            data, executor_meta = unwrap_tool_result(raw_result)
            # Reset tool failure counter on success
            ctx.state["__consecutive_tool_failures__"] = 0
            ctx.tool_results.append({"tool_id": tool_id, "result": data})
            if (
                ctx.usage is not None
                and before_calls is not None
                and ctx.usage.tool_calls == before_calls
            ):
                ctx.usage.tool_calls += 1
            await self.emit(
                "tool.succeeded",
                tool_id=tool_id,
                result=data,
                executor_metadata=executor_meta,
            )

            payload = _format_tool_result(data)
            result_payloads.append(
                self._tool_result_message(
                    tool_call_id=call_id,
                    tool_name=tool_id,
                    content=payload,
                    is_error=False,
                    as_openai_message=as_openai_messages,
                )
            )
        return result_payloads

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


def _format_tool_result(data: Any) -> str:
    """Render a tool's return into the string we feed back as tool_result.

    Prefer the tool's own ``message`` field when present (CoreCoder tools all
    surface a human-readable message), fall back to JSON dump, finally str().
    """
    if isinstance(data, dict):
        message = data.get("message")
        if isinstance(message, str) and message.strip():
            return _truncate(message)
        try:
            return _truncate(json.dumps(data, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            return _truncate(str(data))
    if isinstance(data, str):
        return _truncate(data)
    return _truncate(str(data))


def _truncate(text: str) -> str:
    if len(text) <= _TOOL_RESULT_CHAR_LIMIT:
        return text
    head = text[: _TOOL_RESULT_CHAR_LIMIT - 200]
    return head + "\n... (tool output truncated)"

"""Structured LLM generation — single-call, schema-validated, auto-retry.

Inspired by Instructor and PydanticAI:
- Schema is automatically injected into the system prompt.
- Parse failures trigger retry with the error message fed back to the LLM.
- Pydantic models provide type-safe output.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


def _inject_schema(messages: list[dict], schema: dict) -> list[dict]:
    """Inject JSON schema into the system prompt."""
    schema_text = json.dumps(schema, ensure_ascii=False, indent=2)
    instruction = (
        "\n\nYou must respond with a JSON object that strictly follows this schema:\n"
        f"```json\n{schema_text}\n```\n"
        "Output ONLY the JSON object, no markdown fences, no extra text."
    )

    new_messages: list[dict] = []
    system_injected = False
    for msg in messages:
        if msg.get("role") == "system":
            new_messages.append({**msg, "content": msg.get("content", "") + instruction})
            system_injected = True
        else:
            new_messages.append(dict(msg))

    if not system_injected:
        new_messages.insert(0, {
            "role": "system",
            "content": f"Respond with JSON following this schema:\n```json\n{schema_text}\n```",
        })

    return new_messages


def _strip_markdown(text: str) -> str:
    """Remove markdown code fences if present."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


async def structured_generate(
    messages: list[dict[str, Any]],
    response_model: type[T],
    llm_client: Any,
    *,
    max_retries: int = 2,
    temperature: float = 0.0,
    max_tokens: int = 512,
) -> tuple[T, Any]:
    """Generate a structured response from an LLM.

    Args:
        messages: Conversation messages (system + user).
        response_model: Pydantic model defining the expected output shape.
        llm_client: LLM client with a ``generate()`` method.
        max_retries: Number of retry attempts on validation failure.
        temperature: LLM temperature.
        max_tokens: Max output tokens.

    Returns:
        (parsed_model, usage_object)

    Raises:
        ValidationError: If all retry attempts fail to produce valid JSON.
    """
    schema = response_model.model_json_schema()
    messages = _inject_schema(messages, schema)

    async def _generate_with_retry(current_messages: list[dict[str, Any]]) -> Any:
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                return await llm_client.generate(
                    messages=current_messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    tools=None,
                )
            except Exception as exc:
                last_exc = exc
                if attempt >= max_retries:
                    raise
                await asyncio.sleep(0.5 * (attempt + 1))
        raise last_exc  # type: ignore[misc]

    response = await _generate_with_retry(messages)

    last_error: ValidationError | None = None

    for attempt in range(max_retries + 1):
        text = _strip_markdown(response.output_text or "")

        try:
            parsed = response_model.model_validate_json(text)
            return parsed, response.usage
        except ValidationError as exc:
            last_error = exc
            if attempt < max_retries:
                retry_messages = messages + [
                    {"role": "assistant", "content": response.output_text or ""},
                    {
                        "role": "user",
                        "content": (
                            f"Your response does not match the required schema. "
                            f"Errors: {exc}\n\n"
                            f"Please output ONLY a valid JSON object matching the schema. "
                            f"No markdown, no extra text."
                        ),
                    },
                ]
                response = await _generate_with_retry(retry_messages)

    raise last_error  # type: ignore[return-value]

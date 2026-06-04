"""semantic_edit — natural-language-driven file editing via LLM.

The agent describes what to change (e.g. "add error handling to the foo function")
and the tool uses the agent's own LLM to generate the modified file content.
More forgiving than edit_file when the agent struggles with exact string matching.
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from openagents.errors.exceptions import ModelRetryError, ToolError
from openagents.interfaces.run_context import RunContext
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin
from pydantic import BaseModel

_MAX_DIFF_CHARS = 3000


class _EditResult(BaseModel):
    """LLM output schema for semantic edit."""

    modified_content: str
    explanation: str


class SemanticEditTool(ToolPlugin):
    """Edit a file by describing the change in natural language."""

    name = "semantic_edit"
    description = (
        "Edit a file using a natural-language instruction. "
        "The LLM reads the file, applies the described change, and writes back the result. "
        "Use this when edit_file fails due to exact-match issues, or when the desired change "
        "is complex (e.g. 'refactor this function to use async/await', 'add try/except around all DB calls'). "
        "WARNING: This consumes extra LLM tokens — prefer edit_file for simple, well-defined changes."
    )
    durable_idempotent = False

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(
            concurrency_safe=False,
            side_effects="writes_filesystem",
            reads_files=True,
            writes_files=True,
            default_timeout_ms=60_000,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "File to edit.",
                },
                "instruction": {
                    "type": "string",
                    "description": (
                        "Natural-language description of the change. Be specific: "
                        "mention function names, line ranges, or patterns to match."
                    ),
                },
            },
            "required": ["file_path", "instruction"],
        }

    async def invoke(
        self, params: dict[str, Any], context: RunContext[Any] | None
    ) -> dict[str, Any]:
        file_path = str(params.get("file_path", "")).strip()
        instruction = str(params.get("instruction", "")).strip()

        if not file_path:
            raise ToolError("file_path is required", tool_name=self.name)
        if not instruction:
            raise ToolError("instruction is required", tool_name=self.name)

        path = Path(file_path).expanduser()
        if not path.exists():
            raise ToolError(f"File not found: {file_path}", tool_name=self.name)
        if not path.is_file():
            raise ToolError(f"Not a regular file: {file_path}", tool_name=self.name)

        try:
            original = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError(
                f"File is not UTF-8 text: {file_path} ({exc.reason})",
                tool_name=self.name,
            ) from exc

        # Get LLM client from context
        llm_client = getattr(context, "llm_client", None) if context else None
        if llm_client is None:
            raise ModelRetryError(
                "No LLM client available for semantic_edit. "
                "This tool requires an LLM to generate the modified content."
            )

        # Build prompt
        system_prompt = (
            "You are a precise code editor. You will be given a file's content and an instruction. "
            "Your task is to apply the instruction faithfully and output the COMPLETE modified file content. "
            "Output ONLY the modified file content inside a markdown code block (```). "
            "Do NOT output partial changes, diffs, or explanations. "
            "Preserve all unrelated code exactly as-is."
        )

        user_prompt = (
            f"File: {file_path}\n\n"
            "=== ORIGINAL CONTENT ===\n"
            f"{original}\n"
            "=== END CONTENT ===\n\n"
            f"Instruction: {instruction}\n\n"
            "Apply the instruction to the file and output the COMPLETE modified file content "
            "inside a markdown code block (```language ... ```)."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        # Generate modified content
        try:
            response = await llm_client.generate(
                messages=messages,
                temperature=0.1,
                max_tokens=4096,
                tools=None,
            )
        except Exception as exc:
            raise ModelRetryError(
                f"LLM call failed for semantic_edit: {exc}"
            ) from exc

        raw_text = (response.output_text or "").strip()
        if not raw_text:
            raise ModelRetryError(
                "LLM returned empty response for semantic_edit. Please retry."
            )

        # Extract modified content from markdown code block
        modified_content = _extract_code_block(raw_text)
        if modified_content is None:
            # Fallback: treat the entire response as the file content
            # (Some LLMs may output raw text without fences)
            modified_content = raw_text
            if not modified_content.strip():
                raise ModelRetryError(
                    "Could not parse LLM response. Expected markdown code block with modified content. "
                    f"Raw response (first 500 chars): {raw_text[:500]}"
                )

        # Validate: content should not be empty
        if not modified_content.strip():
            raise ModelRetryError(
                "LLM returned empty modified_content. Please retry with a more specific instruction."
            )

        # If no change was made, warn but don't fail
        # Use rstrip() comparison to ignore trailing newline differences
        # (LLM code blocks typically don't preserve exact trailing newline)
        if modified_content.rstrip() == original.rstrip():
            return {
                "file_path": str(path),
                "changed": False,
                "message": "No changes were made — the LLM returned the same content.",
            }

        # Write back
        path.write_text(modified_content, encoding="utf-8")

        # Track dirty files
        if context is not None:
            dirty = context.scratch.setdefault("dirty_files", set())
            if isinstance(dirty, set):
                dirty.add(str(path.resolve(strict=False)))

        # Generate diff
        diff = _unified_diff(original, modified_content, str(path))

        return {
            "file_path": str(path),
            "changed": True,
            "diff": diff,
            "message": f"Edited {file_path}\n{diff}",
        }


def _extract_code_block(text: str) -> str | None:
    """Extract content from a markdown code block if present."""
    lines = text.splitlines()
    in_block = False
    block_lines: list[str] = []
    for line in lines:
        if line.strip().startswith("```"):
            if in_block:
                # End of block
                return "\n".join(block_lines)
            else:
                in_block = True
                continue
        if in_block:
            block_lines.append(line)
    if in_block and block_lines:
        return "\n".join(block_lines)
    return None


def _unified_diff(old: str, new: str, filename: str, *, context: int = 3) -> str:
    diff = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        n=context,
    )
    rendered = "".join(diff)
    if len(rendered) > _MAX_DIFF_CHARS:
        rendered = rendered[: _MAX_DIFF_CHARS - 200] + "\n... (diff truncated)\n"
    return rendered

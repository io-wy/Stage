"""Custom tools for the CoreCoder example.

Each module exports a single ToolPlugin subclass implementing one tool from the
CoreCoder distillation. Wired into the runtime via ``agent.json`` ``tools[].impl``.
"""

from openagents_orchestration.tools.corecoder.run_claude_code import RunClaudeCodeTool
from openagents_orchestration.tools.corecoder.semantic_edit import SemanticEditTool

__all__ = ["RunClaudeCodeTool", "SemanticEditTool"]

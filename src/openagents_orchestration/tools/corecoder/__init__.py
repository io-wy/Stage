"""Custom tools for the CoreCoder example.

Each module exports a single ToolPlugin subclass implementing one tool from the
CoreCoder distillation. Wired into the runtime via ``agent.json`` ``tools[].impl``.
"""

from openagents_orchestration.tools.corecoder.ask_human import CoreCoderAskHumanTool
from openagents_orchestration.tools.corecoder.complete_task import CompleteTaskTool
from openagents_orchestration.tools.corecoder.list_directory import ListDirectoryTool
from openagents_orchestration.tools.corecoder.run_claude_code import RunClaudeCodeTool
from openagents_orchestration.tools.corecoder.semantic_edit import SemanticEditTool
from openagents_orchestration.tools.corecoder.sub_agent import SubAgentTool
from openagents_orchestration.tools.corecoder.think import ThinkTool
from openagents_orchestration.tools.corecoder.web_fetch import WebFetchTool

__all__ = [
    "CoreCoderAskHumanTool",
    "CompleteTaskTool",
    "ListDirectoryTool",
    "RunClaudeCodeTool",
    "SemanticEditTool",
    "SubAgentTool",
    "ThinkTool",
    "WebFetchTool",
]

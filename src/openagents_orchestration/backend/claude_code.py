"""Claude Code backend adapters exposed under the backend namespace."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openagents_orchestration.control.audit import AuditStore
from openagents_orchestration.control.models import (
    CaseAuditEvent,
    ToolInvocationRecord,
)
from openagents_orchestration.control.router import GovernancePlan
from openagents_orchestration.control.safety import scan_public_output

_DEFAULT_ALLOWED_TOOLS = ("Read", "Edit", "Bash", "Glob", "Grep")
_DEFAULT_TIMEOUT_SECONDS = 300
_FORBIDDEN_PATTERNS = (
    "sastsast",
    "sast_forever",
    "saster",
    "shared account",
    "credential",
)


ClaudeCodeRunner = Callable[..., dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ClaudeCodeImport:
    tool_invocation: ToolInvocationRecord
    public_case_result: dict[str, Any]
    raw_case_result_path: str
    timing: dict[str, Any] = field(default_factory=dict)
    grading: dict[str, Any] = field(default_factory=dict)


class ClaudeCodeBackend:
    """Governed backend that invokes local Claude Code for workspace tasks."""

    execution_mode = "claude_code_governed"

    def __init__(
        self,
        *,
        workspace_path: str | Path | None = None,
        allowed_tools: tuple[str, ...] | list[str] | None = None,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
        skip_permissions: bool = False,
        safe_mode: bool = True,
        runner: ClaudeCodeRunner | None = None,
    ):
        self.workspace_path = Path(workspace_path or Path.cwd())
        self.allowed_tools = tuple(allowed_tools or _DEFAULT_ALLOWED_TOOLS)
        self.timeout_seconds = timeout_seconds
        self.skip_permissions = skip_permissions
        self.safe_mode = safe_mode
        self._runner = runner or _run_claude_code

    def run(
        self,
        *,
        case_id: str,
        run_id: str,
        prompt: str,
        route_plan: GovernancePlan,
        audit_store: AuditStore,
    ) -> dict[str, Any]:
        instruction = _governed_instruction(prompt, route_plan)
        audit_store.append(
            CaseAuditEvent(
                case_id=case_id,
                run_id=run_id,
                event_type="tool_invoked",
                payload={
                    "backend": "claude_code",
                    "execution_mode": self.execution_mode,
                    "route": route_plan.to_dict(),
                    "workspace_path": str(self.workspace_path),
                    "allowed_tools": list(self.allowed_tools),
                    "skip_permissions": self.skip_permissions,
                    "safe_mode": self.safe_mode,
                },
            )
        )
        result = self._runner(
            instruction=instruction,
            workspace_path=self.workspace_path,
            allowed_tools=self.allowed_tools,
            timeout_seconds=self.timeout_seconds,
            skip_permissions=self.skip_permissions,
            safe_mode=self.safe_mode,
        )
        exit_code = int(result.get("exit_code", 1))
        output = str(result.get("output", "")).strip()
        command = str(result.get("command", "claude -p")).strip()
        succeeded = exit_code == 0
        summary = output or f"claude_code exited with code {exit_code}"
        failure_mode = _classify_failure(exit_code=exit_code, output=summary)
        action_result = {
            "action_id": route_plan.action_plan.action_id
            if route_plan.action_plan is not None
            else "",
            "executor": "claude_code",
            "executed": succeeded,
            "actions_taken": ["run_claude_code"],
            "side_effects": ["workspace_write"],
            "external_refs": {"workspace": str(self.workspace_path)},
            "verification_claims": [f"claude_code_exit_code={exit_code}"],
            "errors": [] if succeeded else [summary],
            "metadata": {
                "command": command,
                "exit_code": exit_code,
                "allowed_tools": list(self.allowed_tools),
                "safe_mode": self.safe_mode,
                "failure_mode": failure_mode,
            },
        }
        return {
            "backend": "claude_code",
            "family": "development",
            "closed": succeeded,
            "answer": summary,
            "actions": ["propose_patch", "run_verification"]
            if succeeded
            else ["create_handoff"],
            "evidence": [
                {
                    "source_ref": "claude_code_output",
                    "summary": f"{prompt}\n{summary}",
                    "rank": 1,
                    "score": 1.0 if succeeded else 0.0,
                    "tags": ["runtime", "perm:internal"],
                }
            ],
            "action_result": action_result,
            "human_questions": []
            if succeeded
            else [_handoff_question(failure_mode)],
            "failure_mode": failure_mode,
            "confidence": route_plan.confidence,
            "metadata": {
                "workspace_path": str(self.workspace_path),
                "command": command,
                "exit_code": exit_code,
                "safe_mode": self.safe_mode,
            },
        }


class ClaudeCodeAdapter:
    """Import-first adapter for Claude Code baseline artifacts."""

    def __init__(self, audit_store: AuditStore | None = None):
        self._audit_store = audit_store

    def import_run(
        self,
        *,
        case_id: str,
        run_id: str,
        artifact_dir: str | Path,
    ) -> ClaudeCodeImport:
        base = Path(artifact_dir)
        case_result_path = self._find_required(base, "case_result.json")
        timing_path = self._find_optional(base, "timing.json")
        grading_path = self._find_optional(base, "grading.json")

        raw_case_result = json.loads(case_result_path.read_text(encoding="utf-8"))
        public_case_result = self._sanitize_value(raw_case_result)
        timing = self._read_json(timing_path)
        grading = self._read_json(grading_path)

        tool_invocation = ToolInvocationRecord(
            case_id=case_id,
            run_id=run_id,
            backend="claude_code",
            input_ref=str(base),
            output_ref=str(case_result_path),
            duration_seconds=timing.get("total_duration_seconds"),
            token_cost=timing.get("total_tokens"),
            status="imported",
            metadata={
                "grading_summary": grading.get("summary", {}),
                "artifact_dir": str(base),
            },
        )

        if self._audit_store is not None:
            self._audit_store.append(
                CaseAuditEvent(
                    case_id=case_id,
                    run_id=run_id,
                    event_type="tool_invoked",
                    payload={
                        "backend": "claude_code",
                        "output_ref": str(case_result_path),
                        "duration_seconds": timing.get("total_duration_seconds"),
                    },
                )
            )

        return ClaudeCodeImport(
            tool_invocation=tool_invocation,
            public_case_result=public_case_result,
            raw_case_result_path=str(case_result_path),
            timing=timing,
            grading=grading,
        )

    @staticmethod
    def _read_json(path: Path | None) -> dict[str, Any]:
        if path is None or not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _find_required(base: Path, filename: str) -> Path:
        for path in base.rglob(filename):
            return path
        raise FileNotFoundError(f"{filename} not found under {base}")

    @staticmethod
    def _find_optional(base: Path, filename: str) -> Path | None:
        for path in base.rglob(filename):
            return path
        return None

    def _sanitize_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self._sanitize_value(child) for key, child in value.items()}
        if isinstance(value, list):
            return [self._sanitize_value(child) for child in value]
        if isinstance(value, str):
            sanitized = value
            for pattern in _FORBIDDEN_PATTERNS:
                sanitized = re.sub(
                    re.escape(pattern),
                    "[redacted]",
                    sanitized,
                    flags=re.IGNORECASE,
                )
            if scan_public_output(sanitized, forbidden_patterns=_FORBIDDEN_PATTERNS).blocked:
                return "[redacted]"
            return sanitized
        return value


def _governed_instruction(prompt: str, route_plan: GovernancePlan) -> str:
    action_plan = route_plan.action_plan.model_dump() if route_plan.action_plan else {}
    return "\n".join(
        [
            "Stage governed code task.",
            "Follow the action boundary exactly.",
            f"Prompt: {prompt}",
            f"Route: {json.dumps(route_plan.to_dict(), ensure_ascii=False)}",
            f"Action plan: {json.dumps(action_plan, ensure_ascii=False)}",
            "Do not commit, deploy, push, or touch external services.",
            "After edits, report changed files and verification results.",
        ]
    )


def _run_claude_code(
    *,
    instruction: str,
    workspace_path: Path,
    allowed_tools: tuple[str, ...],
    timeout_seconds: int,
    skip_permissions: bool,
    safe_mode: bool = True,
) -> dict[str, Any]:
    claude_path = shutil.which("claude")
    if claude_path is None:
        return {
            "exit_code": 127,
            "output": "claude CLI not found in PATH",
            "command": "claude -p",
        }
    cmd = [claude_path]
    if safe_mode:
        cmd.append("--safe-mode")
    if skip_permissions:
        cmd.extend(["--permission-mode", "bypassPermissions"])
    if allowed_tools:
        cmd.extend(["--allowedTools", ",".join(allowed_tools)])
    cmd.extend(["--output-format", "json", "--no-session-persistence", "-p", instruction])
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=str(workspace_path),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "exit_code": 124,
            "output": _timeout_output(exc),
            "command": _display_command(cmd),
        }
    output = result.stdout
    if result.stderr:
        output = f"{output}\n[stderr]\n{result.stderr}"
    return {
        "exit_code": result.returncode,
        "output": _truncate_output(output),
        "command": _display_command(cmd),
    }


def _display_command(cmd: list[str]) -> str:
    visible = ["<prompt>" if part.startswith("Stage governed code task.") else part for part in cmd]
    return " ".join(visible)


def _classify_failure(*, exit_code: int, output: str) -> str | None:
    if exit_code == 0:
        return None
    lowered = output.lower()
    if exit_code == 124 or "timed out after" in lowered:
        return "execution_timeout"
    if "error_max_budget_usd" in lowered or "maximum budget" in lowered:
        return "over_budget"
    if any(
        marker in lowered
        for marker in (
            "not signed in",
            "auth",
            "oauth",
            "api key",
            "network",
            "enotfound",
            "econn",
            "duration_api_ms\":0",
        )
    ):
        return "auth_or_network_failure"
    return "tool_unavailable"


def _handoff_question(failure_mode: str | None) -> str:
    if failure_mode == "execution_timeout":
        return "Claude Code 执行超时，请确认任务范围、超时配置或改由人工接管。"
    if failure_mode == "over_budget":
        return "Claude Code 执行超过预算，请调整预算或拆分任务后重试。"
    if failure_mode == "auth_or_network_failure":
        return "Claude Code 认证或网络不可用，请确认运行环境后重试。"
    return "Claude Code 执行失败，请确认工具可用性或改由人工处理。"


def _timeout_output(exc: subprocess.TimeoutExpired) -> str:
    stdout = _decode_stream(exc.stdout)
    stderr = _decode_stream(exc.stderr)
    return _truncate_output(
        f"claude-code timed out after {exc.timeout}s.\nstdout: {stdout}\nstderr: {stderr}"
    )


def _decode_stream(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _truncate_output(output: str, max_output: int = 8_000) -> str:
    if len(output) <= max_output:
        return output
    return output[: max_output - 200] + "\n... (output truncated)"


__all__ = [
    "ClaudeCodeAdapter",
    "ClaudeCodeBackend",
    "ClaudeCodeImport",
]

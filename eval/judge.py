"""Agent-as-Judge — 用 Claude Code CLI 评估戏台执行质量.

调用 `claude -p --output-format json --json-schema ...` 做非交互式结构化评估.
Judge 通过 --add-dir 访问 work_dir，自行用 Read 工具读取需要评估的文件.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from openagents.llm.registry import create_llm_client
from openagents_orchestration.utils.structured_generate import structured_generate

# ---------------------------------------------------------------------------
# JSON Schema for the combined 4-dimension judgment
# ---------------------------------------------------------------------------

_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "fulfillment": {
            "type": "object",
            "properties": {
                "score": {"type": "number", "minimum": 0, "maximum": 1},
                "reasoning": {"type": "string"},
            },
            "required": ["score", "reasoning"],
        },
        "decomposition": {
            "type": "object",
            "properties": {
                "score": {"type": "number", "minimum": 0, "maximum": 1},
                "reasoning": {"type": "string"},
            },
            "required": ["score", "reasoning"],
        },
        "collaboration": {
            "type": "object",
            "properties": {
                "score": {"type": "number", "minimum": 0, "maximum": 1},
                "feedback_quality": {"type": "number", "minimum": 0, "maximum": 1},
                "reasoning": {"type": "string"},
            },
            "required": ["score", "feedback_quality", "reasoning"],
        },
        "output_quality": {
            "type": "object",
            "properties": {
                "correctness": {"type": "number", "minimum": 1, "maximum": 5},
                "readability": {"type": "number", "minimum": 1, "maximum": 5},
                "completeness": {"type": "number", "minimum": 1, "maximum": 5},
                "efficiency": {"type": "number", "minimum": 1, "maximum": 5},
                "reasoning": {"type": "string"},
            },
            "required": [
                "correctness",
                "readability",
                "completeness",
                "efficiency",
                "reasoning",
            ],
        },
    },
    "required": ["fulfillment", "decomposition", "collaboration", "output_quality"],
}


class _ScoreReason(BaseModel):
    score: float = Field(ge=0, le=1)
    reasoning: str


class _CollaborationScore(BaseModel):
    score: float = Field(ge=0, le=1)
    feedback_quality: float = Field(ge=0, le=1)
    reasoning: str


class _OutputQualityScore(BaseModel):
    correctness: float = Field(ge=1, le=5)
    readability: float = Field(ge=1, le=5)
    completeness: float = Field(ge=1, le=5)
    efficiency: float = Field(ge=1, le=5)
    reasoning: str


class _JudgeOutput(BaseModel):
    fulfillment: _ScoreReason
    decomposition: _ScoreReason
    collaboration: _CollaborationScore
    output_quality: _OutputQualityScore


class ClaudeCodeJudge:
    """用 Claude Code CLI 作为 Judge.

    Usage:
        judge = ClaudeCodeJudge()
        result = await judge.evaluate(task, state_board, work_dir)
    """

    def __init__(self, timeout_sec: int = 180):
        self.timeout_sec = timeout_sec

    # -- public API --------------------------------------------------------

    async def evaluate(
        self,
        task_description: str,
        state_board: Any,
        work_dir: Path,
        verify_scores: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """调用 Claude Code CLI 一次性评估 4 个主观维度.

        Returns dict with keys:
            - fulfillment_score (float)
            - decomposition_score (float)
            - collaboration_score (float)
            - collaboration_feedback_quality (float)
            - output_quality_score (float)   # 1-5 rubric 均值 ÷ 5 → 0-1
            - output_quality_rubric (dict)
            - reasoning_map (dict[str, str])
            - cost_usd (float | None)
            - error (str | None)
        """
        prompt = self._build_prompt(
            task_description=task_description,
            state_board=state_board,
            work_dir=work_dir,
            verify_scores=verify_scores or {},
        )

        state_file = work_dir / ".judge_state.txt"
        try:
            raw_result = await self._call_claude(prompt, work_dir)
        finally:
            # Clean up temp state file to prevent cross-task leakage
            with contextlib.suppress(Exception):
                state_file.unlink(missing_ok=True)

        if raw_result.get("error"):
            return {
                "fulfillment_score": 0.0,
                "decomposition_score": 0.0,
                "collaboration_score": 0.0,
                "collaboration_feedback_quality": 0.0,
                "output_quality_score": 0.0,
                "output_quality_rubric": {},
                "reasoning_map": {},
                "cost_usd": raw_result.get("cost_usd"),
                "error": raw_result["error"],
            }

        data = raw_result["data"]

        # 1-5 rubric 均值 → 0-1
        oq = data.get("output_quality", {})
        rubric_mean = (
            (
                oq.get("correctness", 1)
                + oq.get("readability", 1)
                + oq.get("completeness", 1)
                + oq.get("efficiency", 1)
            )
            / 4.0
            / 5.0
        )

        return {
            "fulfillment_score": data.get("fulfillment", {}).get("score", 0.0),
            "decomposition_score": data.get("decomposition", {}).get("score", 0.0),
            "collaboration_score": data.get("collaboration", {}).get("score", 0.0),
            "collaboration_feedback_quality": data.get("collaboration", {}).get(
                "feedback_quality", 0.0
            ),
            "output_quality_score": rubric_mean,
            "output_quality_rubric": {
                "correctness": oq.get("correctness", 1),
                "readability": oq.get("readability", 1),
                "completeness": oq.get("completeness", 1),
                "efficiency": oq.get("efficiency", 1),
            },
            "reasoning_map": {
                "fulfillment": data.get("fulfillment", {}).get("reasoning", ""),
                "decomposition": data.get("decomposition", {}).get("reasoning", ""),
                "collaboration": data.get("collaboration", {}).get("reasoning", ""),
                "output_quality": oq.get("reasoning", ""),
            },
            "cost_usd": raw_result.get("cost_usd"),
            "error": None,
        }

    # -- prompt builder ----------------------------------------------------

    def _build_prompt(
        self,
        task_description: str,
        state_board: Any,
        work_dir: Path,
        verify_scores: dict[str, float],
    ) -> str:
        """构造给 Claude Code CLI 的 judge prompt.

        为避免命令行参数过长，将完整的 StateBoard 摘要写入 work_dir 下的
        临时文件，prompt 中仅保留精简概览和文件引用。
        """

        # 文件列表 + 内嵌关键证据，避免 Claude Code Judge 卡在工具探索。
        file_list = self._list_work_dir_files(work_dir)
        evidence = self._build_file_evidence(work_dir)

        # StateBoard 摘要 —— 写入临时文件避免 CLI 参数过长
        board_summary = self._build_board_summary(state_board)
        state_file = work_dir / ".judge_state.txt"
        with contextlib.suppress(Exception):
            state_file.write_text(board_summary, encoding="utf-8")

        # Keep the prompt compact; full summaries make Claude Code Judge spend
        # minutes exploring instead of scoring. The file remains available for
        # manual debugging, but the judge prompt should be self-contained.
        board_preview = board_summary[:900]
        if len(board_summary) > 900:
            board_preview += "\n... (truncated; score from this summary unless essential)"

        verify_summary = (
            json.dumps(verify_scores, indent=2) if verify_scores else "(无验证规则)"
        )

        return (
            "# 评估任务\n"
            "你是独立的评估专家。请基于提供的证据，对以下戏台（多 Agent 编排系统）"
            "的执行质量进行严格评估。不要给同情分。\n\n"
            "## 原始任务描述\n"
            f"{task_description}\n\n"
            "## 验证规则结果\n"
            f"{verify_summary}\n\n"
            "## 产出文件列表\n"
            "以下是最多 12 个关键文件（内容摘要已内嵌，禁止再读文件）。\n"
            f"{file_list}\n\n"
            "## 关键文件内容摘录\n"
            f"{evidence}\n\n"
            "## 戏台执行摘要\n"
            f"{board_preview}\n\n"
            "**注意**：不要调用 Read/Bash/Grep/Glob 等工具；直接基于上方摘要和内嵌文件摘录评分。\n\n"
            "## 评估维度\n"
            "请对以下 4 个维度分别评分，输出严格遵循 JSON Schema。\n\n"
            "### 1. fulfillment（功能满足度）\n"
            "验证规则通过不代表功能真正正确。请读取关键代码文件，判断：\n"
            "- 功能是否真正满足任务描述的需求？\n"
            "- 有无逻辑错误、边界条件遗漏、安全隐患？\n"
            "- 0-1 分，1.0 = 完美，0.0 = 完全无关\n\n"
            "### 2. decomposition（编排分解质量）\n"
            "基于执行摘要中的任务图，判断：\n"
            "- 任务拆分粒度是否合适（每个任务 3-30 步可完成）？\n"
            "- 依赖关系是否合理、无冗余串行？\n"
            "- agent_type 分配是否与任务性质匹配？\n"
            "- 0-1 分\n\n"
            "### 3. collaboration（协作质量）\n"
            "基于执行摘要，判断：\n"
            "- coder 和 reviewer 之间是否有有效的反馈闭环？\n"
            "- reviewer 的反馈是否精准、可执行？\n"
            "- 如果没有协作（单 agent 完成），整体 score 取 0.8（不应惩罚无必要的协作），"
            "feedback_quality 取 0.8。\n"
            "- score 和 feedback_quality 均为 0-1 分\n\n"
            "### 4. output_quality（产出质量 rubric）\n"
            "请读取代码文件，对以下 4 维度各给 1-5 的整数评分：\n"
            "- correctness (正确性): 逻辑是否正确\n"
            "- readability (可读性): 命名、结构、注释\n"
            "- completeness (完整性): 边界条件、异常处理\n"
            "- efficiency (效率): 是否过度工程或过于简陋\n"
            "5 = 范例级，4 = 良好，3 = 合格，2 = 较差，1 = 不合格\n\n"
            "## 输出格式\n"
            "严格输出 JSON，不要 markdown 代码块，不要额外文本。不要使用任何工具。"
        )

    # -- Claude CLI caller -------------------------------------------------

    async def _call_claude(self, prompt: str, work_dir: Path) -> dict[str, Any]:
        """Call the configured project LLM directly for structured judgment.

        Older versions used `claude -p`, but Claude Code CLI brings agent
        tools/hooks/session context that can hang or contaminate scoring.  The
        judge only needs one structured LLM call over already-inlined evidence,
        so use the same project LLM client as the orchestrator.
        """
        try:
            from openagents.config.loader import load_config

            env_path = Path(".env")
            if env_path.exists():
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        os.environ.setdefault(key.strip(), value.strip())

            config_path = Path(os.environ.get("XITAI_JUDGE_CONFIG", "agent.json"))
            config = load_config(config_path)
            director = next((agent for agent in config.agents if agent.id == "director"), config.agents[0])
            llm = create_llm_client(director.llm)
            parsed, usage = await asyncio.wait_for(
                structured_generate(
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are a strict software-evaluation judge. "
                                "Use only the evidence in the user prompt. "
                                "Do not assume files or facts not shown."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    response_model=_JudgeOutput,
                    llm_client=llm,
                    temperature=0.0,
                    max_tokens=2048,
                    max_retries=1,
                ),
                timeout=self.timeout_sec,
            )
            return {
                "error": None,
                "cost_usd": None,
                "data": parsed.model_dump(),
                "usage": usage,
            }
        except TimeoutError:
            return {
                "error": f"LLM Judge timed out after {self.timeout_sec}s",
                "cost_usd": None,
                "data": {},
            }
        except Exception as exc:
            return {
                "error": f"Failed to run LLM Judge: {exc}",
                "cost_usd": None,
                "data": {},
            }

    @staticmethod
    def _validate_judge_output(data: dict[str, Any]) -> str | None:
        """手动校验 judge 输出是否符合 _JUDGE_SCHEMA. 返回错误信息或 None."""
        for dim in ("fulfillment", "decomposition", "collaboration", "output_quality"):
            if dim not in data:
                return f"missing dimension: {dim}"

        for dim in ("fulfillment", "decomposition", "collaboration"):
            score = data.get(dim, {}).get("score")
            if score is None or not isinstance(score, (int, float)):
                return f"{dim}.score is not a number: {score}"
            if not (0 <= score <= 1):
                return f"{dim}.score out of range [0,1]: {score}"

        collab = data.get("collaboration", {})
        fq = collab.get("feedback_quality")
        if fq is None or not isinstance(fq, (int, float)):
            return f"collaboration.feedback_quality is not a number: {fq}"
        if not (0 <= fq <= 1):
            return f"collaboration.feedback_quality out of range [0,1]: {fq}"

        oq = data.get("output_quality", {})
        for k in ("correctness", "readability", "completeness", "efficiency"):
            v = oq.get(k)
            if v is None or not isinstance(v, (int, float)):
                return f"output_quality.{k} is not a number: {v}"
            if not (1 <= v <= 5):
                return f"output_quality.{k} out of range [1,5]: {v}"

        return None

    # -- context builders --------------------------------------------------

    @staticmethod
    def _list_work_dir_files(work_dir: Path) -> str:
        """生成工作目录中的可读文件列表（供 judge 自行读取）."""
        if not work_dir.exists():
            return "(work_dir does not exist)"

        skip_suffixes = {
            ".pyc",
            ".pyo",
            ".so",
            ".dll",
            ".exe",
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".svg",
            ".zip",
            ".tar",
            ".gz",
            ".bz2",
            ".7z",
            ".git",
            ".DS_Store",
        }
        skip_dirs = {
            ".git",
            "__pycache__",
            ".pytest_cache",
            ".eval_cache",
            ".artifacts",
        }

        candidates: list[tuple[int, str]] = []
        priority_suffixes = (".py", ".toml", ".json", ".yaml", ".yml")
        priority_names = ("test", "tests", "cli", "main", "app", "api", "service", "model", "schema")

        for path in sorted(work_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(work_dir)
            rel_str = str(rel)
            if any(part in skip_dirs for part in rel.parts):
                continue
            if any(rel_str.endswith(suffix) for suffix in skip_suffixes):
                continue
            if rel_str.startswith(".agent_memory/") or rel.name == ".judge_state.txt":
                continue
            if not rel_str.endswith(priority_suffixes):
                continue
            try:
                size = path.stat().st_size
            except Exception:
                continue
            if size > 20_000:
                continue
            score = 0
            lowered = rel_str.lower()
            if lowered.endswith(".py"):
                score -= 20
            if any(name in lowered for name in priority_names):
                score -= 10
            if "test" in lowered:
                score -= 8
            score += len(rel.parts)
            candidates.append((score, f"  - {rel} ({size} bytes)"))

        lines = [line for _, line in sorted(candidates)[:12]]
        if not lines:
            return "(no readable files found)"
        return "\n".join(lines)

    @staticmethod
    def _build_file_evidence(work_dir: Path) -> str:
        """Inline compact evidence so Judge does not need file tools."""
        listing = ClaudeCodeJudge._list_work_dir_files(work_dir)
        if listing.startswith("("):
            return listing

        evidence_parts: list[str] = []
        for line in listing.splitlines()[:8]:
            rel = line.strip().split(" (", 1)[0].removeprefix("- ").strip()
            path = work_dir / rel
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if len(content) > 2500:
                content = content[:2500] + "\n... (truncated)"
            evidence_parts.append(f"### {rel}\n```text\n{content}\n```")

        return "\n\n".join(evidence_parts) if evidence_parts else "(no evidence files readable)"

    @staticmethod
    def _build_board_summary(state_board: Any) -> str:
        """从 StateBoard 提取 Judge 需要的精简摘要."""
        lines: list[str] = []

        # objective
        obj = getattr(state_board, "objective", "(unknown)")
        lines.append(f"Objective: {obj}\n")

        # agents (fetch before tasks so we can build task_id -> steps mapping)
        agents = getattr(state_board, "agents", {})
        agent_steps: dict[str, int] = {}
        for _aid, a in agents.items():
            ct = getattr(a, "current_task", "")
            if ct:
                agent_steps[ct] = getattr(a, "steps_used", 0)

        # tasks
        tasks = getattr(state_board, "tasks", {})
        total_estimated = 0
        total_actual = 0
        if tasks:
            lines.append("Tasks:")
            for tid, t in tasks.items():
                status = getattr(t, "status", "?")
                status_val = status.value if hasattr(status, "value") else status
                agent_type = getattr(t, "agent_type", "?")
                deps = getattr(t, "dependencies", [])
                desc = getattr(t, "description", "")[:60]
                est = getattr(t, "estimated_complexity", 1)
                actual = agent_steps.get(tid, 0)
                total_estimated += est
                total_actual += actual
                lines.append(
                    f"  - {tid}: {agent_type} | {status_val} | est={est} | actual={actual} steps | deps={deps} | {desc}"
                )
            lines.append("")
            if total_estimated > 0:
                ratio = total_actual / total_estimated
                lines.append(
                    f"Complexity summary: total_estimated={total_estimated}, "
                    f"total_actual={total_actual}, ratio={ratio:.1f} (约 3-5 steps per complexity point is healthy)\n"
                )
        if agents:
            lines.append("Agents:")
            for aid, a in agents.items():
                status = getattr(a, "status", "?")
                status_val = status.value if hasattr(status, "value") else status
                steps = getattr(a, "steps_used", 0)
                retry = getattr(a, "retry_count", 0)
                lines.append(f"  - {aid}: {status_val} | steps={steps} | retry={retry}")
            lines.append("")

        # budget
        budget = getattr(state_board, "budget", None)
        if budget:
            lines.append(
                f"Budget: steps={budget.steps_taken}/{budget.max_steps}, "
                f"tokens={budget.token_used}/{budget.token_limit}, "
                f"exhausted={budget.exhausted}\n"
            )

        # events — collaboration & recovery focused
        events = getattr(state_board, "events", [])
        if events:
            lines.append("Key events:")
            relevant_keywords = [
                "review",
                "collab",
                "fix",
                "recover",
                "retry",
                "spawned_reviewer",
                "coder_ready",
                "approved",
            ]
            count = 0
            for e in events:
                et = getattr(e, "event_type", "")
                msg = getattr(e, "message", "")
                if any(
                    kw in et.lower() or kw in msg.lower() for kw in relevant_keywords
                ):
                    agent_id = getattr(e, "agent_id", "")
                    task_id = getattr(e, "task_id", "")
                    parts = [f"  [{et}]"]
                    if agent_id:
                        parts.append(f"agent={agent_id}")
                    if task_id:
                        parts.append(f"task={task_id}")
                    if msg:
                        parts.append(msg[:100])
                    lines.append(" ".join(parts))
                    count += 1
                    if count >= 20:
                        break
            lines.append("")

        # human interventions
        human_questions = getattr(state_board, "_human_questions", [])
        if human_questions:
            total = len(human_questions)
            answered = sum(1 for q in human_questions if q.get("answer") is not None)
            lines.append(f"Human questions: {answered}/{total} answered\n")

        # conversation threads
        threads = getattr(state_board, "conversation_threads", {})
        if threads:
            lines.append("Threads:")
            for tid, thread in threads.items():
                msgs = getattr(thread, "messages", [])
                participants = getattr(thread, "participants", set())
                lines.append(f"  - {tid}: {len(msgs)} msgs, {participants}")
            lines.append("")

        return "\n".join(lines)

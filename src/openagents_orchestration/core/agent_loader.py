"""Stage AgentSpec 编译层 —— 一文件一 agent 的加载器。

戏台把角色配置从 monolithic 的 ``agent.json`` 拆成 ``agents/<role>.json``，每个
角色文件自带 ``extends`` 继承、``prompts`` 引用列表、``hooks`` 声明、工具增量。
本模块把这些 **Stage 扩展字段** 编译成 SDK 认的扁平 ``AgentDefinition``。

为什么需要这一层（SDK 约束实测）：
- ``AppConfig`` / ``AgentDefinition`` 都是 ``extra: forbid`` —— ``prompts`` /
  ``hooks`` / ``extends`` 进不了 agent 顶层，必须编译下沉。
- ``pattern.config`` 是自由 ``dict[str, Any]`` 且 ``_instantiate(symbol, config)``
  原样进 ``__init__`` —— ``prompts`` / ``hooks`` 搭这班车进 pattern / runner。
- ``load_config(path)`` 只收单文件、不支持 ``extends`` —— 多文件组装 Stage 自理。

编译产物仍是标准 ``AgentDefinition``，下游 ``load_agent_plugins`` / runner 零感知。

工具与 hook 的 **唯一信源**：``TOOL_REGISTRY`` / ``HOOK_REGISTRY``。角色文件只写
工具 id（如 ``"+apply_patch"``），impl 在此解析，避免 impl 字符串散落各处。
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

from openagents.config.loader import _expand_env_vars
from openagents.config.schema import AgentDefinition

# --------------------------------------------------------------------------
# 单一信源：工具 id → impl（从原 agent.json 提取，新增工具在此登记一次）
# --------------------------------------------------------------------------
TOOL_REGISTRY: dict[str, str] = {
    # corecoder 文件/探索工具
    "read_file": "openagents_orchestration.tools.corecoder.read_file.ReadFileTool",
    "write_file": "openagents_orchestration.tools.corecoder.write_file.WriteFileTool",
    "edit_file": "openagents_orchestration.tools.corecoder.edit_file.EditFileTool",
    "apply_patch": "openagents_orchestration.tools.corecoder.apply_patch.ApplyPatchTool",
    "semantic_edit": "openagents_orchestration.tools.corecoder.semantic_edit.SemanticEditTool",
    "list_directory": "openagents_orchestration.tools.corecoder.list_directory.ListDirectoryTool",
    "glob": "openagents_orchestration.tools.corecoder.glob_tool.GlobTool",
    "grep": "openagents_orchestration.tools.corecoder.grep_tool.GrepTool",
    "bash": "openagents_orchestration.tools.corecoder.bash_tool.BashTool",
    "think": "openagents_orchestration.tools.corecoder.think.ThinkTool",
    "todo_read": "openagents_orchestration.tools.corecoder.todo.TodoReadTool",
    "todo_write": "openagents_orchestration.tools.corecoder.todo.TodoWriteTool",
    "complete_task": "openagents_orchestration.tools.corecoder.complete_task.CompleteTaskTool",
    "run_claude_code": "openagents_orchestration.tools.corecoder.run_claude_code.RunClaudeCodeTool",
    "sub_agent": "openagents_orchestration.tools.corecoder.sub_agent.SubAgentTool",
    "web_search": "openagents_orchestration.tools.corecoder.web_search.WebSearchTool",
    "web_fetch": "openagents_orchestration.tools.corecoder.web_fetch.WebFetchTool",
    # skill
    "read_skill": "openagents_orchestration.tools.read_skill.ReadSkillTool",
    # director 调度工具
    "spawn_agent": "openagents_orchestration.tools.director.spawn_agent.SpawnAgentTool",
    "show_state": "openagents_orchestration.tools.director.show_state.ShowStateTool",
    "replan": "openagents_orchestration.tools.director.replan.ReplanTool",
    "finalize": "openagents_orchestration.tools.director.finalize.FinalizeTool",
    "ask_human": "openagents_orchestration.tools.director.ask_human.AskHumanTool",
    "send_message": "openagents_orchestration.tools.director.send_message.SendMessageTool",
    "classify_intent": "openagents_orchestration.tools.director.classify_intent.ClassifyIntentTool",
    "decompose": "openagents_orchestration.tools.director.decompose.DecomposeTool",
    # github 工具
    "github_pr": "openagents_orchestration.tools.github.pr.GitHubPRTool",
    "github_issue": "openagents_orchestration.tools.github.issue.GitHubIssueTool",
    "github_ci": "openagents_orchestration.tools.github.ci.GitHubCITool",
    "github_repo": "openagents_orchestration.tools.github.repo.GitHubRepoTool",
    # monitor 工具
    "inspect_state": "openagents_orchestration.tools.monitor.inspect_state.InspectStateTool",
    "analyze_event_pattern": "openagents_orchestration.tools.monitor.analyze_event_pattern.AnalyzeEventPatternTool",
    "diagnose_agent": "openagents_orchestration.tools.monitor.diagnose_agent.DiagnoseAgentTool",
    "predict_budget": "openagents_orchestration.tools.monitor.predict_budget.PredictBudgetTool",
    "send_alert": "openagents_orchestration.tools.monitor.send_alert.SendAlertTool",
    "verify_alert_effectiveness": "openagents_orchestration.tools.monitor.verify_alert_effectiveness.VerifyAlertEffectivenessTool",
}

# Hook name → 点路径，由 runner 在 session.start 时按名解析执行（X-04 集中映射）
HOOK_REGISTRY: dict[str, str] = {
    "load_skills_into_context": (
        "openagents_orchestration.hooks.skill_loader:load_skills_into_context"
    ),
}


class AgentSpecError(Exception):
    """角色 spec 编译失败（坏文件 / 未知工具 / 缺必填字段）。"""


# --------------------------------------------------------------------------
# 解析辅助
# --------------------------------------------------------------------------
def _load_json(path: Path) -> dict[str, Any]:
    """读取 + 展开 ``${VAR}`` + parse（与 SDK ``load_config`` 同款顺序）。

    必须先展开环境变量再交给 pydantic：``AgentDefinition`` 校验不认字面量
    ``"${LLM_PROVIDER:-...}"``，会在 provider 枚举校验处报错。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise AgentSpecError(f"无法读取 {path.name}: {exc}") from exc
    try:
        return json.loads(_expand_env_vars(text, source=path))
    except json.JSONDecodeError as exc:
        raise AgentSpecError(f"无法解析 {path.name}: {exc}") from exc


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """深合并 base ← override。dict 递归合并，其余（含 list）override 整体替换。"""
    result = dict(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _tool_id(entry: Any) -> str:
    """工具条目归一为 id 字符串（支持纯 id 字符串或 ``{"id": ...}`` dict）。"""
    if isinstance(entry, str):
        return entry.lstrip("+-")
    if isinstance(entry, dict) and "id" in entry:
        return str(entry["id"])
    raise AgentSpecError(f"非法工具条目: {entry!r}")


def _resolve_tool_ids(
    base_tools: list[Any], overrides: list[Any]
) -> list[str]:
    """把 base 工具集 + 角色增量解析成最终工具 id 列表。

    增量语法：``"+tool"`` 增、``"-tool"`` 删、``"tool"`` 增。若 overrides 不含任何
    ``+``/``-`` 前缀，则视为**显式全量列表**，整体替换 base。
    """
    base_ids = [_tool_id(t) for t in base_tools]
    if not overrides:
        ids = base_ids
    elif any(isinstance(o, str) and o[:1] in "+-" for o in overrides):
        ids = list(base_ids)
        for o in overrides:
            if isinstance(o, str) and o.startswith("-"):
                tid = o[1:]
                ids = [x for x in ids if x != tid]
            else:
                tid = _tool_id(o)
                if tid not in ids:
                    ids.append(tid)
    else:
        ids = [_tool_id(o) for o in overrides]

    unknown = [t for t in ids if t not in TOOL_REGISTRY]
    if unknown:
        raise AgentSpecError(
            f"未知工具 {unknown}（不在 TOOL_REGISTRY；新工具需先在此登记）"
        )
    return ids


# --------------------------------------------------------------------------
# 编译
# --------------------------------------------------------------------------
# Stage 扩展字段（不属于 SDK AgentDefinition，编译时消化，不得直接下传）
_STAGE_ONLY_KEYS = {"extends", "prompts", "hooks", "tools"}


def compile_one_spec(
    raw: dict[str, Any], *, base: dict[str, Any] | None = None
) -> AgentDefinition:
    """把一个角色 spec（含 Stage 扩展字段）编译成 SDK ``AgentDefinition``。

    - ``extends``：与 base 深合并（base ← raw）
    - ``tools``：base 工具集 + 增量 → 全量 ToolRef（impl 查 TOOL_REGISTRY）
    - ``prompts`` / ``hooks``：下沉进 ``pattern.config``（pattern 读 prompts，
      runner 读 hooks）
    - 其余 SDK 标准字段原样保留，最后 pydantic 校验
    """
    base = base or {}
    merged = _deep_merge(base, raw)

    if "id" not in merged:
        raise AgentSpecError("角色 spec 缺少必填字段 'id'")

    # 工具：base + 增量 → ToolRef
    tool_ids = _resolve_tool_ids(base.get("tools", []), raw.get("tools", []))
    tool_refs = [{"id": tid, "impl": TOOL_REGISTRY[tid]} for tid in tool_ids]

    # prompts / hooks 下沉进 pattern.config
    pattern = dict(merged.get("pattern") or {})
    pcfg = dict(pattern.get("config") or {})
    if merged.get("prompts"):
        pcfg["prompts"] = list(merged["prompts"])
    if merged.get("hooks"):
        pcfg["hooks"] = dict(merged["hooks"])
    pattern["config"] = pcfg

    agent_dict: dict[str, Any] = {
        "id": merged["id"],
        "name": merged.get("name", merged["id"]),
        "memory": merged["memory"],
        "pattern": pattern,
        "tools": tool_refs,
    }
    # 可选 SDK 字段：仅在存在时下传（避免把 None 塞给 extra:forbid 之外的默认）
    for opt_key in ("llm", "context_assembler", "runtime"):
        if opt_key in merged and merged[opt_key] is not None:
            agent_dict[opt_key] = merged[opt_key]

    try:
        return AgentDefinition.model_validate(agent_dict)
    except Exception as exc:  # pydantic ValidationError 等
        raise AgentSpecError(
            f"角色 '{merged.get('id')}' 编译产物非法: {exc}"
        ) from exc


def load_agent_specs(agents_dir: Path | str) -> list[AgentDefinition]:
    """扫描 ``agents/*.json``，以 ``_base.json`` 为 base 编译出全部 AgentDefinition。

    容错：单个角色文件坏 → 抛 ``AgentSpecError`` 带文件名（不静默吞，呼应 X-07）。
    """
    agents_dir = Path(agents_dir)
    if not agents_dir.is_dir():
        raise AgentSpecError(f"agents 目录不存在: {agents_dir}")

    base = _load_json(agents_dir / "_base.json")
    specs: list[AgentDefinition] = []
    for f in sorted(agents_dir.glob("*.json")):
        if f.name == "_base.json":
            continue
        raw = _load_json(f)
        try:
            specs.append(compile_one_spec(raw, base=base))
        except AgentSpecError as exc:
            raise AgentSpecError(f"编译 {f.name} 失败: {exc}") from exc
    if not specs:
        raise AgentSpecError(f"{agents_dir} 下没有发现任何角色文件")
    return specs


def resolve_hook(name: str) -> Any:
    """按名从 HOOK_REGISTRY 解析 hook callable（点路径 ``module:symbol``）。"""
    ref = HOOK_REGISTRY.get(name)
    if ref is None:
        raise AgentSpecError(
            f"未知 hook '{name}'（不在 HOOK_REGISTRY）"
        )
    module_name, _, attr = ref.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, attr)

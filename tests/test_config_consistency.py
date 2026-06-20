"""Tests that agent role configuration is consistent with runtime implementations.

角色定义已从 monolithic agent.json 迁到 ``agents/<role>.json``（一文件一 agent），
由 Stage 编译层 ``load_agent_specs`` 编译成 AgentDefinition。本测试改用编译层作为
数据源，断言逻辑（pattern 可导入 / 工具唯一 / env 展开 / runtime 限制）不变。
No real LLM calls — purely static validation of config against code.
"""

from __future__ import annotations

import importlib
import inspect
import os
import re
from pathlib import Path

import pytest

from openagents_orchestration.core.agent_loader import load_agent_specs

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _import_class(dotted_path: str) -> type:
    """Dynamically import a class from a dotted module path."""
    module_path, class_name = dotted_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def _agents_dir() -> Path:
    return Path(__file__).parent.parent / "agents"


@pytest.fixture(scope="module")
def agents_by_id():
    """Mapping of agent id -> AgentDefinition（经 Stage 编译层）。"""
    os.environ.setdefault("LLM_API_BASE", "http://localhost:9999")
    os.environ.setdefault("LLM_MODEL", "test-model")
    specs = load_agent_specs(_agents_dir())
    return {a.id: a for a in specs}


# ---------------------------------------------------------------------------
# 1. Agent roles match documentation
# ---------------------------------------------------------------------------


def test_agent_roles_match_documentation(agents_by_id):
    """agent.json 中的 agent id 集合必须等于文档化的 7 个角色。"""
    expected = {
        "director",
        "coder",
        "reviewer",
        "researcher",
        "github_agent",
        "monitor",
        "team_leader",
    }
    actual = set(agents_by_id.keys())
    assert actual == expected, f"Expected agents {expected}, got {actual}"


# ---------------------------------------------------------------------------
# 2. All pattern classes are importable
# ---------------------------------------------------------------------------


def test_all_pattern_classes_are_importable(agents_by_id):
    """每个 agent 的 pattern.impl 必须能动态导入且是类。"""
    for agent_id, agent in agents_by_id.items():
        impl = agent.pattern.impl
        cls = _import_class(impl)
        assert inspect.isclass(cls), f"{agent_id}: {impl} is not a class"


# ---------------------------------------------------------------------------
# 3. All tool classes are importable
# ---------------------------------------------------------------------------


def test_all_tool_classes_are_importable(agents_by_id):
    """每个 agent 的每个 tool impl 必须能动态导入且是类。"""
    for agent_id, agent in agents_by_id.items():
        for tool in agent.tools:
            impl = tool.impl
            cls = _import_class(impl)
            assert inspect.isclass(cls), (
                f"{agent_id} tool {tool.id}: {impl} is not a class"
            )


# ---------------------------------------------------------------------------
# 4. Pattern assignment rules
# ---------------------------------------------------------------------------


def test_director_and_team_leader_use_scheduler_patterns(agents_by_id):
    """director/team_leader 的 pattern 不能是裸 CoreCoderPattern；
    coder/reviewer/researcher/github_agent/monitor 必须是 CoreCoderPattern 或其子类。
    """
    corecoder = _import_class("openagents_orchestration.patterns.corecoder.CoreCoderPattern")

    scheduler_ids = {"director", "team_leader"}
    tactical_ids = {"coder", "reviewer", "researcher", "github_agent", "monitor"}

    for aid in scheduler_ids:
        agent = agents_by_id[aid]
        # 语义：scheduler 必须有自己专属的调度 pattern，不能是裸 CoreCoderPattern
        assert agent.pattern.impl != "openagents_orchestration.patterns.corecoder.CoreCoderPattern", (
            f"{aid}: pattern must not be bare CoreCoderPattern"
        )

    for aid in tactical_ids:
        agent = agents_by_id[aid]
        cls = _import_class(agent.pattern.impl)
        assert issubclass(cls, corecoder), (
            f"{aid}: {agent.pattern.impl} is not a subclass of CoreCoderPattern"
        )


# ---------------------------------------------------------------------------
# 5. Tool uniqueness
# ---------------------------------------------------------------------------


def test_agent_tools_are_unique(agents_by_id):
    """每个 agent 的工具 id 列表没有重复。"""
    for agent_id, agent in agents_by_id.items():
        tool_ids = [t.id for t in agent.tools]
        assert len(tool_ids) == len(set(tool_ids)), (
            f"{agent_id}: duplicate tool ids found: {tool_ids}"
        )


# ---------------------------------------------------------------------------
# 6. Env placeholders resolvable
# ---------------------------------------------------------------------------


def test_env_placeholders_have_defaults_or_are_set(agents_by_id):
    """编译后所有 ${...} 占位符必须已被解析（编译层在 parse 前展开 env）。

    如果占位符未解析，llm 字段会残留 ``${``，或 provider 校验直接失败。
    """
    for agent in agents_by_id.values():
        llm = agent.llm
        assert llm is not None
        for field in ("api_base", "model"):
            val = getattr(llm, field, "")
            assert val and "${" not in val, (
                f"{agent.id}: llm.{field} still contains unresolved placeholder: {val}"
            )

        # api_key_env 是环境变量名（不是值），允许 ${...} 但展开后应是一个字符串名
        api_key_env = llm.api_key_env
        assert api_key_env and "${" not in api_key_env, (
            f"{agent.id}: llm.api_key_env unresolved: {api_key_env}"
        )


# ---------------------------------------------------------------------------
# 7. Runtime limits
# ---------------------------------------------------------------------------


def test_agent_runtime_has_reasonable_limits(agents_by_id):
    """每个 agent 必须有 runtime.max_steps 且大于 0。"""
    for agent_id, agent in agents_by_id.items():
        assert hasattr(agent, "runtime"), f"{agent_id}: missing runtime config"
        assert agent.runtime is not None, f"{agent_id}: runtime is None"
        max_steps = getattr(agent.runtime, "max_steps", None)
        assert max_steps is not None, f"{agent_id}: missing runtime.max_steps"
        assert isinstance(max_steps, int), f"{agent_id}: max_steps must be int"
        assert max_steps > 0, f"{agent_id}: max_steps must be > 0, got {max_steps}"


# ---------------------------------------------------------------------------
# 8. Bonus: raw JSON env placeholder coverage (不依赖 load_config 的展开)
# ---------------------------------------------------------------------------


def test_raw_json_env_placeholders_covered():
    """直接读取 agents/*.json 原文，检查所有 ${VAR} 都有默认值或已被 conftest 设置。"""
    raw = "\n".join(
        f.read_text(encoding="utf-8") for f in _agents_dir().glob("*.json")
    )
    placeholders = set(re.findall(r"\$\{([^}]+)\}", raw))

    # 提取变量名（去掉 :-default 部分）
    var_names = set()
    for ph in placeholders:
        var_name = ph.split(":-")[0].strip()
        var_names.add(var_name)

    # conftest 已设置 LLM_API_BASE 和 LLM_MODEL；
    # LLM_PROVIDER 和 LLM_API_KEY_ENV 有默认值 :-openai_compatible / :-LLM_API_KEY
    for var in var_names:
        if var in ("LLM_API_BASE", "LLM_MODEL"):
            assert os.environ.get(var), f"Environment variable {var} not set"
        elif var in ("LLM_PROVIDER", "LLM_API_KEY_ENV"):
            # 有 :-default，不需要环境变量
            pass
        else:
            pytest.fail(f"Unexpected env placeholder variable: {var}")

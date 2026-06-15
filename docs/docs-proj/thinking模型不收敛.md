# CoreCoder + DeepSeek 无法收敛问题记录

**日期**: 2026-06-14  
**状态**: 已解决  
**相关模块**: `src/openagents_orchestration/patterns/corecoder.py`, `src/prompts/core.py`

---

## 问题现象

使用 `scripts/run_corecoder.py` 驱动 CoreCoder 单 Agent（接入 `.env` 中的 DeepSeek `deepseek-v4-flash`）进行自主 bug 修复时，出现以下现象：

- Agent 会反复调用 `read_file` / `list_directory` / `grep` / `think` 等只读/思考工具。
- 30～50 步后步数耗尽，仍未产出最终修复或总结。
- 最终输出为 `[CoreCoder] step budget exhausted before producing a final answer.`
- 消耗 token 50k+，没有实际文件改动。

## 根因分析

`deepseek-v4-flash` 是 reasoning 模型，在 **开放 ended 的 ReAct 长循环** 中容易陷入“过度探索”：

1. CoreCoder 的 system prompt 鼓励“先探索、再行动”，但没有明确上限。
2. 循环本身没有 deterministic guardrail 来打断连续只读探索。
3. 工具列表较长（默认 19 个）时，模型更容易 stuck 在反复确认状态。

本质不是模型不会调用工具（短 prompt 下单工具调用正常），而是**循环缺少“逼它行动”的机制**。

## 解决方案（参考 Claude Code）

Claude Code 的核心设计之一是“厚确定性基础设施”：用硬规则约束 Agent 循环，而不是完全依赖模型自律。

本轮落地了三个 guardrail：

### 1. 连续只读探索计数

- 每轮识别本轮 tool_calls 是否全是只读/无副作用工具。
- 记录 `__consecutive_readonly_steps__`。
- 超过 `max_consecutive_readonly_steps`（默认 5）后，向对话注入强制 nudge：
  > “你已使用 N 次连续探索步，剩余 X 步。停止探索，立即选择：改文件 / 跑验证 / 结束。”

### 2. 步数预算警告

- 当剩余步数 ≤ `step_budget_warning_steps`（默认 5）时注入警告：
  > “Step budget warning: X step(s) remain...”

### 3. System Prompt 强化

在 `src/prompts/core.py` 的 `CORE_PRINCIPLES` 中新增：

> **You have a limited step budget.** Do not explore indefinitely. After at most a few read/search turns, you MUST edit, run a verification command, or finish.

## 改动文件

- `src/openagents_orchestration/patterns/corecoder.py`
  - 新增配置 `max_consecutive_readonly_steps`、`step_budget_warning_steps`。
  - 新增 `_is_readonly_tool_call`、`_build_readonly_budget_message`、`_build_step_budget_warning`。
  - 在每轮循环开头注入探索预算和步数预算提示。
- `src/prompts/core.py`
  - system prompt 增加“步数有限、禁止无限探索”原则。
- `tests/test_corecoder_guardrails.py`
  - 新增 guardrail 单元测试。

## 验证结果

Guardrail 落地后，CoreCoder 成功完成一轮自主修复：

- 13 步、约 21k tokens
- 发现并修复 `src/openagents_orchestration/tools/corecoder/web_fetch.py` 中 `<a>` 标签把 URL 当链接文本的 bug
- 新增 `tests/test_web_fetch.py::test_web_fetch_anchor_link_text` 验证该修复
- 全量测试：`542 passed, 7 skipped`

## 后续注意

- 若未来接入其他 reasoning 模型（如 DeepSeek-R1），建议保持本 guardrail 开启。
- 若工具列表继续膨胀，可进一步做“按意图动态裁剪工具集”，但当前 guardrail 已足够让 CoreCoder 收敛。
- 交互式/单 Agent 场景下，可考虑把 `coder` 默认工具集裁剪到 7～10 个，进一步降低模型认知负担。

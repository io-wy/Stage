# CoreCoder 单 Agent 对齐 Claude Code：方法论与迭代设计

> 目标：以 CoreCoder 为唯一执行 Agent，参考 Claude Code 的生产设计，把单 Agent 的编码能力逐步推到接近 Claude Code 的水平。

## 1. 核心观点：Claude Code 强在哪里

根据对 Claude Code 源码级分析（`how-claude-code-works`、`Dive into Claude Code`），其 50 万行 TypeScript 中只有约 **1.6% 是 AI 决策逻辑**，其余 **98.4% 是确定性基础设施**：权限与沙箱、上下文组装与压缩、工具路由、错误恢复、子 Agent 隔离、任务与记忆持久化。

因此对 CoreCoder 的优化方向不是“让 prompt 更复杂”，而是：

> **保持 ReAct 循环简单，把循环周围的确定性基础设施做厚。**

Claude Code 的每次模型调用都遵循统一管线：

```
设置解析 → 状态初始化 → 上下文组装 → 5 层预模型 shaper → 模型调用
→ 工具分发 → 权限门 → 工具执行 → 停止条件
```

CoreCoder 已经具备其中大部分雏形，但各层都较薄。完整迭代计划见 `corecoder_claude_code_phases.md`，本文先给出方法论与总体架构。

## 2. 差距分层

| 层级 | Claude Code 做法 | CoreCoder 现状 | 关键差距 |
|------|------------------|---------------|---------|
| **Agent Loop** | 简单 `while` 循环，9 步管线，7 种恢复策略 | `CoreCoderPattern.execute()` 已有 ReAct、失败计数、空响应处理 | 缺少中断恢复、继续策略、tool pre-execution |
| **Context Engineering** | 4 级渐进压缩 + 5 个 shaper + 前缀缓存 | `__DYNAMIC_BOUNDARY__` 分静态/动态；有截断 | 无 snip/dedup/collapse/summarize 分层；无大输出 spill-to-file |
| **Tool System** | 66+ 工具统一接口，只读并行、写串行，结果结构化 | 已实现统一 `ToolPlugin`，支持 `concurrency_safe` | 缺少 MCP、图片、worktree、结果结构化摘要 |
| **Safety** | 权限模式 + 规则 + Bash AST + 确认 + Hook 五层 | bash 有基础拦截 | 无 AST 级分析、无规则引擎、无 Hook 管线 |
| **Verification** | gather → act → verify 三阶段，自动跑测试/诊断 | `__pending_verification__` + nudge | 未自动执行测试；未解析失败并自动定位修复 |
| **Extension** | Agent / Skills / Plugins / MCP 四种机制 | `sub_agent`、`run_skill` 已存在 | 缺 MCP 客户端；coder 不能 spawn sub-agent；无 Hook |
| **UX** | 流式输出、tool pre-execution、快速启动 | 无流式 | 可延后做，但对“感觉快”很重要 |

## 3. 设计原则

1. **确定性基础设施优先**：先把错误处理、权限、验证、上下文压缩做扎实，再调 prompt。
2. **统一接口**：所有扩展（MCP、Skill、子 Agent、Hook）都走 `ToolPlugin` 或事件总线，不复用则已，复用就复用同一安全与执行管线。
3. **Read before write, verify after edit**：把这两件事变成不可绕过的机制，而不是 prompt 里的建议。
4. **失败即反馈**：任何工具失败、测试失败、LLM 空响应都必须结构化地回到 loop，供模型自诊断。
5. **渐进压缩**：context 满了不要一次性粗暴截断，而是分级释放空间，并保留最近编辑文件的记忆。
6. **最小权限**：危险操作默认需要用户确认，可通过权限模式或规则降级为自动。

## 4. 现状映射

CoreCoder 已经打下的基础：

- **统一工具层**：`src/openagents_orchestration/tools/corecoder/` 下的 `read_file`、`write_file`、`edit_file`、`apply_patch`、`semantic_edit`、`bash`、`glob`、`grep`、`list_directory`、`think`、`web_search`、`web_fetch`、`sub_agent`、`complete_task`、`run_claude_code`。
- **ReAct 循环**：`CoreCoderPattern.execute()` 支持 native tool calling、并发分发、失败计数、空响应/文本拒绝处理、planning phase。
- **上下文分离**：`prompts/core.py` 提供静态 `CORE_PRINCIPLES`；`prompts/dynamic.py` 的 `build_runtime_fragment` 注入动态片段，中间用 `__DYNAMIC_BOUNDARY__` 分隔，利于前缀缓存。
- **验证萌芽**：`_build_verification_nudge()` 在编辑后提示模型跑测试；`_detect_project_type()` 能识别 Python/Node/Rust/Go 等并推荐测试命令。
- **子 Agent**：`sub_agent` 可 spawn reviewer/researcher，但仅一层；`run_claude_code` 可作为终极兜底。

## 5. 迭代路线图概览

| 阶段 | 主题 | 核心目标 | 关键产出 |
|------|------|---------|---------|
| Phase 0 | 工具补齐 | 覆盖 Claude Code 常用工具 | `apply_patch`、`semantic_edit`、`complete_task`、`sub_agent`、`run_claude_code` |
| Phase 1 | 执行可信 | 编辑成功率高、失败可自诊断、危险操作受控 | 自动验证闭环、Bash 安全增强、编辑降级链、失败恢复状态机 |
| Phase 2 | 上下文工程 | 长任务不爆窗、信息相关、缓存友好 | 动态片段分类、四层渐进压缩、大输出 spill-to-file |
| Phase 3 | 扩展架构 | 连接外部世界、支持多 Agent | MCP 客户端、coder 级 sub-agent、Hooks 管线 |
| Phase 4 | 产品化体验 | 感觉像 Claude Code | 流式输出、tool pre-execution、权限模式、Plan Mode、多模态 |

**注**：根据反馈，**Phase 4.1 流式输出**和**Phase 4.2 tool pre-execution**对体验影响极大，不依赖 MCP 或多模态，可在 Phase 1/2 之后尽早实现（详细设计见 `corecoder_claude_code_phases.md`）。

详细设计（每阶段的具体改动、涉及文件、验收标准）见 `corecoder_claude_code_phases.md`。

## 6. 总体验收标准

| 阶段 | 核心验收 |
|------|---------|
| Phase 0 | 新增工具的单元测试全部通过；dirty_files 追踪一致 |
| Phase 1 | 在 10 个代表性编辑任务中，自动验证闭环能把 70% 以上失败自动修复或准确求助；危险 bash 默认被拦截 |
| Phase 2 | 模拟长对话（>30 轮）下，context 不爆炸且关键信息（最近编辑、plan、错误）保留 |
| Phase 3 | 能连接一个真实 MCP server 并调用其工具；coder 能成功 spawn reviewer 子 agent |
| Phase 4 | 流式输出可用；plan mode 走完完整审批流程 |

## 7. 测试策略

- **Mock LLM**：所有测试不依赖真实 API，通过 mock `llm_client.generate` 返回预设 tool_calls。
- **失败注入**：专门测试工具失败、验证失败、空响应、循环被打断等场景。
- **集成测试**：在 `demo/` 或临时目录中跑端到端任务，检查文件修改、测试执行、dirty_files 记录。
- **回归测试**：每轮改动后跑 `uv run pytest tests/ -v`。

## 8. 风险与反模式

- **反模式 1：过度依赖 prompt** — Claude Code 的经验是规则/基础设施比 prompt 更可靠。
- **反模式 2：过早做流式/UX** — 先把执行正确性做扎实；流式不能掩盖错误。
- **反模式 3：子 agent 滥用** — 子 agent 只用于独立、需要独立上下文的任务，否则增加延迟和失败面。
- **反模式 4：权限过松** — 默认必须安全，自动模式需要显式授权。

## 9. 参考

- `claude_code_reference.md` — 能力对照表
- `corecoder_claude_code_phases.md` — 分阶段详细设计
- `src/openagents_orchestration/patterns/corecoder.py` — ReAct 循环实现
- `src/prompts/core.py` — 静态 system prompt
- `src/prompts/dynamic.py` — 动态上下文片段
- `src/openagents_orchestration/tools/corecoder/` — 工具实现
- 外部资料：
  - https://github.com/Windy3f3f3f3f/how-claude-code-works
  - https://arxiv.org/abs/2604.14228
  - https://docs.anthropic.com/en/docs/claude-code

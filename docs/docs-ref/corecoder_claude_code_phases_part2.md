# CoreCoder 单 Agent 对齐 Claude Code：分阶段详细设计（Part 2 — Phase 3~4）

本文是 `corecoder_claude_code_method.md` 的配套文档，覆盖 Phase 3 扩展架构、Phase 4 产品化体验，以及总体验收清单与涉及文件总览。

## Phase 3 — 扩展架构

### 3.1 MCP 客户端集成

Claude Code 通过 MCP 把外部工具纳入统一工具池。

#### 设计

- 读取 `.claude/mcp.json` 或 `agent.json` 中的 `mcp_servers` 配置。
- 用 stdio/SSE 连接 MCP server，把 server 提供的工具转换为 `ToolPlugin` schema。
- 所有 MCP 工具走同样的权限/执行/结果格式化管线。

#### 涉及文件

- 新增 `src/openagents_orchestration/tools/mcp/client.py`
- 新增 `src/openagents_orchestration/tools/mcp/adapter.py`
- 修改 `src/openagents_orchestration/tools/__init__.py` 注册 MCP 工具

### 3.2 coder 级 sub-agent（✅ 已实现）

当前只有 Director/TeamLeader 能 spawn agent。让 coder 也能 spawn 子 agent：

- 在 `agent.json` 中给 coder 增加 `spawn_agent` 工具权限（或复用 `sub_agent` 的内部 runner 接口）。
- 子 agent 的上下文与父 agent 隔离，只返回 summary。
- 防止无限递归：记录 `__sub_agent_depth__`，超过 2 层禁止再 spawn。

### 3.3 Hooks / 事件管线（✅ 已实现）

Claude Code 有 27 种 hook 事件。CoreCoder 从关键事件开始：

- `tool.before_invoke`：允许 Hook 修改参数或阻止执行（权限、安全）。
- `tool.after_invoke`：允许 Hook 修改结果、触发验证。
- `pattern.before_llm`：允许 Hook 修改 messages（压缩 shaper 的注入点）。
- `pattern.after_llm`：用于日志、监控、流式事件。

实现：在 `CoreCoderPattern` 的 `_invoke_llm`、`_dispatch_single_tool` 等位置 emit 事件；runner 或外部注册 Hook handler。

## Phase 4 — 产品化体验（🔄 部分实现）

### 4.1 权限模式（✅ 已实现）

Claude Code 模式：`plan` → `default` → `acceptEdits` → `auto` → `dontAsk` → `bypassPermissions`。CoreCoder 先实现：

- `default`：写操作和危险 bash 需要确认。
- `acceptEdits`：文件编辑自动通过，bash 仍需确认。
- `auto`：规则允许的操作自动通过。

### 4.2 Plan Mode（✅ 已实现）

- `EnterPlanMode`：停止工具调用，让模型生成 plan 文件（如 `.agent_plan.md`）。
- 用户/上级 Agent 审批后 `ExitPlanMode` 进入执行。
- 执行过程中可再次进入 plan mode 调整。

### 4.3 多模态

- 在 `read_file` 中支持图片读取，把图片以 base64/base64_url 形式送入支持多模态的模型。
- 增加 `analyze_image` 工具。

## 总体验收清单

| 阶段 | 验收项 |
|------|--------|
| Phase 0 | 新增工具单测通过；dirty_files 一致；sub-agent 支持 2 层 |
| Phase 1 | 编辑降级链生效；验证闭环强制跑测试；Bash 危险命令被拦截；恢复状态机覆盖 retry/replan/delegate/ask |
| Phase 2 | 长对话（>30 轮）context 可控；动态片段分类清晰；spill-to-file 对大输出生效；流式输出与 tool pre-execution 可用 |
| Phase 3 | 可连真实 MCP server；coder 能 spawn 子 agent；Hook 能拦截/修改工具调用 |
| Phase 4 | 权限模式可切换；plan mode 审批流程完整；多模态 read_file 可用 |

## 涉及文件总览

```
src/openagents_orchestration/
  patterns/corecoder.py              # ReAct 循环、恢复状态机、验证检查、流式改造
  patterns/stream_parser.py          # 流式 tool_use 解析（新增）
  models/stream.py                   # StreamEvent 定义（新增）
  context.py / context/shapers.py    # 渐进压缩、上下文分类
  tools/corecoder/
    edit_file.py                     # 精确替换
    apply_patch.py                   # 批量/模糊补丁
    semantic_edit.py                 # 自然语言兜底
    bash_tool.py                     # 安全规则、AST、权限
    complete_task.py                 # 完成信号
    sub_agent.py                     # 子 Agent 委派
    test_parser.py                   # 测试失败解析（新增）
  tools/mcp/                         # MCP 客户端（新增）
  hooks/                             # Hook 管线（新增）

agent.json                           # 增加 project_root、bash_rules、mcp_servers 配置
prompts/core.py                      # 静态 system prompt
prompts/dynamic.py                   # 动态片段、项目类型检测
tests/                               # 各阶段单测与集成测试
```

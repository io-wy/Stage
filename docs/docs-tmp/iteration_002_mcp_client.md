# CoreCoder → Claude Code 迭代日志 #002

**日期**: 2026-06-14  
**目标**: 以 CoreCoder 单 Agent 为基线，参考 Claude Code 生产设计，持续迭代优化。  
**本轮主题**: MCP 客户端集成 —— 让 CoreCoder 单 Agent 能够调用外部 MCP server 的工具。

---

## 1. 本轮启动时的已验证状态

迭代 #001 完成后：

- `CompressingContextAssembler` 已补齐 **Deduplicate** 层，形成四层渐进压缩：
  - Layer 1: Snip（≥50% 预算）
  - Layer 2: Deduplicate（≥60% 预算）✅ #001 新增
  - Layer 3: Summarize（≥70% 预算）
  - Layer 4: Hard-collapse（≥90% 预算）
- 全量测试：`523 passed, 7 skipped`。

剩余最大差距（按影响排序）：

1. **MCP 客户端**：Claude Code 通过 MCP 连接大量外部工具（浏览器、数据库、GitHub、文件系统等），是能力扩展的主要通道。
2. **流式输出 + Tool Pre-execution**：UX 与延迟。
3. **多模态 / Worktree**。

---

## 2. 为什么本轮做 MCP

Claude Code 的扩展性核心来自三层：

- **Built-in tools**（Bash/Read/Edit/WebSearch 等）
- **Skills**（项目内复用脚本）
- **MCP**（外部 server 提供的工具与资源）

CoreCoder 已覆盖 built-in tools 与 skills，但缺少 MCP。没有 MCP，单 agent 无法：

- 操作浏览器（Playwright MCP）
- 查询 PostgreSQL（PostgreSQL MCP）
- 与 GitHub/GitLab 深度集成
- 调用企业内部的自定义工具

因此 MCP 是补齐能力差距的最高优先级方向。

---

## 3. 设计

### 3.1 架构

```
agent.json ──▶ mcp_servers 配置
                   │
                   ▼
    McpClientManager (单例/Runner 持有)
                   │
      ┌────────────┼────────────┐
      ▼            ▼            ▼
  stdio client  sse client  streamable_http client
      │            │            │
      ▼            ▼            ▼
  MCP Session  MCP Session  MCP Session
      │            │            │
      └────────────┴────────────┘
                   │
                   ▼
      McpToolAdapter ──▶ ToolPlugin schema
                   │
                   ▼
        CoreCoderPattern.tools
```

### 3.2 配置

在 `agent.json` 中新增 `mcp_servers` 配置（与 `.claude/mcp.json` 格式对齐）：

```json
{
  "mcp_servers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed"],
      "env": {}
    },
    "fetch": {
      "command": "uvx",
      "args": ["mcp-server-fetch"]
    }
  }
}
```

### 3.3 新增文件

- `src/openagents_orchestration/tools/mcp/__init__.py`
- `src/openagents_orchestration/tools/mcp/client.py` —— `McpClientManager` 与连接管理
- `src/openagents_orchestration/tools/mcp/adapter.py` —— `McpToolAdapter`，把 MCP Tool 转为 `ToolPlugin`
- `tests/test_mcp_client.py` —— 单元测试

### 3.4 生命周期

1. **Runner 启动时**：读取 `agent.json` 的 `mcp_servers`，为每个 server 建立 `McpClientManager`。
2. **Agent 启动时**：`McpClientManager.list_tools()` 获取工具列表，通过 `McpToolAdapter` 包装后注入 `ctx.tools`。
3. **调用时**：`McpToolAdapter.invoke()` 通过 `session.call_tool()` 转发请求，返回结果格式化为 CoreCoder 工具结果。
4. **关闭时**：Runner 关闭所有 MCP session。

### 3.5 工具命名

避免 ID 冲突：`mcp:<server_name>:<tool_name>`

例如：`mcp:filesystem:read_file`

### 3.6 安全

- MCP 工具默认走与 built-in 工具相同的 `ToolPlugin` 执行管线（Hook、超时、权限模式）。
- 对文件系统类 MCP server，依赖 server 自身的 allowlist；CoreCoder 侧不额外放行危险命令。
- 首次连接失败不阻断 agent 启动，记录 warning，后续按需重连。

---

## 4. 本轮实现范围

迭代 #002 聚焦 **stdio 传输 + 工具调用** 的最小可用闭环：

- [ ] 新增 `mcp` 依赖到 `pyproject.toml`。
- [ ] `McpClientManager` 支持 stdio 连接、`list_tools()`、`call_tool()`、`close()`。
- [ ] `McpToolAdapter` 实现 `ToolPlugin` 接口，动态注册。
- [ ] `tools/__init__.py` 或 Runner 集成：把 MCP 工具注入 agent。
- [ ] 单元测试使用 mock MCP session，不依赖真实 server。

SSE / Streamable HTTP、资源/提示支持留到后续迭代。

---

## 5. 验收标准

- [x] `pyproject.toml` 包含 `mcp>=1.4.0`。
- [x] mock session 下，`McpClientManager.list_tools()` 返回工具列表。
- [x] `McpToolAdapter.invoke()` 正确转发参数并返回格式化结果。
- [x] 工具 schema 符合 CoreCoder 的 OpenAI/Anthropic 双格式要求。
- [x] 全量测试 `uv run pytest tests/ -v` 仍通过。

---

## 6. 本轮结果

- **新增依赖**: `mcp>=1.4.0`（实际安装 1.27.2）。
- **新增文件**:
  - `src/openagents_orchestration/tools/mcp/__init__.py`
  - `src/openagents_orchestration/tools/mcp/client.py` —— `McpClientManager`（stdio 连接、list_tools、call_tool、close）。
  - `src/openagents_orchestration/tools/mcp/adapter.py` —— `McpToolAdapter` + `build_mcp_tools`。
  - `tests/test_mcp_client.py` —— 6 个用例。
- **修改文件**:
  - `pyproject.toml` —— 增加 `mcp` 依赖。
  - `src/openagents_orchestration/core/runner.py` ——
    - 从 `agent.json` 读取 `mcp_servers`。
    - `run()` 中连接 MCP server 并在结束时关闭。
    - `_ensure_bundle()` 将缓存的 MCP 工具注入 agent 工具池。
    - 顺手修复了文件中 3 处 pre-existing 的 `try/except/pass` lint 警告。
- **测试**: `529 passed, 7 skipped`（新增 6 个 MCP 用例）。
- **Lint**: `ruff check` 通过。

### 配置示例

在 `agent.json` 顶层加入：

```json
{
  "mcp_servers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/allowed/path"]
    }
  }
}
```

运行时工具名将以 `mcp:filesystem:<tool_name>` 形式出现在 agent 工具池中。

---

## 7. 下一轮候选方向

按优先级排序：

1. **流式输出 + Tool Pre-execution**：对“感觉快”影响最大，但需改造 `CoreCoderPattern.execute()` 返回类型。
2. **MCP 能力补齐**：SSE / Streamable HTTP 传输、MCP resources/prompts 支持。
3. **多模态 / Worktree**。

本轮完成后将根据剩余时间和测试情况选择。

---

## 8. 参考

- `../docs-ref/corecoder_claude_code_method.md`
- `../docs-ref/corecoder_claude_code_phases.md`
- `iteration_001_context_compression.md`
- MCP Python SDK: https://github.com/modelcontextprotocol/python-sdk
- Claude Code MCP docs: https://docs.anthropic.com/en/docs/claude-code/mcp

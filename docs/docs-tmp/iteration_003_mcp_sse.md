# CoreCoder → Claude Code 迭代日志 #003

**日期**: 2026-06-14  
**目标**: 以 CoreCoder 单 Agent 为基线，参考 Claude Code 生产设计，持续迭代优化。  
**本轮主题**: MCP SSE 传输支持 —— 扩展 MCP 客户端以支持远程 SSE server。

---

## 1. 本轮启动时的已验证状态

- 迭代 #001：四层上下文压缩（Deduplicate 层）。
- 迭代 #002：MCP stdio 客户端 + Runner 集成。
- 全量测试：`529 passed, 7 skipped`。

剩余主要差距：
1. MCP SSE / Streamable HTTP（本轮目标）。
2. 流式输出 + Tool Pre-execution。
3. 多模态 / Worktree。

---

## 2. 为什么本轮做 MCP SSE

Claude Code 的 MCP 配置中大量 server 通过 SSE（Server-Sent Events）暴露，例如：

- 企业内部部署的 MCP gateway
- 需要长时间运行的服务（数据库、浏览器）
- 不想在本地起子进程的 server

迭代 #002 已实现 stdio，但只覆盖了一半场景。SSE 传输与 stdio 共用同一 session/adapter 层，只需在 client 中新增一种 transport 即可，改动集中、风险可控。

---

## 3. 设计

### 3.1 配置

在 `agent.json` 的 `mcp_servers` 中支持 `transport: "sse"`：

```json
{
  "mcp_servers": {
    "remote-fetch": {
      "transport": "sse",
      "url": "http://localhost:3000/sse"
    }
  }
}
```

无 `transport` 时默认 `stdio`。

### 3.2 实现

- `McpClientManager._connect()` 根据 `cfg.transport` 分发：
  - `"stdio"` → `_connect_stdio()`
  - `"sse"` → `_connect_sse()`
- `_connect_sse(url)` 使用 `mcp.client.sse.sse_client(url)` 建立 SSE 连接，复用同一 `ClientSession` 生命周期。
- 工具发现、调用、adapter 完全复用 #002 的实现。

### 3.3 生命周期

SSE 连接同样通过 `AsyncExitStack` 管理，在 `close()` 中统一关闭。

---

## 4. 涉及文件

- `src/openagents_orchestration/tools/mcp/client.py` —— 增加 SSE 连接逻辑。
- `tests/test_mcp_client.py` —— 增加 SSE 连接测试。

---

## 5. 验收标准

- [x] `McpClientManager` 能解析 `transport: "sse"` 配置。
- [x] mock SSE client 下，`connect_all()` 成功并返回 `ok`。
- [x] SSE server 的工具能正常通过 `list_tools()` 获取。
- [x] 全量测试 `uv run pytest tests/ -v` 仍通过。

---

## 6. 本轮结果

- **修改文件**:
  - `src/openagents_orchestration/tools/mcp/client.py`
    - 导入 `mcp.client.sse.sse_client`。
    - `_connect()` 增加 `"sse"` 分支。
    - 新增 `_connect_sse()`，使用 `AsyncExitStack` 管理 SSE session 生命周期。
  - `tests/test_mcp_client.py`
    - 新增 `test_manager_connects_sse_and_lists_tools` 用例。
- **测试**: `530 passed, 7 skipped`（新增 1 个 SSE 用例）。
- **Lint**: `ruff check` 通过。

### 配置示例

```json
{
  "mcp_servers": {
    "remote-fetch": {
      "transport": "sse",
      "url": "http://localhost:3000/sse"
    }
  }
}
```

stdio 与 SSE 两种传输现在共用同一 `ClientSession`、`list_tools()`、`call_tool()`、`McpToolAdapter` 管线。

---

## 7. 下一轮候选方向

按优先级排序：

1. **流式输出 + Tool Pre-execution**：对“感觉快”影响最大。
2. **MCP resources/prompts**：让 agent 能读取 MCP server 提供的资源和提示模板。
3. **多模态 / Worktree**。

本轮完成后将根据剩余时间和测试情况选择。

---

## 8. 参考

- `../docs-ref/corecoder_claude_code_method.md`
- `../docs-ref/corecoder_claude_code_phases.md`
- `iteration_002_mcp_client.md`
- MCP Python SDK SSE client: https://github.com/modelcontextprotocol/python-sdk

- `../docs-ref/corecoder_claude_code_method.md`
- `../docs-ref/corecoder_claude_code_phases.md`
- `iteration_002_mcp_client.md`
- MCP Python SDK SSE client: https://github.com/modelcontextprotocol/python-sdk

# CoreCoder → Claude Code 迭代日志 #004

**日期**: 2026-06-14  
**目标**: 以 CoreCoder 单 Agent 为基线，参考 Claude Code 生产设计，持续迭代优化。  
**本轮主题**: MCP Resources / Prompts 客户端支持。

---

## 1. 本轮启动时的已验证状态

- 迭代 #001：四层上下文压缩。
- 迭代 #002：MCP stdio 客户端 + Runner 集成。
- 迭代 #003：MCP SSE 传输。
- 全量测试：`530 passed, 7 skipped`。

剩余主要差距：
1. MCP resources/prompts（本轮目标）。
2. 流式输出 + Tool Pre-execution。
3. 多模态 / Worktree。

---

## 2. 为什么本轮做 MCP Resources / Prompts

MCP 不只是工具调用，还包括：

- **Resources**：server 暴露的只读数据（文件、数据库记录、API 响应等），通过 URI 访问。
- **Prompts**：server 提供的可复用提示模板，可带参数。

Claude Code 通过 MCP resources 把外部数据源注入上下文，通过 prompts 统一常见任务的提示风格。CoreCoder 目前只能调用 MCP tools，缺少 resources/prompts 能力。

本轮把客户端能力补齐，为后续把 resources 注入 system prompt、把 prompts 作为模板使用打下基础。

---

## 3. 设计

### 3.1 新增客户端方法

在 `McpClientManager` 中增加：

- `list_resources(server_name)` → `{server_name: [Resource, ...]}`
- `read_resource(server_name, uri)` → `ReadResourceResult`
- `list_prompts(server_name)` → `{server_name: [Prompt, ...]}`
- `get_prompt(server_name, prompt_name, arguments)` → `GetPromptResult`

### 3.2 缓存

- `list_resources()` / `list_prompts()` 结果缓存到 `_resources` / `_prompts`，避免重复请求。
- `read_resource()` / `get_prompt()` 不做长期缓存（可能变化），但调用失败不致命。

### 3.3 错误处理

- server 未连接 → 返回空列表 / 抛出 `RuntimeError`。
- 调用失败 → 抛出异常，由调用方（Runner / tool）捕获并记录 warning。

---

## 4. 涉及文件

- `src/openagents_orchestration/tools/mcp/client.py` —— 增加 resources/prompts 方法。
- `tests/test_mcp_client.py` —— 增加对应测试。

---

## 5. 验收标准

- [x] `list_resources()` 返回连接 server 的资源列表。
- [x] `read_resource()` 正确转发 URI 并返回结果。
- [x] `list_prompts()` 返回提示模板列表。
- [x] `get_prompt()` 正确转发参数并返回渲染后的提示。
- [x] 全量测试 `uv run pytest tests/ -v` 仍通过。

---

## 6. 本轮结果

- **修改文件**:
  - `src/openagents_orchestration/tools/mcp/client.py`
    - 导入 `ListResourcesResult`, `ReadResourceResult`, `ListPromptsResult`, `GetPromptResult`。
    - 新增 `_resources` / `_prompts` 缓存。
    - 新增 `list_resources()`, `read_resource()`, `list_prompts()`, `get_prompt()`。
    - `close()` 清空 resources/prompts 缓存。
  - `tests/test_mcp_client.py`
    - 新增 `test_manager_lists_and_reads_resources`。
    - 新增 `test_manager_lists_and_gets_prompts`。
- **测试**: `532 passed, 7 skipped`（新增 2 个用例）。
- **Lint**: `ruff check` 通过。

MCP 客户端能力现在覆盖：stdio/SSE 传输、tools、resources、prompts。

---

## 7. 下一轮

**流式输出 + Tool Pre-execution**（最后一轮功能迭代，之后 review 并 goal clear）。

---

## 8. 参考

- `../docs-ref/corecoder_claude_code_method.md`
- `../docs-ref/corecoder_claude_code_phases.md`
- `iteration_002_mcp_client.md`
- `iteration_003_mcp_sse.md`
- MCP Python SDK: https://github.com/modelcontextprotocol/python-sdk

- `../docs-ref/corecoder_claude_code_method.md`
- `../docs-ref/corecoder_claude_code_phases.md`
- `iteration_002_mcp_client.md`
- `iteration_003_mcp_sse.md`
- MCP Python SDK: https://github.com/modelcontextprotocol/python-sdk

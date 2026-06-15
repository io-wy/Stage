# CoreCoder → Claude Code 迭代日志 #005

**日期**: 2026-06-14  
**目标**: 以 CoreCoder 单 Agent 为基线，参考 Claude Code 生产设计，持续迭代优化。  
**本轮主题**: 流式输出 + Tool Pre-execution —— 最后一轮功能迭代。

---

## 1. 本轮启动时的已验证状态

- 迭代 #001：四层上下文压缩。
- 迭代 #002：MCP stdio 客户端 + Runner 集成。
- 迭代 #003：MCP SSE 传输。
- 迭代 #004：MCP resources/prompts。
- 全量测试：`532 passed, 7 skipped`。

剩余最后一项主要差距：**流式输出 + Tool Pre-execution**。

---

## 2. 设计

### 2.1 目标

1. 让 `CoreCoderPattern` 支持 `execute_stream()`，产出 `StreamEvent`。
2. 保留现有 `execute()` API 不变（内部可收集流式事件后返回最终文本）。
3. 实现 Tool Pre-execution：对只读工具在流式解析到完整参数时提前执行，缓存结果。

### 2.2 StreamEvent

沿用 `src/openagents_orchestration/models/stream.py`：

- `text` —— 模型生成的文本片段
- `tool_call_start` / `tool_call_delta` / `tool_call_complete` —— 工具调用流式事件
- `tool_result` —— 工具执行结果
- `error` —— 可恢复错误
- `complete` —— 循环结束

### 2.3 实现策略

为避免大规模重构破坏现有 `execute()`，采用**保守增量**方案：

- `execute()` 保持当前实现不变。
- 新增 `execute_stream()`，内部复用 `_invoke_llm()`（非流式 LLM 调用）产生的事件粒度：
  - 每轮 LLM 返回后，yield `tool_call_start` 和 `tool_call_complete`。
  - 工具调用完成后，yield `tool_result`。
  - 文本响应被接受时，yield `text` + `complete`。
- 同步新增 `_invoke_llm_stream()`：如果底层 client 支持 `complete_stream()`，则逐 chunk 返回；否则退化为调用 `_invoke_llm()` 并一次性返回聚合结果。
- Tool Pre-execution 在 `execute_stream()` 中实现：
  - 使用 `StreamToolCallParser` 解析流式 chunk。
  - 当解析到完整只读 tool_call（`read_file/glob/grep/list_directory/web_search/web_fetch` 且 `concurrency_safe=True`）时，立即异步执行并缓存。
  - 主循环收到正式 tool_calls 时，优先命中缓存，直接 yield `tool_result`。

### 2.4 安全边界

- 只预执行只读、无副作用工具。
- 写工具和 `bash` 绝不预执行。
- 预执行失败不阻塞主循环，正式调用时重试。

### 2.5 涉及文件

- `src/openagents_orchestration/models/stream.py` —— 已存在，本轮使用。
- `src/openagents_orchestration/patterns/stream_parser.py` —— 已存在，本轮完善。
- `src/openagents_orchestration/patterns/corecoder.py` —— 新增 `execute_stream()`、`_invoke_llm_stream()`、工具预执行逻辑。
- `tests/test_streaming.py` —— 新增测试。

### 2.6 验收标准

- [x] `execute_stream()` 能 yield `text/tool_call_start/tool_call_complete/tool_result/complete` 事件。
- [x] `execute()` 行为不变，全量现有测试通过。
- [x] 只读工具在流式解析到完整参数时被预执行，正式调用时命中缓存。
- [x] 写工具不会被预执行。
- [x] 全量测试 `uv run pytest tests/ -v` 仍通过。

---

## 3. 本轮结果

- **新增/使用文件**:
  - `src/openagents_orchestration/models/stream.py` —— `StreamEvent` / `StreamEventType`。
  - `src/openagents_orchestration/patterns/stream_parser.py` —— `StreamToolCallParser` + `PreExecutionCache`。
  - `src/openagents_orchestration/patterns/corecoder.py`
    - 导入流式相关类型。
    - 新增 `_invoke_llm_stream()`：优先使用 `llm_client.complete_stream()`，不支持时退化为非流式并合成 chunk。
    - 新增 `_preexecute_readonly_tool()`：对只读工具做预执行并缓存。
    - 新增 `execute_stream()`：完整 ReAct 循环的 async generator，输出 `StreamEvent`。
    - 工具调用流程：解析 → 预执行只读工具 → dispatch 时命中缓存 → yield `tool_result`。
  - `tests/test_streaming.py` —— 5 个用例覆盖文本流、工具事件、写工具不预执行、缓存、execute 兼容。
- **测试**: `537 passed, 7 skipped`（新增 5 个流式用例）。
- **Lint**: `ruff check` 通过。

### 使用方式

```python
async for event in pattern.execute_stream():
    if event.type == StreamEventType.text:
        print(event.text, end="")
    elif event.type == StreamEventType.tool_result:
        print(f"[{event.tool_name}] -> {event.result}")
    elif event.type == StreamEventType.complete:
        print(f"\nDone: {event.text}")
```

`execute()` 仍返回最终 `str`，与之前完全一致。

---

## 4. 当前差距状态（review 前）

- ✅ 四层上下文压缩
- ✅ MCP stdio + SSE + resources/prompts
- ✅ 流式输出 + Tool Pre-execution（基础版）
- 🔄 可扩展：真正的逐 token 流式、SSE/Streamable HTTP 完整支持、多模态、Worktree

---

## 5. 参考

- `../docs-ref/corecoder_claude_code_method.md`
- `../docs-ref/corecoder_claude_code_phases.md`
- `iteration_001_context_compression.md`
- `iteration_002_mcp_client.md`
- `iteration_003_mcp_sse.md`
- `iteration_004_mcp_resources.md`
- `src/openagents_orchestration/patterns/corecoder.py`

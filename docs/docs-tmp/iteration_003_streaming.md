# CoreCoder → Claude Code 迭代日志 #003

**日期**: 2026-06-14  
**目标**: 以 CoreCoder 单 Agent 为基线，参考 Claude Code 生产设计，持续迭代优化。  
**本轮主题**: 流式输出 + Tool Pre-execution —— 让 CoreCoder 单 Agent 在交互感觉上接近 Claude Code。

---

## 1. 本轮启动时的已验证状态

- 迭代 #001：补齐上下文压缩 Deduplicate 层。
- 迭代 #002：MCP stdio 客户端集成，Runner 可从 `agent.json` 加载 MCP server 并把工具注入 agent。
- 全量测试：`529 passed, 7 skipped`。

剩余主要差距：
1. **流式输出 + Tool Pre-execution**（本轮目标）。
2. MCP SSE / Streamable HTTP、resources/prompts。
3. 多模态 / Worktree。

---

## 2. 为什么本轮做流式

Claude Code 的端到端流式把模型 token、工具事件实时渲染到终端，避免用户盯着空白屏幕等待 5–30 秒。更重要的是，Claude Code 在**模型还在输出时**就提前启动只读工具（tool pre-execution），把 ~1 秒的工具延迟隐藏到模型生成窗口内。

CoreCoder 当前 `CoreCoderPattern.execute()` 返回最终 `str`，中间过程对外不可见。本轮目标：

1. 把循环改造为 async generator，输出 `StreamEvent`。
2. 保留非流式 API `execute()`（内部收集事件后返回最终文本，避免破坏现有调用方）。
3. 实现 Tool Pre-execution：流式解析到完整只读 tool call 时立即执行，结果缓存，模型正式调用时直接返回。

---

## 3. 设计

### 3.1 StreamEvent 类型

```python
class StreamEventType(str, Enum):
    text = "text"                    # 模型生成的文本片段
    tool_call_start = "tool_call_start"  # 模型开始调用某个工具
    tool_call_delta = "tool_call_delta"  # 工具参数还在流式生成
    tool_call_complete = "tool_call_complete"  # 工具参数完整
    tool_result = "tool_result"      # 工具执行结果
    error = "error"                  # 可恢复错误
    complete = "complete"            # 循环结束，附带 summary
```

存储于 `src/openagents_orchestration/models/stream.py`。

### 3.2 流式执行方法

- 新增 `CoreCoderPattern.execute_stream()`：`AsyncGenerator[StreamEvent, None]`。
- 现有 `execute()` 改为：
  ```python
  async def execute(self) -> str:
      final_text = ""
      async for event in self.execute_stream():
          if event.type == StreamEventType.complete:
              final_text = event.text or ""
      return final_text
  ```

### 3.3 Tool Pre-execution

- 新增 `src/openagents_orchestration/patterns/stream_parser.py`：`StreamToolCallParser`。
  - 累积 `content_block_start` + `content_block_delta` 中的 `input_json_delta`。
  - 当某索引的工具参数 JSON 完整可解析，且工具名为只读工具（`read_file`, `glob`, `grep`, `list_directory`, `web_search`, `web_fetch`）时，触发预执行。
  - 预执行结果缓存到 `ctx.scratch["_preexecuted"]`。
- 在 `execute_stream()` 中，模型正式完成 tool_calls 后：
  - 对每个 tool_call，先检查缓存；命中则直接 yield `tool_result`，跳过实际调用。
  - 未命中则正常 `_dispatch_tool_calls()`。

### 3.4 安全边界

- 只预执行标记为 `concurrency_safe=True` 且无副作用的只读工具。
- 写工具（`write_file`, `edit_file`, `apply_patch`, `bash`）绝不预执行。
- 预执行失败不阻塞主循环，仅记录错误，主循环收到正式 tool_call 时会正常重试。

### 3.5 Hook 兼容

- `pattern.before_llm` 仍可在流式调用前修改 messages/tools。
- `tool.before_invoke` 对预执行同样生效；若 Hook 阻止，则预执行结果被标记为 blocked，正式调用时仍会被阻止。

---

## 4. 涉及文件

- 新增 `src/openagents_orchestration/models/stream.py`
- 新增 `src/openagents_orchestration/patterns/stream_parser.py`
- 修改 `src/openagents_orchestration/patterns/corecoder.py`
  - 拆分 `_execute_one_turn()` 为可流式版本。
  - 新增 `execute_stream()`。
  - 调整 `execute()` 为包装器。
- 新增 `tests/test_streaming.py`

---

## 5. 验收标准

- [ ] `execute_stream()` 能 yield 文本片段、tool_call 事件、tool_result 事件、complete 事件。
- [ ] `execute()` 返回的最终文本与流式收集结果一致。
- [ ] 只读工具在流式解析完成时被预执行，正式调用时命中缓存。
- [ ] 写工具不会被预执行。
- [ ] 全量测试 `uv run pytest tests/ -v` 仍通过。

---

## 6. 参考

- `../docs-ref/corecoder_claude_code_method.md`
- `../docs-ref/corecoder_claude_code_phases.md`
- `iteration_001_context_compression.md`
- `iteration_002_mcp_client.md`
- `src/openagents_orchestration/patterns/corecoder.py`
- Claude Code streaming/tool pre-execution 分析： https://github.com/Windy3f3f3f3f/how-claude-code-works

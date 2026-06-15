# CoreCoder → Claude Code 迭代日志 #001

**日期**: 2026-06-14  
**目标**: 以 CoreCoder 单 Agent 为基线，参考 Claude Code 生产设计，持续迭代优化。  
**本轮主题**: Context Compression —— 补齐四层渐进压缩中的 Deduplicate 层。

---

## 1. 本轮启动时的已验证状态

通过 `uv run pytest tests/ -v` 全量测试：**516 passed, 7 skipped**，无回归。

已落地的主要能力（与 `../docs-ref/corecoder_claude_code_phases.md` 对照）：

| 阶段 | 能力 | 状态 |
|------|------|------|
| Phase 0 | 编辑/探索/通信工具集（含 apply_patch、semantic_edit、sub_agent 等） | ✅ |
| Phase 1 | 编辑失败降级链、验证闭环（pending verification + nudge/enforcement）、Bash 权限门、失败恢复 | ✅ |
| Phase 2 | 动态片段分类（`__DYNAMIC_BOUNDARY__` / `__CATEGORY_BOUNDARY__`）、Spill-to-file、3 层上下文压缩 | ✅ |
| Phase 3 | coder 级 sub-agent（≤2 层）、Hooks 管线 | ✅ |
| Phase 4 | 权限模式、Plan Mode | ✅ |

最大剩余差距：
1. **MCP 客户端**（外部工具扩展）。
2. **流式输出 + Tool Pre-execution**（UX 与延迟）。
3. **四层渐进压缩中的 Deduplicate 层**（当前只有 Snip → Summarize → Hard-collapse）。
4. **多模态 / Worktree**。

---

## 2. 为什么先补 Deduplicate

Claude Code 的上下文工程是其能处理长任务的核心基础设施之一。根据 `how-claude-code-works` 与 `Dive into Claude Code` 的分析，Claude Code 在模型调用前会跑多层 **context shaper**，其中一层专门负责 **去重**（移除重复的 `git status`、重复的 `read_file` 结果等），在 snip 之后、LLM 摘要之前执行。

CoreCoder 当前的 `CompressingContextAssembler` 已经是 3 层：

1. **Snip**（≥50% 预算）：长工具输出头尾截断。
2. **Summarize**（≥70% 预算）：LLM 摘要早期对话。
3. **Hard-collapse**（≥90% 预算）：丢弃中间消息。

缺失的 **Deduplicate** 层可以在不调用 LLM 的情况下，以确定性方式释放大量上下文空间，且实现自包含、测试友好、不影响现有接口。因此选为迭代 #001 的目标。

---

## 3. 设计

### 3.1 触发时机

在 `assemble()` 中插入到 Snip 之后、Summarize 之前：

```
Layer 1: Snip
Layer 2: Deduplicate  ← 新增
Layer 3: Summarize
Layer 4: Hard-collapse
```

触发阈值默认 0.6（可配置），即当 snip 后 token 占用仍 ≥60% 预算时启动去重。

### 3.2 去重策略

保守策略，避免误删关键信息：

- **仅处理 `role == "tool"` 或包含 `tool_result` block 的消息**。
- **仅对内容长度超过 `dedup_min_bytes` 的结果去重**（短结果去重收益低，风险高）。
- **精确匹配**：同一 tool name + 相同规范化内容（去除首尾空白、统一换行符）视为重复。
- **时间窗口**：只与最近 `dedup_lookback` 条消息比较，避免跨太远历史误合并。
- **替换文案**：`[Duplicate {tool_name} result omitted; identical to result at index {idx}]`。

### 3.3 配置项

在 `CompressingContextAssembler.Config` 增加：

```python
dedup_enabled: bool = True
dedup_threshold: float = 0.6
dedup_min_bytes: int = 200
dedup_lookback: int = 20
```

### 3.4 指标

`metadata` 中新增：

```python
"deduped_messages": int   # 本轮被合并的重复消息数
```

---

## 4. 涉及文件

- `src/openagents_orchestration/context.py` —— 增加 dedup 层逻辑与配置。
- `tests/test_context_assembler.py` —— 新增单元测试（当前该模块无测试）。

---

## 5. 验收标准

- [x] `_deduplicate_transcript` 能识别连续重复的 tool_result 并替换为占位。
- [x] 不删除 user/assistant 文本消息。
- [x] 短结果（< `dedup_min_bytes`）不被去重。
- [x] `metadata["deduped_messages"]` 正确计数。
- [x] 全量测试 `uv run pytest tests/ -v` 仍通过。

---

## 6. 本轮结果

- **新增文件**: `tests/test_context_assembler.py`（7 个用例覆盖 dedup 核心行为）。
- **修改文件**: `src/openagents_orchestration/context.py`
  - `Config` 增加 `dedup_enabled / dedup_threshold / dedup_min_bytes / dedup_lookback`。
  - `assemble()` 在 Snip 与 Summarize 之间插入 `_deduplicate_transcript()`。
  - 新增 `_extract_tool_result_content()` 与 `_normalize_for_dedup()` 辅助函数。
  - `metadata` 新增 `deduped_messages`。
- **测试**: `523 passed, 7 skipped`（新增 7 个用例）。
- **Lint**: `ruff check` 通过。

### 压缩层现在对齐 Claude Code 四层模型

```
Layer 1: Snip          (≥50% 预算)  ✅ 已有
Layer 2: Deduplicate   (≥60% 预算)  ✅ 本轮新增
Layer 3: Summarize     (≥70% 预算)  ✅ 已有
Layer 4: Hard-collapse (≥90% 预算)  ✅ 已有
```

---

## 7. 下一轮候选方向

按优先级排序：

1. **流式输出 + Tool Pre-execution**：对“感觉快”影响最大，但需要改造 `CoreCoderPattern.execute()` 的返回类型，风险高于 dedup。
2. **MCP 客户端**：能力补齐最大，但依赖外部 server 与新增依赖（`mcp` SDK）。
3. **上下文压缩：Collapse 层细化**：把 Hard-collapse 前的“折叠早期不活跃段落”做得更细。

本轮完成后将根据剩余时间和测试情况选择。

---

## 8. 参考

- `../docs-ref/corecoder_claude_code_method.md`
- `../docs-ref/corecoder_claude_code_phases.md`
- `src/openagents_orchestration/context.py`
- Claude Code context shapers 分析： https://github.com/Windy3f3f3f3f/how-claude-code-works

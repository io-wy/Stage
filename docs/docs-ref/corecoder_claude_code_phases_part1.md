# CoreCoder 单 Agent 对齐 Claude Code：分阶段详细设计（Part 1 — Phase 0~2）

本文是 `corecoder_claude_code_method.md` 的配套文档，覆盖 Phase 0 工具补齐、Phase 1 执行可信、Phase 2 上下文工程与流式基础。

## Phase 0 — 工具补齐（✅ 已实现）

Claude Code 的常用编辑/探索工具已基本补齐：

- `apply_patch`：多文件 unified-diff，支持模糊匹配，作为 `edit_file` 的批量替代。
- `semantic_edit`：自然语言驱动编辑，作为精确替换失败时的兜底。
- `complete_task`：coder 主动声明完成，返回 summary 与 artifacts。
- `sub_agent`：委派 reviewer/researcher 做独立子任务。
- `run_claude_code`：本地 `claude -p` 兜底，处理复杂重构或环境调试。

### 待做改动

1. **单测补全**
   - `tests/test_apply_patch.py`：覆盖单文件/多文件、模糊匹配、创建新文件、失败回滚。
   - `tests/test_semantic_edit.py`：mock LLM 返回代码块，验证写入与 diff 生成。
   - `tests/test_complete_task.py`：验证 state 标记与 artifacts 记录。
   - `tests/test_sub_agent.py`：验证 runner 复用路径和 standalone 路径。
2. **统一 dirty_files 追踪**
   - 确保 `write_file`、`edit_file`、`apply_patch`、`semantic_edit` 都写入 `ctx.scratch["dirty_files"]`。
   - 在 `CoreCoderPattern._writeback_context()` 中把 `dirty_files` 写回 caller。
3. **解除 sub_agent 单层限制**
   - 在 `sub_agent.py` 中记录 `__sub_agent_depth__`，允许 ≤2 层。
   - 在 schema description 中明确限制。

### 验收标准

- `uv run pytest tests/test_apply_patch.py tests/test_semantic_edit.py tests/test_complete_task.py tests/test_sub_agent.py -v` 全部通过。
- 任意编辑工具执行后，`ctx.scratch["dirty_files"]` 包含被修改文件的绝对路径。

## Phase 1 — 执行可信（✅ 已实现）

目标：让 CoreCoder 的每一次编辑都有高概率成功，失败后能自动诊断并恢复。

### 1.1 编辑可靠性增强

Claude Code 的 `Edit` 优先使用 **search-and-replace**，因为 blast radius 最小。CoreCoder 已有精确替换，但 LLM 常因 `old_string` 不精确失败。

#### 设计

- 在 `CoreCoderPattern` 中维护 `__edit_failure_chain__`：当 `edit_file` 失败，下轮自动建议模型改用 `apply_patch`；`apply_patch` 失败再建议 `semantic_edit`；三层都失败返回结构化错误：

```json
{
  "stage": "edit_failed",
  "attempts": ["edit_file", "apply_patch", "semantic_edit"],
  "suggestion": "read more context or use write_file"
}
```

- `semantic_edit` 作为兜底时，要求 LLM 输出完整文件，避免局部修改遗漏上下文。

#### 涉及文件

- `src/openagents_orchestration/tools/corecoder/edit_file.py`
- `src/openagents_orchestration/tools/corecoder/apply_patch.py`
- `src/openagents_orchestration/tools/corecoder/semantic_edit.py`
- `src/openagents_orchestration/patterns/corecoder.py`

### 1.2 自动验证闭环

现状是“提示模型去跑测试”，但模型可能不听。改进为：**工具层主动触发验证**。

#### 设计

1. 编辑工具执行成功后，自动把项目类型推荐的 `test_cmd`/`lint_cmd` 写入 `__pending_verification__`：

```json
{
  "files": ["src/foo.py"],
  "tool": "edit_file",
  "step": 5,
  "attempts": 0,
  "test_cmd": "uv run pytest tests/test_foo.py -v",
  "lint_cmd": "uv run ruff check src/foo.py"
}
```

2. `CoreCoderPattern` 在下一轮检查 `__pending_verification__`：
   - 如果存在未验证的编辑，强制模型先调用 `bash` 跑测试/lint（可让模型选择具体命令，但必须执行验证）。
   - 验证完成后清除 pending；失败则保留并注入结构化错误。
3. 测试失败后解析输出：
   - 提取失败文件、行号、错误类型。
   - 把 `{file, line, error}` 注入下轮 prompt，要求模型先 `read_file` 定位再修复。
4. 连续 2 轮验证失败且无法自动修复 → 调用 `ask_human`。

#### 涉及文件

- `src/openagents_orchestration/patterns/corecoder.py`
- `src/prompts/dynamic.py`
- 新增 `src/openagents_orchestration/tools/corecoder/test_parser.py`

### 1.3 Bash 安全增强

Claude Code 的 Bash 安全有 5 层。CoreCoder 当前只有基础关键词拦截。

#### 设计

1. **规则引擎**：在 `bash_tool.py` 中增加 `allowlist/denylist` 配置，支持按命令前缀、参数模式匹配。
2. **AST 预检**：对危险命令（`rm`、`dd`、`>` 重定向到系统路径）做词法级检查，识别 `rm -rf /`、`curl | sh`、`git reset --hard` 等。
3. **破坏性操作确认**：默认抛出需要用户确认的 `PermissionRequired` 错误，由 harness 或 `ask_human` 处理。
4. **cwd 逃逸检测**：禁止命令通过 `..` 逃出项目根目录（`agent.json` 可配置 `project_root`）。

#### 涉及文件

- `src/openagents_orchestration/tools/corecoder/bash_tool.py`
- `agent.json` 增加可选 `project_root`、`bash_rules` 配置

### 1.4 失败恢复策略

Claude Code 有 7 种继续策略。CoreCoder 先实现 4 种：

- `retry`：工具报错可重试（如 LLM 超时、网络抖动）。
- `replan`：连续 2 次工具失败或验证失败，调用 `_generate_plan` 重新生成计划。
- `delegate`：任务过大或需要独立上下文，调用 `sub_agent`。
- `ask_human`：无法自动恢复时求助。

#### 设计

- 在 `CoreCoderPattern.execute()` 中维护状态机：
  `__recovery_mode__ ∈ {normal, retrying, replanning, delegated, asking}`。
- 每次工具失败后根据失败类型和重试次数选择策略，而不是简单继续。

## Phase 2 — 上下文工程 + 流式基础（🔄 部分实现）

目标：在长任务中保持上下文窗口高效、相关、可缓存，并为流式体验打基础。

### 2.1 动态片段分类（✅ 已实现）

把 `build_runtime_fragment` 的输出按类别拆成多个 system message：

- `environment`：cwd、git、project type
- `memory`：dirty files、recent thoughts、recent errors
- `plan`：当前 plan、todo progress
- `exploration_cache`：已读文件、已 grep/glob
- `verification`：pending verification

每类加独立 header，模型更容易定位信息。

#### 涉及文件

- `src/prompts/dynamic.py`
- `src/openagents_orchestration/patterns/corecoder.py` 中 `_split_system_prompt` 支持多段动态内容

### 2.2 四层渐进压缩

当消息总 token 接近 `max_input_tokens` 时，按顺序执行：

1. **Snip**：对历史中大段工具输出做头尾截断。
2. **Deduplicate**：检测并合并重复的工具结果（如多次 `git status`）。
3. **Collapse**：把早期不活跃的对话段折叠成摘要，保留在 `ctx.state` 但不送入 LLM；需要时一键展开。
4. **Summarize**：作为最后手段，派生子 agent（reviewer）对早期对话做摘要，替换原历史。

每层执行后检查是否释放足够空间，够了就停止。

#### 涉及文件

- 新增 `src/openagents_orchestration/context/shapers.py`
- 修改 `src/openagents_orchestration/context.py`（`CompressingContextAssembler`）

### 2.3 大输出 spill-to-file（✅ 已实现）

当工具输出超过阈值（如 8K 字符）时：

- 把完整内容写入 `.agent_output/<tool>_<timestamp>.md`。
- 给 LLM 返回摘要 + 文件路径，模型需要时再用 `read_file` 读取。
- 适用于 `bash`、`grep`、`web_fetch`、`sub_agent` 返回的长输出。

### 2.4 流式输出与 Tool Pre-execution（🔄 待实现）

> 反馈：4.1 流式输出和 4.2 tool pre-execution 对“感觉快”至关重要，不依赖扩展架构，建议在 Phase 1 之后尽早落地。

#### 2.4.1 流式输出

Claude Code 的端到端流式把模型 token、工具事件实时渲染到终端，避免用户盯着空白屏幕等待 5-30 秒。

**设计：**

- 将 `CoreCoderPattern.execute()` 从返回 `str` 改为 async generator：`AsyncGenerator[StreamEvent, None]`。
- 定义 `StreamEvent` 类型：
  - `text`: 模型生成的文本 token/片段
  - `tool_call`: 模型决定调用工具
  - `tool_result`: 工具执行结果
  - `error`: 可恢复错误
  - `complete`: 循环结束，附带 summary
- 在 `_invoke_llm()` 中，如果底层 client 支持流式，逐 token yield；否则按整段 yield。
- 上层 runner 负责把事件转发到终端/前端；非流式调用方可用 `''.join([e.text for e in events])` 还原最终文本。

**涉及文件：**

- `src/openagents_orchestration/patterns/corecoder.py`
- `src/openagents_orchestration/models/stream.py`（新增）

#### 2.4.2 Tool Pre-execution

Claude Code 在模型还在生成输出时，就提前解析并启动只读工具，把 ~1 秒工具延迟隐藏到模型生成窗口内。

**设计：**

- 流式解析模型输出：一旦识别到完整的 `tool_use` JSON（OpenAI 格式）或 `<tool_use>` 块（Anthropic 格式），立即判断是否为只读工具。
- 只读工具（`read_file`、`glob`、`grep`、`list_directory`、`web_search`、`web_fetch`）可安全提前执行；写操作（`write_file`、`edit_file`、`apply_patch`、`bash` 带写副作用）必须等待模型输出完整并确认参数后再执行。
- 预执行结果缓存到 `ctx.scratch["_preexecuted"]`；模型正式完成该 tool_call 时直接返回结果，无需二次调用。
- 预执行失败不阻塞主循环，仅记录错误；模型收到错误后可决定重试或换工具。

**涉及文件：**

- `src/openagents_orchestration/patterns/corecoder.py`
- 新增 `src/openagents_orchestration/patterns/stream_parser.py`

**验收标准：**

- 在支持流式 API 的模型上，read/grep/glob 等只读工具的延迟对用户不可感知（工具结果在模型输出结束前已准备好）。
- 写工具不会被预执行，避免参数未完整导致错误副作用。

## Part 1 验收清单

| 阶段 | 验收项 |
|------|--------|
| Phase 0 | 新增工具单测通过；dirty_files 一致；sub-agent 支持 2 层 |
| Phase 1 | 编辑降级链生效；验证闭环强制跑测试；Bash 危险命令被拦截；恢复状态机覆盖 retry/replan/delegate/ask |
| Phase 2 | 长对话（>30 轮）context 可控；动态片段分类清晰；spill-to-file 对大输出生效；流式输出与 tool pre-execution 可用 |

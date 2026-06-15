# CoreCoder → Claude Code 能力对照

本文件记录 CoreCoder 单 Agent 与 Claude Code 原生工具的差距，用于持续迭代。

## 已对齐的核心工具

| Claude Code 工具 | CoreCoder 对应实现 | 状态 | 备注 |
|-----------------|-------------------|------|------|
| `Bash`          | `bash`            | ✅   | 支持危险命令拦截、cwd 记忆、输出截断 |
| `Edit`          | `edit_file`       | ✅   | 精确替换，失败时返回可操作的错误信息 |
| `Write`         | `write_file`      | ✅   | 全文件写入，记录 dirty_files |
| `Read`          | `read_file`       | ✅   | 支持 offset/limit，行号显示 |
| `Glob`          | `glob`            | ✅   | 按 mtime 排序，结果上限 100 |
| `Grep`          | `grep`            | ✅   | 正则搜索，跳过缓存目录 |
| `Think`         | `think`           | ✅   | 显式推理，最近 3 条注入 dynamic prompt |
| `WebSearch`     | `web_search`      | ✅   | DuckDuckGo HTML 接口，无需 API key |
| `WebFetch`      | `web_fetch`       | ✅   | HTML 转 markdown，支持 charset |
| `AskUserQuestion` | `ask_human`     | ✅   | 多选/文本问题，已注册到 coder/reviewer/researcher |
| `LS` / 目录列表  | `list_directory`  | ✅   | 树形展示，支持深度限制 |
| `Skill`         | `run_skill`       | ✅   | 复用项目 skills |
| `ApplyPatch`    | `apply_patch`     | ✅   | unified-diff 多文件/模糊匹配补丁 |
| `SemanticEdit`  | `semantic_edit`   | ✅   | 自然语言驱动编辑，作为 edit_file 失败兜底 |
| `CompleteTask`  | `complete_task`   | ✅   | coder 侧任务完成信号，返回 summary |
| `RunClaudeCode` | `run_claude_code` | ✅   | 调用本地 `claude -p` 作为最终兜底 |

## 部分对齐/有差异的工具

| Claude Code 工具 | CoreCoder 对应实现 | 状态 | 差距 |
|-----------------|-------------------|------|------|
| `Agent`         | `sub_agent` | ⚠️   | 已实现：coder 可 spawn reviewer/researcher 子 agent；但子 agent 不能再 spawn（只能有一层），且 standalone 模式会新建本地 runner |
| `CronCreate/Delete/List` | 系统级 CronCreate | ⚠️   | 通过 harness 提供，不是 agent 工具 |
| `EnterPlanMode/ExitPlanMode` | `enable_planning` + planning phase | ⚠️   | 只有自动 planning，没有交互式 plan approval |
| `EnterWorktree/ExitWorktree` | 未实现 | ❌   | 需要 git worktree 集成 |
| `ReadMcpResourceTool/ListMcpResourcesTool` | 未实现 | ❌   | 需要 MCP 客户端集成 |
| `AnalyzeImage` / 多模态 | 未实现 | ❌   | 依赖模型多模态能力 |
| `Memory` / 跨会话长期记忆 | `CoreCoderMemory` | ⚠️   | 仅保存 dirty_files、cwd、 summaries；没有向量检索或语义记忆 |

## Claude Code Prompt / Context 设计借鉴

Claude Code 的上下文设计有几个值得 CoreCoder 对齐的点：

### 1. 静态 system prompt + 动态上下文分离
- Claude Code 将不可变的身份/规则与每轮变化的上下文分开，便于前缀缓存。
- CoreCoder 已实现：`CORE_PRINCIPLES` 是静态，`__DYNAMIC_BOUNDARY__` 之后是 `build_runtime_fragment` 动态片段。
- **可改进**：静态部分还可以更短、更结构化；动态部分可以按类别分块（environment、git、memory、plan）。

### 2. 工作目录与 git 状态
- Claude Code 每轮都向模型展示 cwd、git branch、dirty files、最近 commit。
- CoreCoder 已实现：cwd、git branch/status、最近 3 条 commits、`git diff --stat`。
- **可改进**：展示 `git diff` 的具体内容摘要、未跟踪文件、stash 列表。

### 3. 最近操作记忆
- Claude Code 会记住用户最近编辑的文件、最近使用的命令、最近的错误。
- CoreCoder 已实现：`CoreCoderMemory` 保存 dirty_files、last_cwd、recent summaries；`scratch` 保存 _file_cache、_grep_cache、_glob_cache、_recent_thoughts。
- **可改进**：持久化最近 N 次 run 的完整摘要；基于文件路径/查询的语义检索。

### 4. 工具结果截断与结构化
- Claude Code 对长输出进行智能截断，保留头尾关键信息。
- CoreCoder 已实现：bash 输出头 6000 + 尾 3000 截断；read_file 支持 offset/limit；tool result 8000 字符上限。
- **可改进**：对 JSON/表格输出做结构化摘要；对错误输出保留完整 traceback。

### 5. 验证闭环
- Claude Code 编辑后强烈建议运行测试/lint。
- CoreCoder 已实现：`__pending_verification__` + verification nudge 自动提示。
- **可改进**：自动识别项目类型后推荐具体命令（已实现 project type detection）；失败后自动解析错误并定位文件。

### 6. 子 Agent 与 MCP
- Claude Code 通过 `Agent` 工具派生子 agent，通过 MCP 连接外部工具。
- CoreCoder 差距最大：coder 不能独立 spawn sub-agent；没有 MCP 客户端。

## Claude Code 工程方法参考

> Claude Code 的 50 万行 TypeScript 里，只有约 1.6% 是 AI 决策逻辑，其余 98.4% 是确定性基础设施（权限、上下文、工具路由、恢复）。

这提示 CoreCoder 的演进重点不是让模型“更聪明”，而是把**循环周围的确定性基础设施**做厚：

1. **简单 ReAct 循环 + 厚重外围** — 保持 `CoreCoderPattern.execute()` 简洁，把压缩、权限、恢复、验证拆成独立模块。
2. **四层渐进压缩** — Snip → 去重 → Collapse → Summarize，而不是单次粗暴截断。
3. **工具预执行与流式** — 模型还在输出时，提前开始读文件/执行只读工具，隐藏延迟。
4. **五层安全** — 权限模式、规则、Bash AST、用户确认、Hook。
5. **验证即核心** — 编辑后自动跑测试；失败后自动诊断、重试。
6. **子 Agent / Skills / MCP 统一扩展** — 通过相同接口注册，复用同一安全与执行管线。

详见 `corecoder_claude_code_method.md` 的完整迭代设计。

## Claude Code 工作流模式参考

1. **Every turn: tool or finish** — CoreCoder 已实现：text-only 响应会被拒绝或要求 finalize。
2. **Read before write** — prompts/core.py 第 3 条强制要求。
3. **Verify after edit** — `__pending_verification__` + verification nudge 自动提示跑测试。
4. **Parallel tool calls** — CoreCoderPattern 已支持 concurrency_safe=True 的并行执行。
5. **Project context in system prompt** — dynamic prompt 包含 cwd、git status、recent commits、diff stat、project type、test/lint 命令推荐。
6. **Sub-agents for large tasks** — `sub_agent` 工具已实现：coder 可委派 reviewer/researcher 处理独立子任务，但子 agent 不能再 spawn，保证单一层级。
7. **MCP for custom tools** — 未实现，是最大差距之一。

## 下一步优先级

1. **MCP 集成**：让 CoreCoder 能调用外部 MCP server（最大能力补齐）。
2. **coder 级 sub-agent 解禁**：让 coder 自身能 spawn_agent 处理独立子任务。
3. **Plan mode 交互**：支持显式 EnterPlanMode/ExitPlanMode，plan approval。
4. **Worktree 支持**：与 git worktree 集成，隔离实验性修改。
5. **长期记忆增强**：向量检索、语义搜索历史 run summaries。
6. **多模态**：图片输入、截图分析。

## 参考资料

- Claude Code Tools reference: https://docs.anthropic.com/en/docs/claude-code/tools
- Claude Code CLI reference: https://docs.anthropic.com/en/docs/claude-code/cli-reference
- Claude Code overview: https://docs.anthropic.com/en/docs/claude-code/overview
- How Claude Code Works (source-level analysis): https://github.com/Windy3f3f3f3f/how-claude-code-works
- Dive into Claude Code (arxiv): https://arxiv.org/abs/2604.14228

# 戏台（Xitai）问题与竞品分析

> 分析日期：2026-05-29
> 对比系统：Claude Code / OpenAI Codex CLI / OpenCode / DeepSeek

---

## 一、戏台现有问题

### 架构级（4 个）

#### 1. Director 邮箱单向
`check_messages` 不在 Director 的工具列表里。HealthMonitor 发出的告警和 `ask_human` 的人类回复都进了 StateBoard 邮箱，但 Director 从来不读。告警单向、人机交互单向。

#### 2. 纯内存状态
- `_SessionStore` 是一个内存 dict，崩溃全丢
- StateBoard 在内存，无 checkpoint/restore
- Resident transcript 落盘了但编排状态没有
- 生产环境不可用

#### 3. 并发控制裸奔
`asyncio.gather` 无限制并行，10 个 agent 同时跑时 API 限速打满就全挂。没有 semaphore、没有排队、没有退避。

#### 4. FastAPI 层脱节
`app/` 是一个独立的 CRUD 脚手架（SQLAlchemy + FastAPI），跟编排引擎完全没连上。没有 `/start`、`/status`、`/tasks` 等编排 endpoint。

### 实现级（4 个）

#### 5. CoreCoderPattern 是 copy-fork
从 SDK 复制出来改的，不是 `pip install` 导入。SDK 修 bug 后戏台不会自动同步。当前 SDK 的 ReActPattern 已经删了 legacy 路径，但戏台的 CoreCoderPattern 没有同步这些改动。

#### 6. SDK native pattern 不更新 `ctx.usage`
`_native_react_step` 直接调 `ctx.llm_client.generate()` 而非 `call_llm()`，所以 `RunResult.usage.llm_calls` 始终为 0。戏台自己的 `_invoke_llm` 修复了，但 SDK 的 `ReActPattern`/`ReflexionPattern`/`PlanExecutePattern` 没修。

#### 7. 压缩 token 不准
`CompressingContextAssembler` 按字符截断，不是按 token。不同模型的 tokenizer 不一样（Anthropic 的 tokenizer 和 OpenAI 的不同），字符数跟 token 数误差很大。

#### 8. StateBoard 无审计日志
只看得到当前状态，看不到"谁在什么时候改了啥"。无法回放 Director 的决策过程来 debug。

### 缺口（4 个）

#### 9. human-reply 无轮询
`ask_human` 可以记录问题到 StateBoard，`reply_human` 可以回答问题，但 Director 的 ReAct 循环里没有等着也没有被通知。问完人类继续做别的去了。

#### 10. 无 checkpoint/restore
长时间运行的任务如果中间中断，所有进度丢失。没有在 `pattern.step_finished` 等事件上设 checkpoint。

#### 11. 无集成测试
15 个测试文件全部 mock LLM，没有一条端到端路径用真实 API 跑过。文件系统操作、工具调用链、重试逻辑在真实环境下可能表现不同。

#### 12. 无任务优先级/调度策略
任务按 DAG 顺序执行，没有优先级、没有重试策略配置、没有超时重试的自适应。

---

## 二、竞品架构对比

### 2.1 上下文管理

| 维度 | Claude Code | Codex CLI | OpenCode | 戏台 |
|------|-------------|-----------|----------|------|
| 压缩策略 | 5 层（snip → microcompact → context collapse → autocompact） | Compaction（阈值触发） | Token 预算 + 自动压缩 | 3 层（snip → LLM summary → hard collapse） |
| Token 感知 | ✅ tiktoken-based | ✅ | ✅ | ❌ character-based |
| Prompt Cache | ✅ 静态区全局共享（SERVER_PROMPT_DYNAMIC_BOUNDARY） | ✅ | 有 | 无 |
| Cache 失效处理 | 缓存保温 + 失效熔断（3 次失败后停止） | MCP 枚举顺序不一致导致失效 | — | 无 |
| Auto-compact 阈值 | ~167K tokens for 200K window | — | — | 70%/90% 比例触发 |

**参考：** Claude Code 的 5 层压缩是目前已知最完善的设计，特别是 `SYSTEM_PROMPT_DYNAMIC_BOUNDARY` 让静态区 prompt 跨用户共享 cache，大幅降低成本。

### 2.2 工具系统

| 维度 | Claude Code | Codex CLI | OpenCode | 戏台 |
|------|-------------|-----------|----------|------|
| 文件操作 | 专用工具（Read/Edit/Write/Grep/Glob） | 专用工具 | 专用工具 | 专用工具 |
| Shell 安全 | 8 层（AST 解析 + 50+ 注入检测 + tree-sitter） | macOS: Apple Seatbelt / Linux: Docker + iptables | 命令模式过滤 | 9 个危险 pattern 正则黑名单 |
| 并发控制 | `isConcurrencySafe` 标记，安全工具并行 | 串行 | — | 无 |
| 执行顺序 | 流式（一边接收 tool_use 一边执行） | 串行 | — | 串行 |
| 超时处理 | 每个工具独立超时 | — | — | 全局 step_timeout_ms |
| 重试 | 3 次 token 递增 | — | — | 3 次指数退避 |

**参考：** Claude Code 的 shell 安全是行业标杆。戏台的正则黑名单很脆弱。Codex 用 OS 级沙箱（Apple Seatbelt/Linux Docker）做隔离。

### 2.3 Agent 架构

| 维度 | Claude Code | Codex CLI | OpenCode | 戏台 |
|------|-------------|-----------|----------|------|
| 循环模型 | AsyncGenerator ReAct（1,729 行 `query.ts`） | 双循环（inner/outer） | 主从双层 | Director + CoreCoderPattern |
| 子代理 | AgentTool（隔离上下文，~7× tokens） | Codex 作为 MCP Server | `task` 工具 | `sub_agent` 工具（部分集成） |
| 子代理隔离 | worktree / remote / in-process 三种 | 无 | 独立 Session（Session.create） | 无 |
| 并行策略 | 并行 partition（安全工具组并行） | 无 | 子 agent 可并行 | `asyncio.gather` 无限制 |
| 停止条件 | 5 种（无工具/超步/超长/hook/明确停止） | `done` 事件 | — | max_steps |
| 子代理通信 | sidechain JSONL，只传摘要回 parent | — | Bus 事件系统 | pending_messages 邮箱 |

**参考：** Claude Code 的 sidechain 模式（子代理写独立 JSONL，只传摘要回父代理）是隔离模式的最佳实践。

### 2.4 安全/权限

| 维度 | Claude Code | Codex CLI | OpenCode | 戏台 |
|------|-------------|-----------|----------|------|
| 权限等级 | 7 级（plan → auto → bypassPermissions） | 3 级（Suggest → Auto Edit → Full Auto） | edit/bash/webfetch 细粒度 | 无 |
| ML 分类器 | `yoloClassifier`（双阶段：fast-filter + CoT） | — | — | 无 |
| deny 优先级 | deny > ask > allow | — | — | 无 |
| 沙箱 | ✅ | ✅ | — | ❌ |

**参考：** "Deny > Ask > Allow" 是 Claude Code 安全设计的第一原则。当研究发现用户批准 93% 的审批提示时，Anthropic 的应对是加固沙箱和引入自动分类器，而不是加更多审批对话框。

### 2.5 持久化/恢复

| 维度 | Claude Code | Codex CLI | OpenCode | 戏台 |
|------|-------------|-----------|----------|------|
| 会话存储 | append-only JSONL | — | 三层存储 | 内存 dict |
| Checkpoint | ✅ chain-patched compaction boundaries | ✅ | — | ❌ |
| Resume | ✅ 重建上下文 + 权限重新验证 | — | — | ❌ |
| 审计日志 | 完整 transcript + compaction 标记 | — | — | 无 |
| Session 隔离 | 每 agent 独立 JSONL | — | `opencode-sessions` 四种模式 | 单 dict |

**参考：** Claude Code 用 append-only JSONL 存储 transcript，Chain-patched compaction 标记标明压缩边界，resume 时重建上下文但权限永远重新验证。

### 2.6 人类交互

| 维度 | Claude Code | Codex CLI | OpenCode | 戏台 |
|------|-------------|-----------|----------|------|
| 审批 | 7 级权限每个工具可要求审批 | 3 级审批 | task 工具需确认 | 无 |
| 中断 | hooks, stop on keypress | — | — | 无 |
| 恢复等待 | await user response | — | — | 单向 |
| 审批跟踪 | append-only 审计 | — | — | 无 |

### 2.7 代码库规模参考

| | Claude Code | 戏台 |
|---|---|---|
| 总行数 | ~512,000 行 TypeScript | ~6,149 行 Python |
| 文件数 | ~1,900 | ~51 |
| AI 逻辑占比 | 1.6% | — |
| 测试 | 大量 | 1,789 行 / 15 文件 |

戏台的代码量是 Claude Code 的 ~1.2%，但 Claude Code 有 98.4% 的确定性基础设施（权限、安全、上下文管理、工具路由、恢复逻辑）。

---

## 三、修复优先级

### P0 — 不上生产就不能缺

| # | 问题 | 工作量估计 | 方案 |
|---|------|-----------|------|
| 1 | 权限/安全系统 | 中 | 引入分级的审批模式（suggest/auto/full），加 shell 沙箱 |
| 2 | 持久化 + checkpoint | 大 | StateBoard 落盘（SQLite/JSONL），在 pattern.step_finished 设 checkpoint |
| 3 | Token-aware 压缩 | 中 | 用 tiktoken 或 Anthropic tokenizer 替代字符计数 |

### P1 — 影响开发/测试效率

| # | 问题 | 工作量估计 | 方案 |
|---|------|-----------|------|
| 4 | 并发控制 | 小 | batch spawn 加 `asyncio.Semaphore` |
| 5 | 集成测试 | 中 | 用 mock server 或最小模型测端到端路径 |
| 6 | CoreCoderPattern 同步 | 中 | 要么重新 import 自 SDK，要么正式 fork 并建立同步机制 |

### P2 — 体验提升

| # | 问题 | 工作量估计 | 方案 |
|---|------|-----------|------|
| 7 | Director 邮箱轮询 | 小 | 每轮 show_state 后自动检查 pending_messages |
| 8 | human-reply 通知 | 小 | `reply_human` 时触发 event，Director 的下轮能看到 |
| 9 | StateBoard 审计 | 中 | 所有 mutation 方法加 `log_event` 调用 |
| 10 | FastAPI 集成 | 大 | 加 `/run` `/status` `/tasks` endpoint，连接 StateBoard |

---

## 四、趋势观察

### 4.1 1M 上下文的影响

DeepSeek V4 和 Gemini 2.5 Pro 都支持 1M+ token 上下文。这改变了 Agent 设计的假设：

- **RAG 不再是默认方案** — 如果整个代码库能塞进上下文，RAG 是多余的
- **长历史保留** — 数百次工具调用的完整执行历史可以保持在上下文中
- **压缩策略需要重写** — 1M 上下文下压缩不再是"能省则省"而是"什么时候必须省"

戏台的 3 层压缩是为 128K 上下文设计的，需要适应 1M 时代。

### 4.2 Agent 安全即将成为合规要求

Claude Code 的 7 层安全和 Codex 的 OS 级沙箱反映了趋势：Agent 安全正在从"最佳实践"演变为"合规要求"。戏台的正则黑名单在当前阶段不够。

### 4.3 AI 逻辑 vs 基础设施

Claude Code 只有 1.6% 的代码是 AI 决策逻辑，98.4% 是确定性基础设施。戏台 6,149 行中大部分也是基础设施，但**安全、持久化、上下文管理这三个最大基础设施块几乎是空的**。增加这些不会显著增加 AI 逻辑占比，但会极大提升可靠性。

# AI 部门 Go 后端面试题（Agent 方向 · 基于 Stage）

> 岗位：AI 平台 / Agent  Infra / LLM 应用后端
> 简历关键词假设：多 Agent 编排引擎、Director-Agent、ReAct 循环、常驻 Agent、协作状态机、MCP、流式工具调用、Token 预算、Eval 体系
>
> 下面按 2 小时面试设计，70% Agent/LLM Infra，30% Go 后端工程。

---

## 时间分配

| 阶段 | 时长 | 题号 | 重点 |
|------|------|------|------|
| 项目破冰 | 10 min | Q0 | 讲清楚 Stage 是做什么的 |
| Agent 架构 | 25 min | Q1~Q2 | ReAct、Multi-Agent、控制平面 |
| LLM 调用与工具 | 25 min | Q3~Q4 | Tool use、Function Calling、MCP |
| 常驻 Agent 与通信 | 20 min | Q5~Q6 | Actor、消息队列、并发模型 |
| 记忆与上下文 | 15 min | Q7 | Memory、RAG、Context window |
| 流式与推理优化 | 15 min | Q8 | Streaming、cache、batch |
| 状态机与容错 | 15 min | Q9 | Collaboration SM、retry、DLQ |
| Eval 与安全 | 15 min | Q10 | LLM-as-judge、Sandbox、护栏 |
| AI 后端系统设计 | 15 min | Q11 | Python ↔ Go、服务拆分 |
| Go 工程 | 15 min | Q12 | 并发、测试、可观测 |
| 收尾 | 5 min | — | 反问 |

---

## Q0. 请介绍 Stage：它解决了什么 Agent 场景的问题？你在里面承担什么角色？

**参考答案**
Stage 是一个多 Agent 编排引擎，把复杂软件工程任务拆成子任务，由一个 Director Agent 调度多个 Worker Agent（coder/reviewer/tester）协作完成。

解决的痛点：
- 单个大模型长链路容易"遗忘"、幻觉、输出不稳定。
- 复杂任务需要"规划→执行→检查→修复"闭环，单 Agent 难以自纠。
- 多 Agent 之间需要状态共享、通信、预算控制。

我的角色：
1. 设计 Director-Agent 双层调度：Director 基于 ReAct 读取 StateBoard 做 spawn/replan/finalize 决策。
2. 实现 CoreCoderPattern 的增强：planning mode、streaming parser、tool gating、clarification。
3. 常驻 Agent 消息循环：ResidentAgent 用 asyncio.Queue 做异步收件箱。
4. 协作状态机：producer↔checker 闭环，approve/fix 循环。
5. MCP 接入与工具生态扩展。
6. Eval 体系：HumanEval / SWE-bench-lite / custom harness + LLM-as-judge。

---

## Q1. 对比 ReAct、Plan-and-Execute、Reflexion 三种 Agent 范式，Stage 属于哪一种？如果用 Go 实现会怎么选？

**参考答案**

| 范式 | 特点 | 适用 |
|------|------|------|
| ReAct | 交替 Thought → Action → Observation，适合单 Agent 逐步推理 | 工具调用、探索性任务 |
| Plan-and-Execute | 先全局规划，再分步执行，Planner 不直接碰工具 | 复杂任务、需要稳定性 |
| Reflexion | 执行后自我反思、生成改进建议，循环 | 迭代优化、代码生成 |

Stage 是 **ReAct + Plan-and-Execute 的混合**：
- Director 层：Plan-and-Execute，先做 TaskGraph 分解，再调度。
- Agent 层：ReAct，每个 coder/reviewer 在自己的 loop 里交替推理和工具调用。
- 协作层：Reflexion 思想，checker 反馈 → producer 修复。

Go 实现选择：
- Director 用 Plan-and-Execute：状态明确、可恢复、便于做全局预算。
- Worker 用 ReAct：灵活、工具调用原生。
- 对代码生成类任务，加入 Reflexion 步骤：生成 → 测试 → 反思 → 重试。

---

## Q2. Director 在 Stage 里怎么决定是 spawn、replan、finalize 还是 ask_human？这种决策逻辑放在 Go 里会怎么设计？

**参考答案**
Stage 中 Director 的决策输入：StateBoard.snapshot()，输出是 tool call（show_state、spawn_agent、replan、finalize、ask_human）。

决策依据：
- 所有 task completed → finalize
- 有 task failed：
  - 超时/429 → retry
  - 复杂度太高 → replan 拆子任务
  - 非关键 → skip
  - 需要人确认 → ask_human
- 有 ready task → spawn_agent
- 有协作消息等待处理 → 干预或继续观察

Go 设计：
```go
type DecisionType int
const (
    Spawn DecisionType = iota
    Replan
    Finalize
    AskHuman
    Wait
)

type Director struct {
    board StateBoard
    llm   LLMClient
}

func (d *Director) Decide(ctx context.Context) (*Decision, error) {
    snapshot := d.board.Snapshot()
    if snapshot.AllCompleted() {
        return &Decision{Type: Finalize}, nil
    }
    if failed := snapshot.FailedTasks(); len(failed) > 0 {
        return d.handleFailure(ctx, failed[0])
    }
    if ready := snapshot.ReadyTasks(); len(ready) > 0 {
        return &Decision{Type: Spawn, TaskID: ready[0].ID}, nil
    }
    return &Decision{Type: Wait}, nil
}
```

关键点：
- 决策逻辑要可测试：把 LLM 调用抽象成 interface，测试时喂固定 snapshot。
- 可扩展：用策略模式（Strategy）注入不同决策器（保守型、激进型、预算敏感型）。
- 可解释：Director 的每个决策都要记录 reason，便于审计。

---

## Q3. Stage 的工具调用链路是怎么走的？Go 里实现 LLM Function Calling 要注意什么？

**参考答案**
Stage 链路：
1. LLM 生成带 tool_calls 的 assistant message。
2. Pattern 解析 tool call，找到对应 ToolPlugin。
3. 校验参数 schema（Pydantic / JSON Schema）。
4. 并发或串行执行工具。
5. 把 tool result 以 user/tool 消息塞回 LLM。
6. LLM 下一轮继续，直到没有 tool call。

Go 实现要点：
- Schema 生成：用 Go struct tag + `jsonschema` 库生成 JSON Schema。
- 参数解析：`json.Unmarshal` 到 struct，再用 `validator` 库校验。
- Tool registry：`map[string]Tool`。
- 并发安全：每个 tool 标注是否 `concurrency_safe`，调度器决定是否并行执行。
- 结果截断：长结果要截断到 context window 内，避免 token 爆炸。
- 错误反馈：工具失败时要把 error message 结构化返回给 LLM，让它能自我修复。

```go
type Tool interface {
    Name() string
    Schema() json.RawMessage
    Invoke(ctx context.Context, args json.RawMessage) (Result, error)
    Spec() ExecutionSpec
}
```

---

## Q4. MCP（Model Context Protocol）在 Stage 里怎么接入的？Go 后端做 MCP client/server 会怎么设计？

**参考答案**
Stage 接入方式：
- `agent.json` 顶层配置 `mcp_servers`。
- `OrchestratorRunner` 启动时连接 MCP server。
- `McpClientManager` 维护 stdio/SSE 连接。
- `build_mcp_tools()` 把 MCP tool 包装成内部 Tool 接口。

Go 设计：
- Client：
  ```go
  type MCPClient interface {
      Initialize(ctx context.Context) (*InitializeResult, error)
      ListTools(ctx context.Context) ([]Tool, error)
      CallTool(ctx context.Context, name string, args map[string]any) (CallResult, error)
      Close() error
  }
  ```
- Transport：stdio（启动子进程）、SSE、HTTP+JSON-RPC。
- Server：
  - 用 `net/rpc` 或 gRPC 实现 JSON-RPC 2.0。
  - 提供 `tools/list`、`tools/call`、`resources/list`、`prompts/list`。
  - 每个 tool 用 interface 注册，类似内部 Tool 系统。
- 生命周期：MCP server 可能 crash，client 要有健康检查、重连、连接池。

---

## Q5. ResidentAgent 是常驻内存的，Python 用 asyncio.Queue。如果 Go 实现常驻 Agent，每个 Agent 一个 goroutine 吗？怎么管理生命周期？

**参考答案**
是的，每个 ResidentAgent 一个 goroutine 是最自然的模型：

```go
type ResidentAgent struct {
    id      string
    inbox   chan Message
    ctrl    chan ControlMsg
    state   AgentState
    llm     LLMClient
    tools   ToolRegistry
    done    chan struct{}
}

func (a *ResidentAgent) Run(ctx context.Context) {
    for {
        select {
        case msg := <-a.inbox:
            a.handleMessage(ctx, msg)
        case ctrl := <-a.ctrl:
            if ctrl == Shutdown {
                a.drainInbox()
                close(a.done)
                return
            }
        case <-ctx.Done():
            close(a.done)
            return
        }
    }
}
```

生命周期管理：
- 启动：`go agent.Run(ctx)`。
- 停止：发送 Shutdown 控制消息，或 cancel ctx。
- drain：停止前处理 inbox 中剩余消息或标记为失败。
- supervisor：一个 supervisor goroutine 监听所有 Agent 的 done channel，失败时重启或上报。
- 避免泄漏：每个 goroutine 必须有退出路径；避免阻塞在 `inbox` 或 LLM 调用上。

---

## Q6. Agent 间通信用什么协议？如果跨机器部署，Go 会怎么设计这个协议？

**参考答案**
Stage 里：Agent A 把消息写入 StateBoard._pending_messages，Agent B 调用 check_messages 拉取。是共享内存 + 拉模式。

跨机器部署需要：
- 消息格式：protobuf / JSON，包含：
  - message_id
  - correlation_id
  - from_agent / to_agent
  - task_id
  - payload
  - timestamp
  - ttl
- 传输层：NATS / Redis Streams / Kafka。
- 投递语义：at-least-once + 幂等消费（message_id 去重）。
- 顺序：同一个 sender→receiver 的消息保证顺序。
- DLQ：消费失败 N 次后入死信队列，人工介入。

Go 实现：
```go
type MessageBus interface {
    Publish(ctx context.Context, msg Message) error
    Subscribe(ctx context.Context, agentID string, handler Handler) error
    Close() error
}
```

---

## Q7. Agent 的记忆系统怎么设计？Stage 里怎么处理短期记忆和长期记忆？RAG 在这里有什么用？

**参考答案**
Stage 里的记忆：
- 短期记忆：transcript（对话历史）、scratch（临时上下文）、StateBoard 中的最近事件。
- 长期记忆：mailbox/memory.py、持久化的 sessions、residents 的 transcript。

完整记忆系统设计：
- **短期记忆**：放在 Agent 进程的内存或 Redis，快速访问，有 TTL。
- **长期记忆**：写入向量数据库（pgvector / Milvus / Chroma）+ 关系数据库。
- **RAG 流程**：
  1. 用户输入 → embedding。
  2. 从向量库检索相关历史记忆 / 文档。
  3. 把检索结果注入 system prompt 或 context。
  4. LLM 生成回答。
- **记忆压缩**：长对话超过窗口时，做摘要（summarization）并存入长期记忆。

Go 实现要点：
- Embedding 服务异步调用 OpenAI / 本地模型。
- 向量检索用专门的 vector DB client。
- 对实时性要求高的，用 Redis 缓存最近 N 条记忆。

---

## Q8. corecoder 的 streaming parser 是做什么的？Go 里处理 LLM streaming 要注意什么？

**参考答案**
streaming parser 作用：
- LLM 流式返回 delta，工具调用参数可能跨多个 chunk。
- parser 边读边缓存 partial JSON，等完整 tool call 出现后再 dispatch。
- 同时emit `StreamEvent` 给上层做实时展示或日志。

Go 处理 streaming 要点：
- 用 `bufio.Scanner` 读 SSE 行，解析 `data:` 字段。
- 把解析结果发到 `chan StreamEvent`。
- consumer 用 select 监听 event 和 `ctx.Done()`，防止 goroutine 泄漏。
- 对 tool call 参数做 partial JSON 累积；出现完整 JSON 时 unmarshal。
- 背压：channel 带 buffer，或做 token bucket rate limit。
- 超时：设置 `http.Client.Timeout` 或 context deadline，避免 hanging connection。

---

## Q9. 协作状态机在 Stage 里怎么工作？approve/fix 循环在 Go 里怎么保证状态一致性？

**参考答案**
Stage 协作状态机：
- 状态：idle → assigned → in_review → approved → completed
- 也可能：in_review → fix_needed → assigned（循环）
- 触发：producer 提交 artifact → checker review → approve / request_fix

Go 状态一致性保证：
- StateMachine 内部用 `sync.Mutex` 或 `sync.RWMutex`。
- 每次状态转移先写 WAL / event log，再改内存状态。
- 只有状态转移成功后才触发下一步动作（spawn next agent / send message）。
- 幂等：重复收到同一事件不会导致错误转移（用 event_id 去重）。
- 并发控制：同一个 task 同时只能有一个 Agent 修改状态，用 task-level mutex。

---

## Q10. Stage 有 Eval 体系，也有 agent 调用 bash/write_file 的安全风险。AI 后端怎么保证 Agent 输出可靠、执行安全？

**参考答案**
可靠性（Eval）：
- 单元测试：HumanEval、SWE-bench-lite、custom harness。
- LLM-as-judge：用独立 judge 模型打分。
- 多投票：多个 reviewer 投票，降低单一模型偏见。
- 回归测试：每次改动后跑 eval，防止能力下降。

安全性：
- 沙箱：Agent 的 bash/write_file 在容器 / seccomp / chroot 中执行。
- Allowlist：允许执行的命令白名单，禁止 `rm -rf /`、`curl | bash` 等。
- 权限模式：read-only、prompt（需确认）、auto。
- 审计日志：每个工具调用记录 who/when/what/result。
- 输出校验：文件写入后 read_file 验证；bash 输出检查敏感信息泄露。
- 网络隔离：Agent 访问外网需显式授权。

Go 实现：
- 用 `os/exec` + 容器运行时（firecracker/gvisor）做沙箱。
- 用 OPA / 自定义规则引擎做命令 allowlist。
- 用 OpenTelemetry 记录每个 tool call 的 span。

---

## Q11. 如果要把 Stage 从 Python 迁移成 AI 后端的 Go 服务，你会怎么拆分服务？Python 和 Go 怎么共存？

**参考答案**
服务拆分：
- `orchestrator`（Go）：Director 调度、StateBoard 管理、任务分解、预算控制。
- `agent-worker`（Go/Python）：实际执行 ReAct loop 的 Worker。可以 Go 化，也可以保留 Python 做 LLM 推理代理。
- `llm-gateway`（Go）：统一 LLM 调用、缓存、限流、fallback、metrics。
- `mcp-registry`（Go）：MCP server 发现、健康检查、工具转换。
- `state-store`（PostgreSQL + Redis）：持久化 StateBoard、session、memory。
- `eval-service`（Go + Python）：跑测试 harness、LLM judge。

Python 与 Go 共存：
- **推理留在 Python**：复杂模型推理、embedding、vLLM 等用 Python sidecar，Go 通过 gRPC/HTTP 调用。
- **编排和调度 Go 化**：并发、网络、状态管理用 Go 更适合。
- **Bridge 模式**：Go orchestrator 调 Python agent-worker，逐步替换。
- 共享协议：protobuf + gRPC，定义 Task、Message、ToolCall、State 等实体。

---

## Q12. Go 后端在面试里常被问的工程题：给 Agent 系统设计一个可测试的 LLM client 层。

**参考答案**
```go
type LLMClient interface {
    Generate(ctx context.Context, req *GenerateRequest) (*GenerateResponse, error)
    GenerateStream(ctx context.Context, req *GenerateRequest) (<-chan StreamChunk, error)
}

type GenerateRequest struct {
    Messages []Message
    Tools    []ToolSchema
    Model    string
    MaxTokens int
}
```

测试：
```go
type mockLLM struct {
    responses []GenerateResponse
    idx int
}

func (m *mockLLM) Generate(ctx context.Context, req *GenerateRequest) (*GenerateResponse, error) {
    if m.idx >= len(m.responses) {
        return nil, errors.New("no more responses")
    }
    r := m.responses[m.idx]
    m.idx++
    return &r, nil
}
```

优点：
- Director、Pattern、Tool 都依赖 interface，不依赖真实 API。
- 可以构造固定 tool call 序列，验证编排流程。
- 可以模拟失败、超时、stream reset，测试容错。

---

## 加分追问

1. **如何解决 LLM 的 context window 不够大？**
   - 答：分层摘要、RAG、只传相关 tool schema、事件归档。

2. **如何降低 LLM 调用成本？**
   - 答：prompt caching、结果缓存、tool 选择器、模型降级（gpt-4 → gpt-3.5）、批量请求。

3. **Agent 产生幻觉 tool call 怎么办？**
   - 答：schema 校验、参数类型检查、tool 执行前再次确认、fallback 到 ask_human。

4. **怎么做 Agent 的版本管理和 A/B 测试？**
   - 答：Agent 配置化、不同版本跑不同 eval、影子模式（shadow mode）对比输出。

5. **多模态 Agent 怎么支持图片/音频？**
   - 答：Message 里支持 multi-part content；tool 返回二进制时做 base64 / URL 引用；Go 负责路由，Python sidecar 做实际编码识别。

---

## 面试建议

- 开场 2 分钟把 Stage 的"导演-戏子-戏台"讲清楚，让面试官快速建立画面。
- 每道题都先讲 Stage 里的真实做法，再讲"如果迁移到 Go 我会怎么做"。
- 重点准备 Q1、Q3、Q5、Q7、Q11，这几道最能体现 AI 后端深度。
- 准备 1 个排查故事：比如 coder agent 在 Stage 里遇到 stream reset，怎么通过事件时间线定位问题。
- 对不确定的 AI 概念诚实兜底，但要把思考路径说出来。

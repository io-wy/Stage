# 基于 Stage 项目的 Go 后端深度面试题

> 假设简历上关于 Stage 只写了 4~5 行，例如：
>
> "戏台（Stage）：多 Agent 编排引擎，基于 Director-Agent 架构实现任务分解与调度；使用 asyncio 实现常驻 Agent 消息循环和协作状态机；支持 MCP 插件扩展、流式工具调用、持久化恢复与全局 Token 预算控制。"
>
> 下面按照 2 小时面试设计，覆盖简历关键词 + 面试官自然拓展的 Go 后端相关技术。

---

## 时间分配与面试节奏

| 阶段 | 时长 | 题号 | 目的 |
|------|------|------|------|
| 开场 + 项目介绍 | 10 min | Q0 | 考察表达、项目 ownership |
| 架构与设计 | 25 min | Q1~Q2 | 系统思维、抽象能力 |
| 并发与调度 | 25 min | Q3~Q4 | Go 并发核心、Actor/管道模型 |
| 状态与一致性 | 20 min | Q5 | 状态机、事务、容错 |
| 错误处理与重试 | 15 min | Q6 | Go 错误处理哲学 |
| 流式与网络 | 15 min | Q7 | HTTP/2、SSE、背压 |
| 插件与扩展性 | 15 min | Q8 | 接口设计、依赖注入 |
| 测试与质量 | 15 min | Q9 | 可测试架构、Mock |
| 可观测性与性能 | 15 min | Q10 | Metrics、Tracing、Budget |
| 系统设计/迁移 | 15 min | Q11 | 把 Python 系统迁移到 Go |
| 收尾 | 5 min | — | 反问 |

---

## 题目与参考答案

### Q0. 请用 2~3 分钟介绍一下 Stage 这个项目，它解决了什么问题？你在里面负责什么？

**参考答案：**
Stage（戏台）是一个多 Agent 编排引擎。核心思路是把复杂任务交给一个"导演"（Director）Agent 做全局调度，再由多个"戏子"（Agent）执行具体子任务。它解决的是：当单个大模型无法可靠完成复杂工程任务时，如何通过多 Agent 分工、协作、验证来提升成功率。

我在里面负责：
1. 设计并实现 Director-Agent 双层调度：Director 基于 ReAct 循环读取 StateBoard 状态并决策；Agent 使用 CoreCoderPattern 执行具体任务。
2. 常驻 Agent（ResidentAgent）消息循环：用 asyncio.Queue 实现异步收件箱，支持 Agent 间消息通信。
3. 协作状态机与全局预算：实现 producer↔checker 闭环，以及跨 Agent 的 Token 预算控制。
4. 工具生态：list_directory、think、sub_agent、web_fetch、approve_plan 等新工具，以及 MCP 插件接入。
5. 测试与可观测性：Mock LLM 测试体系、事件时间线、Metrics 上报。

---

### Q1. Stage 里 Director-Agent 架构和你做过的 Go 后端服务有什么共通点？如果把它翻译成 Go，整体服务怎么划分？

**参考答案：**
共通点：
- **控制平面 vs 数据平面**：Director 类似 Kubernetes 的 Controller / Scheduler，Agent 类似 Worker。Go 后端也常做这种拆分，比如 API Gateway + Worker Pool。
- **状态中心**：StateBoard 是 Single Source of Truth，类似 etcd / 配置中心 / 任务状态数据库。
- **事件驱动**：Agent 状态变化通过事件总线传播，和 Go 里用 channel / message queue 解耦类似。

Go 服务划分建议：
- `orchestrator`：接收 objective，维护 Project/StateBoard，做决策循环。
- `agent-worker`：无状态 Worker，执行具体任务，通过 gRPC/HTTP 被调度。
- `state-store`：StateBoard 持久化，可用 PostgreSQL + Redis 缓存。
- `mcp-registry`：MCP server 注册与工具发现。
- `event-bus`：NATS / Kafka，用于 Agent 间通信和事件回放。

---

### Q2. StateBoard 被设计成"独立数据结构、不塞进 Director 的对话历史"，为什么？换成 Go，你会怎么实现它的并发安全？

**参考答案：**
原因：
1. **对话历史受 context window 限制**，会被截断或压缩；StateBoard 是结构化、完整、可查询的"白板"。
2. **可查询性**：对话历史是线性文本，StateBoard 是结构化数据，便于 Director 快速读取任务状态、预算、产出。
3. **可恢复性**：StateBoard 可序列化、持久化、快照，对话历史难以精确恢复。

Go 实现并发安全：
- 用 `struct StateBoard { mu sync.RWMutex; tasks map[string]*Task; agents map[string]*AgentState; budget Budget; events []Event }`。
- 读操作用 `RLock()`；写操作（更新任务状态、添加消息、扣减预算）用 `Lock()`。
- 或者按领域拆成多个 `sync.Map` / 独立 mutex，减少锁竞争：task 锁、agent 锁、budget 锁。
- 高频事件写入可用 **append-only log + snapshot** 模型：事件先写 WAL，再异步合并快照，避免每次更新都锁整个 StateBoard。
- 如果跨实例共享，把 StateBoard 放到 Redis / 数据库，Go 服务通过 repository 层访问。

---

### Q3. Stage 的 ResidentAgent 用"拉模式"（check_messages）而不是"推模式"，为什么？Go 里你会改成推模式吗？

**参考答案：**
原因：CoreCoderPattern.execute() 是顺序循环，卡在 `await llm.generate()` 时无法接收外部消息。不改 Pattern 的前提下，拉模式是最小改动方案。

Go 改成推模式的思路：
- 每个 ResidentAgent 一个 goroutine，内部 select：`case msg := <-inbox:` 接收消息，`case <-ctx.Done():` 退出。
- LLM 调用改成 **异步可中断**：`ctx, cancel := context.WithCancel(ctx)`，收到新消息时 cancel 当前请求，重新构建 prompt。
- 但这样复杂度上升：需要管理"正在进行的 LLM 调用"状态、处理 cancel 后的清理、避免消息乱序。
- 更实用的折中：保留拉模式，但缩短 check 间隔，或者用条件变量/通知机制让 Agent 在收到消息时立即 wake up（类似 `sync.Cond` 或 channel 信号）。

延伸考点：Go 里 `select` 多路复用、`context` 取消传播、goroutine 生命周期管理。

---

### Q4. Stage 里 Agent 间通信用 StateBoard._pending_messages 队列。如果换成 Go，你会怎么设计这个通信层？

**参考答案：**
单机版：
- 每个 Agent 一个 `chan Message`。
- `send_message(to, content)` 时，从 registry 找到目标 Agent 的 channel，非阻塞发送：`select { case ch <- msg: default: /* buffer full, drop or DLQ */ }`。
- 用 `sync.Map[string]chan Message` 做 Agent registry，支持动态注册/注销。

分布式版：
- 用 NATS / Redis Streams / Kafka。
- 每个 Agent 订阅 `agent.<id>.inbox` subject。
- 消息体序列化用 protobuf / JSON，包含 message_id、from、to、correlation_id、timestamp。
- 增加 dead-letter queue（DLQ）和 at-least-once 投递确认。

和 Go channel 对比：
- channel 是内存级、类型安全、编译期检查；但跨进程不行。
- NATS/Redis 支持持久化、跨实例、故障恢复，但引入网络延迟和序列化成本。

---

### Q5. 协作状态机（CollaborationStateMachine）在 Stage 里是怎么工作的？用 Go 写一个简化版会怎么设计？

**参考答案：**
Stage 里：producer（coder）生成实现，checker（reviewer/tester）检查；状态机管理 `idle -> assigned -> in_review -> approved -> completed` 或 `-> fix_needed -> assigned` 的循环。

Go 简化版：
```go
type State string
const (
    Idle       State = "idle"
    Assigned   State = "assigned"
    InReview   State = "in_review"
    FixNeeded  State = "fix_needed"
    Approved   State = "approved"
    Completed  State = "completed"
)

type Transition struct {
    From   State
    Action Action
    To     State
}

var transitions = []Transition{
    {Idle, Assign, Assigned},
    {Assigned, Submit, InReview},
    {InReview, Approve, Approved},
    {InReview, RequestFix, FixNeeded},
    {FixNeeded, Submit, InReview},
    {Approved, Complete, Completed},
}

func (sm *StateMachine) Apply(action Action) error {
    sm.mu.Lock()
    defer sm.mu.Unlock()
    for _, t := range transitions {
        if t.From == sm.state && t.Action == action {
            sm.state = t.To
            sm.history = append(sm.history, t)
            return nil
        }
    }
    return fmt.Errorf("invalid transition from %s with %s", sm.state, action)
}
```

延伸：
- 用 map[[2]string]State 做 O(1) 查找。
- 持久化：每次 Apply 先写 WAL，再改内存状态。
- 事件驱动：状态变更时 emit event，由 orchestrstrator 监听并触发下一步动作。

---

### Q6. Stage 里任务失败后会判断超时/429/复杂度太高/非关键，分别 retry/replan/skip。Go 里你会怎么实现这种错误分类与重试策略？

**参考答案：**
错误分类：
```go
type ErrorCategory int
const (
    RetryableNetwork ErrorCategory = iota
    RetryableRateLimit
    NonRetryable
    NeedsReplan
    NonCritical
)

type CategorizedError struct {
    error
    Category ErrorCategory
    RetryAfter time.Duration
}
```

重试策略：
- 网络/429：指数退避 + jitter，`backoff.Retry` 或自研 `retry.WithBackoff`。
- 复杂度太高：replan，把任务拆成子任务，递归提交。
- 非关键：skip，记录 skipped 原因。
- 需要人确认：ask_human，进入人类审批队列。

Go 代码示例：
```go
func (r *Runner) handleTaskFailure(ctx context.Context, task *Task, err error) error {
    cat := classify(err)
    switch cat.Category {
    case RetryableNetwork, RetryableRateLimit:
        if task.Retries < maxRetries {
            task.Retries++
            time.Sleep(cat.RetryAfter)
            return r.retryTask(ctx, task)
        }
    case NeedsReplan:
        return r.replan(ctx, task)
    case NonCritical:
        task.Status = Skipped
        return nil
    }
    task.Status = Failed
    return err
}
```

要点：
- 不要把所有 error 都 `fmt.Errorf` 包一层就丢给上层，要保留原始错误类型（用 `%w`）。
- 429 要读 `Retry-After` header。
- 重试要设置上限，避免无限重试打爆上游。

---

### Q7. corecoder 新增了流式解析器（StreamParser）处理 LLM 的流式输出。Go 里如果也要处理 SSE/HTTP streaming，你会怎么写？

**参考答案：**
Python 里 StreamParser 做的是：边读 LLM chunk 边识别 tool call 边界，缓存未完成的 JSON，最终组合成完整 tool call。

Go 实现思路：
```go
func ParseStream(ctx context.Context, rc io.ReadCloser, out chan<- StreamEvent) error {
    defer rc.Close()
    scanner := bufio.NewScanner(rc)
    var buf bytes.Buffer
    for scanner.Scan() {
        line := scanner.Text()
        if !strings.HasPrefix(line, "data: ") {
            continue
        }
        data := strings.TrimPrefix(line, "data: ")
        if data == "[DONE]" {
            break
        }
        var chunk LLMChunk
        if err := json.Unmarshal([]byte(data), &chunk); err != nil {
            return err
        }
        // accumulate partial tool call arguments
        buf.WriteString(chunk.Delta.ToolCall.Arguments)
        if chunk.Delta.ToolCall.Complete {
            out <- StreamEvent{Type: ToolCall, Payload: buf.String()}
            buf.Reset()
        }
    }
    return scanner.Err()
}
```

关键点：
- 用 `bufio.Scanner` 或 `bufio.Reader` 按行读取 SSE。
- 流式数据天然适合 `chan`：生产者解析 chunk，消费者（tool dispatcher）并行处理。
- 注意 goroutine 泄漏：consumer 退出时要能取消 producer，或设置 `ctx.Done()`。
- 背压：channel 带 buffer，避免 producer 过快阻塞；或做 rate limit。

---

### Q8. Stage 的工具系统用 `ToolPlugin` 接口，新工具继承后实现 name/description/execution_spec/schema/invoke。Go 里设计这样的插件系统有什么要注意的？

**参考答案：**
Go 接口设计：
```go
type Tool interface {
    Name() string
    Description() string
    Schema() json.RawMessage
    Spec() ExecutionSpec
    Invoke(ctx context.Context, params map[string]any) (any, error)
}
```

注册方式：
1. **显式注册**：`registry.Register("bash", &BashTool{})`，简单、可测试。
2. **反射扫描**：启动时扫描 package，用 `reflect` 找实现了 Tool 接口的类型。灵活但隐式，容易遗漏。
3. **plugin 包动态加载**：Go 的 `plugin` 包可以在运行时加载 `.so`，但跨平台差、调试难，生产慎用。

推荐做法：
- 用显式注册 + `init()` 自注册（每个工具文件 init 里注册自己），兼顾可读性和扩展性。
- 执行时通过接口调用，便于 Mock 测试。
- `execution_spec` 标注是否并发安全、是否只读、是否需要权限，调度器可据此优化并行调用。

---

### Q9. Stage 的测试大量 Mock LLM 调用，不依赖真实 API。Go 里你准备怎么给这个系统写单元测试？

**参考答案：**
分层测试：
1. **工具层**：每个 Tool 单独测。LLM 相关用 interface 抽象：
   ```go
   type LLMClient interface {
       Generate(ctx context.Context, req *GenerateRequest) (*GenerateResponse, error)
   }
   ```
   测试时注入 `mockLLM{responses: []Response}`。

2. **Pattern 层**：CoreCoderPattern 依赖 LLMClient、ToolRegistry、State。通过依赖注入替换为 fake。

3. **Director 层**：StateBoard 用内存实现，LLM 返回固定 tool call 序列，验证 Director 是否按预期 spawn/replan/finalize。

4. **集成测试**：用 docker-compose 起真实依赖（Redis、PostgreSQL），跑完整编排链路。

Go 测试技巧：
- `httptest` 模拟外部 HTTP API。
- `testify/mock` 或手写 fake。
- `t.Parallel()` 加速无状态测试。
- table-driven tests 覆盖状态机转移矩阵。

---

### Q10. Stage 有全局 Token 预算和 Metrics（mailbox_metrics 等）。如果迁移到 Go，预算控制和可观测性你会怎么设计？

**参考答案：**
Token 预算：
- 每个 Task / Agent 调用前 `budget.Reserve(estimated)`，调用后 `budget.Consume(actual)`。
- 用 `atomic.Int64` 做进程内全局计数；跨实例用 Redis `INCR`。
- 接近阈值时发 warning event；耗尽时直接 finalize 或 ask_human。

Metrics：
- 用 Prometheus client：
  ```go
  var (
      llmLatency = prometheus.NewHistogramVec(...)
      toolCalls  = prometheus.NewCounterVec(...)
      tokenUsage = prometheus.NewCounterVec(...)
  )
  ```
- 在 LLM client 和 tool invoke 处埋点。
-  tracing：OpenTelemetry，每个 agent run 一个 span，tool call 是子 span。

Alert：
- token 预算耗尽率、任务失败率、LLM 错误率、Agent 响应时间 P99。

---

### Q11. 如果把 Stage 从 Python 完整重写成 Go 服务，你会保留哪些设计、会改哪些设计？技术选型是什么？

**参考答案：**
保留：
- Director-Agent 双层架构。
- StateBoard 作为唯一真相源。
- 工具插件接口 + 注册表。
- 事件驱动的可观测性。
- 全局预算控制。

改动：
- **并发模型**：Python asyncio → Go goroutine + channel + select。
- **常驻 Agent**：每个 ResidentAgent 一个 goroutine，用 channel 收件，context 控制生命周期。
- **状态持久化**：从文件快照 → PostgreSQL + Redis。
- **服务化**：单体拆成 orchestrator + worker + state-store + mcp-registry，用 gRPC/HTTP 通信。
- **流式**：用 Go 的 HTTP client streaming + SSE parser。
- **错误处理**：Python 异常 → Go explicit error + sentinel error + retry classification。

技术选型：
- Web：Gin / Echo / std net/http
- RPC：gRPC + protobuf
- 数据库：PostgreSQL（持久化）+ Redis（缓存/消息）
- 消息队列：NATS / Redis Streams
- 可观测性：Prometheus + OpenTelemetry + Grafana
- 测试：testify + httptest + dockertest

---

## 加分追问（面试官可能会继续问）

1. **Stage 里 claimed ≠ verified，怎么防止 Agent 伪造完成？**
   - 答：Director 在 finalize 前 read_file 或 bash 验证 artifact 确实存在且内容非 placeholder；Go 里可在 artifact store 层加 checksum / signature。

2. **如果 StateBoard 很大，怎么给 Director 做 context compression？**
   - 答：分层摘要：只传 task 摘要 + 最近事件 + 关键指标；老事件归档。Go 里可预计算 snapshot 和 diff。

3. **MCP server 工具很多时，怎么避免 prompt 里 schema 爆炸？**
   - 答：工具选择器（tool selector）先根据任务描述筛选相关工具；Go 里可用向量检索或规则引擎预过滤。

4. **Agent 调用 bash/write_file 怎么保证安全？**
   - 答：沙箱、allowlist、权限模式（permission_mode）、操作审计日志。Go 里可用 seccomp、chroot、容器化。

5. **如何处理两个 Agent 同时写同一个文件？**
   - 答：StateBoard 里 artifact 有版本/锁；Go 里可用乐观锁（version）或悲观锁（artifact-level mutex）。

---

## 面试建议

- 回答时先讲"在 Stage 里我是这样做的"，再联系到"如果换成 Go 我会……"。
- 多用具体数字：568 个测试、30 文件 corecoder commit、全局 100K token budget。
- 对不确定的点诚实兜底，不要硬撑；但要把思考过程说出来。
- 准备一两个"踩坑"故事：比如 stream reset 导致编排失败、怎么排查的，会很加分。

# Agent 方向深度面试题（基于 Stage · 五层追问版）

> 面试岗位：AI Agent 平台 / Agent Infra / LLM 应用研发
> 目标：2 小时深度面试，每题可展开 5 层追问，从项目实践到前沿思辨。

---

## 时间分配

| 阶段 | 时长 | 题目 | 风格 |
|------|------|------|------|
| 项目破冰 | 10 min | Q0 | 表达 + ownership |
| Agent 架构 | 20 min | Q1 | 为什么 Multi-Agent |
| 认知循环 | 20 min | Q2 | ReAct 边界 |
| 调度决策 | 15 min | Q3 | Director 容错 |
| 工具系统 | 15 min | Q4 | Tool design |
| MCP | 10 min | Q5 | 工具生态标准 |
| 常驻 Agent | 15 min | Q6 | 生命周期 |
| 协作闭环 | 15 min | Q7 | 状态机收敛 |
| 记忆系统 | 15 min | Q8 | RAG + 记忆 |
| Eval | 10 min | Q9 | 效果评估 |
| 安全护栏 | 10 min | Q10 | 对齐 + 安全 |
| 成本与人机协同 | 10 min | Q11~Q12 | 落地策略 |
| 收尾 | 5 min | — | 反问 |

---

## Q0. 请用 2~3 分钟介绍 Stage，说明它解决了什么 Agent 场景的问题？

**参考答案**
Stage（戏台）是一个多 Agent 编排引擎。核心思路是把复杂任务交给 Director Agent 做全局调度，再由 coder/reviewer/tester 等 Worker Agent 协作完成。

解决的痛点：
- 单 Agent 长链路容易遗忘、幻觉、输出不稳定。
- 复杂任务需要"规划→执行→检查→修复"闭环。
- 多 Agent 之间需要状态共享、通信、预算控制。

我在里面负责：
1. Director-Agent 双层调度与 StateBoard 设计。
2. CoreCoderPattern 增强：planning mode、streaming parser、tool gating、clarification。
3. ResidentAgent 常驻消息循环。
4. 协作状态机：producer↔checker approve/fix 闭环。
5. MCP 接入与工具生态扩展。
6. Eval 体系与全局 Token 预算控制。

---

## Q1. Stage 为什么选择 Multi-Agent 架构，而不是让单 Agent 直接完成所有任务？

### L1：单 Agent 有什么问题？
- Context window 有限，长任务会遗忘前期决策。
- 一次要同时做规划、编码、测试、review，容易"角色混淆"。
- 没有外部检查机制，自己生成的代码自己觉得对。
- 错误会沿单一路径累积，没有纠错回路。

### L2：Director-Agent 分工的依据是什么？
- **Director**：全局视野，只做调度不编码；拥有完整 StateBoard。
- **Worker**：战术执行，只关心当前 task 和工具；可替换、可横向扩展。
- 这是"关注点分离"：规划和执行需要不同的 prompt、工具集、模型参数。

### L3：什么时候 Multi-Agent 反而不如单 Agent？
- 任务很简单（如翻译一句话、写一行正则）。
- 分解和通信开销超过收益。
- 任务需要强一致性、不能拆（如单次数学证明）。
- 环境不支持多个 Agent 共享状态或并行。

### L4：怎么度量 Multi-Agent 比单 Agent 好？
- 任务完成率（success rate）。
- 平均 retry 次数 / replan 次数。
- Token 效率（完成同样任务的总 token 消耗）。
- 产出可验证性（verified artifacts / claimed artifacts）。
- 人工介入率（human-in-the-loop frequency）。

### L5：如果任务本身不适合分解，Director 应该怎么做？
- 意图分类器识别为"简单任务"，直接 spawn 单个 coder，跳过复杂分解。
- 或者 Director 把 objective 作为一个单节点 TaskGraph。
- Stage 里的 `IntentClassifier` 就是做这件事：task_type + complexity + external 判断。

---

## Q2. Stage 里的 Agent 是怎么"思考"的？ReAct 循环的边界和缺陷是什么？

### L1：ReAct 的 O-T-A-O 流程是什么？
- **Observation**：看到当前环境（StateBoard、tool result）。
- **Thought**：内部推理下一步要做什么。
- **Action**：调用工具或 generate final answer。
- **Observation**：拿到 tool result，继续循环。

### L2：Stage 里 Tool result 怎么回到 LLM？
- Tool 返回结果被格式化（通常 markdown / JSON）。
- 作为 `role=user/tool` 的消息塞回 conversation。
- LLM 下一轮基于新的 observation 继续推理。
- 结果过长时截断到 `_TOOL_RESULT_CHAR_LIMIT`（如 8000 字符）。

### L3：ReAct 为什么容易陷入"思考循环"或"重复调用"？
- LLM 没有真正的"记忆"，容易重复已经验证过的动作。
- Tool result 不够信息性，导致 LLM 无法推进。
- Prompt 里没有明确的终止条件或步骤上限。
- 工具返回错误但 LLM 没有正确理解错误原因。

### L4：怎么检测和打破这种循环？
- **重复检测**：记录最近 N 步的 tool call 指纹，重复相同调用 → 提示 LLM 或强制终止。
- **步骤上限**：`max_steps`，超过直接 finalize 或 ask_human。
- **只读步骤上限**：`max_consecutive_readonly_steps`，防止 LLM 一直 read 不 write。
- **状态摘要**：给 LLM 一个更高级的状态总结，而不是完整历史。

### L5：ReAct 和 tree search / MCTS 有什么结合空间？
- ReAct 是单路径贪心探索；tree search 可以维护多个候选 action 分支。
- 可以引入"价值函数"评估每个 thought/action 的期望收益。
- MCTS 适合开放式探索任务（如漏洞挖掘、科学假设生成）。
- Stage 目前的协作状态机其实是一种简化的多分支探索：producer 和 checker 两路验证。

---

## Q3. Director 的调度决策是怎么做的？如果 Director 自己决策错了怎么办？

### L1：Director 的输入输出分别是什么？
- **输入**：StateBoard.snapshot()，包含 tasks、agents、budget、messages、artifacts。
- **输出**：tool call，如 show_state、spawn_agent、replan、finalize、ask_human。

### L2：spawn / replan / finalize / ask_human 的触发条件？
- **spawn**：有 ready task，且预算/并发槽位足够。
- **replan**：task failed 且原因是复杂度过高 / 依赖缺失 / 目标模糊。
- **finalize**：所有 task completed 或无法继续。
- **ask_human**：需要外部信息、权限确认、或无法自动决策。
- **retry**：超时 / 429 等可重试错误。

### L3：Director 自己的幻觉/错误决策怎么识别？
- 决策后 tool 执行失败（如 spawn 了一个不存在的 agent type）。
- 连续多轮没有推进任务状态。
- 决策与规则冲突（如预算已耗尽还要 spawn）。
- 通过"决策历史"（DecisionRecord）回放，人工复盘。

### L4：有没有给 Director 做"决策审计"或"决策回放"？
- Stage 里有 `DecisionRecord` / `decision_history` 记录每轮 Director 的输入 snapshot 和输出决策。
- 可以回放：把某一轮 snapshot 喂给新版本的 Director，看决策是否改进。
- 持久化事件日志也支持时间线重建。

### L5：能不能让 Director 也变成被 review 的 Agent？
- 可以引入"meta-director"或"orchestrator critic"：
  - 一个独立 Agent 审查 Director 的决策历史。
  - 发现低效决策时发出警告或建议新策略。
- 但这会增加系统复杂度和延迟，适合离线分析而非在线干预。
- 更实际的做法：规则 guardrail + 决策历史分析 + A/B 测试不同 Director prompt。

---

## Q4. Stage 的工具系统是怎么设计的？新增一个 tool 需要改哪些地方？

### L1：ToolPlugin 接口长什么样？
Stage 里工具要实现：
- `name`
- `description`
- `execution_spec()`：并发安全、权限模式、是否只读
- `schema()`：JSON Schema
- `invoke(context, params)`：实际执行

### L2：schema 怎么生成和校验？
- Python 里常用 Pydantic model → JSON Schema。
- 调用前用 JSON Schema 校验参数类型、必填项、范围。
- 校验失败要返回结构化错误给 LLM，让它能自我修复参数。

### L3：tool result 太长怎么办？
- 截断到 `_TOOL_RESULT_CHAR_LIMIT`（如 8000 字符）。
- 对列表类结果做摘要（如只返回前 N 条 + "... 共 M 条"）。
- 提供分页工具（如 `read_file offset=100 limit=50`）。
- 对代码/文件内容，支持 `grep` 过滤后再返回。

### L4：并发工具调用的边界在哪里？
- 只有标注 `concurrency_safe=True` 的工具才能并行。
- 写操作（write_file、bash 修改文件）通常串行。
- 读操作（read_file、list_directory、glob）可以并行。
- 同一文件的读写不能并发，否则会有竞态。

### L5：工具本身也会失败，怎么让 Agent 从 tool failure 恢复？
- 错误要结构化返回：`{error: "...", recoverable: true/false, suggestion: "..."}`。
- LLM 看到 recoverable 错误后可以重试或换参数。
- 不可恢复错误（如文件系统只读）升级给 Director 做 replan / ask_human。
- 记录 tool failure 模式，用于后续改进 tool 实现或 prompt。

---

## Q5. MCP 是什么？Stage 为什么要接入 MCP？

### L1：MCP 解决了什么问题？
- Model Context Protocol（模型上下文协议）是 Anthropic 推动的开放标准。
- 解决 LLM 应用接入外部工具/数据源时"每个平台一套接口"的碎片化问题。
- 统一了 tools、resources、prompts 的发现和调用方式。

### L2：MCP 和 function calling 是什么关系？
- Function calling 是 LLM 输出 tool call 的能力。
- MCP 是工具/资源/提示的"服务发现 + 传输协议"层。
- MCP 让 LLM 应用像连 USB 一样连外部能力，而不用关心每个能力的具体实现。

### L3：stdio / SSE / HTTP 三种 transport 的取舍？
- **stdio**：本地子进程，简单、安全隔离好，但只限单机。
- **SSE**：Server-Sent Events，单向 server→client 推送，适合实时通知。
- **HTTP**：最通用，可跨网络，但 latency 稍高，需要处理认证和重连。
- Stage 作为后端更适合 HTTP/SSE；stdio 适合本地开发或沙箱内工具。

### L4：MCP server 崩溃或返回错误怎么办？
- Client 端做健康检查、心跳、重连。
- 调用失败时 fallback 到本地同功能 tool 或报错给 LLM。
- 对关键 MCP server 做连接池 + 熔断（circuit breaker）。
- 记录失败率，自动降级不稳定的 MCP server。

### L5：MCP 会成为 AI 时代的"USB-C"吗？它的局限是什么？
- **可能成为 USB-C**：如果 OpenAI、Google、Meta 都支持，生态会统一。
- **局限**：
  - 目前还是早期，生态和工具质量参差不齐。
  - 安全模型还在演进：MCP server 能访问什么数据、怎么授权。
  - 复杂工作流（条件分支、循环、状态共享）不是 MCP 原生强项，还需要编排层（如 Stage）。

---

## Q6. ResidentAgent 常驻内存，为什么不用一次性 Agent？拉模式 vs 推模式怎么选？

### L1：为什么需要常驻 Agent？
- 协作任务中 Agent 需要保持上下文（已读文件、已做决策、未完成任务）。
- 减少反复创建/销毁的开销。
- 支持 Agent 间异步通信：A 发消息给 B，B 稍后处理。
- 适合 long-running 任务，如监控、reviewer 等待提交。

### L2：拉模式的优缺点？
- **优点**：实现简单，不 interrupt 当前 LLM 调用，避免并发修改 transcript。
- **缺点**：消息不能及时处理，延迟取决于 check 间隔；空闲时也会轮询。

### L3：推模式在 Agent 里难在哪里？
- Agent 正在执行 LLM.generate() 时无法安全注入新消息。
- 需要 cancel 当前调用、保存状态、重建 prompt，复杂且容易出错。
- 消息乱序：新消息可能比旧消息先处理。
- 需要更复杂的并发控制。

### L4：Agent 被消息打断时，正在进行的 LLM 调用怎么处理？
- 方案 A：忽略新消息直到当前 LLM 调用完成，事后读取。
- 方案 B：cancel 当前调用，把新消息合并进 prompt 重新生成。
- 方案 C：把消息放入"待处理队列"，当前调用结束后优先处理。
- Stage 目前用方案 A + 拉模式，是最小改动的稳健选择。

### L5：常驻 Agent 有没有记忆一致性风险？
- 有。如果 Agent 进程重启，内存中的 scratch/transcript 会丢失。
- 解决：定期持久化 transcript；从 StateBoard 恢复状态。
- 多个 Agent 同时读写同一 artifact 时，需要 artifact-level locking / versioning。

---

## Q7. 协作状态机是怎么工作的？producer↔checker 闭环怎么保证收敛？

### L1：状态有哪些？
常见状态：`idle → assigned → in_review → approved → completed`，或 `in_review → fix_needed → assigned`。

### L2：approve 和 fix_needed 的判断标准是什么？
- **approve**：测试通过、代码符合要求、无明显 bug。
- **fix_needed**：测试失败、review 发现缺陷、安全/风格问题。
- 标准应由 checker 的 prompt 和客观测试共同决定，不能仅凭 LLM 主观判断。

### L3：如果 checker 太严格或太宽松怎么办？
- **太严格**：producer 反复修改仍不通过，进入死循环。
- **太宽松**：bug 漏到下游。
- 解决：校准 checker prompt；引入测试用例作为硬性门槛；限制 approve/fix 循环次数。

### L4：循环不收敛（无限 approve/fix）怎么处理？
- 设置最大循环次数（如 5 轮）。
- 超过后升级：ask_human、拆分子任务、换 checker。
- 记录失败模式，分析是 producer 能力不足还是 checker 标准不清。

### L5：能不能引入第三个仲裁 Agent？
- 可以。当 producer 和 checker 争执不下时，引入 arbitrator。
- arbitrator 看双方论据 + artifact + 原始需求，做最终判定。
- 成本：多一轮 LLM 调用 + 更复杂的状态机。
- 适合高价值任务；日常任务用"循环上限 + ask_human"更经济。

---

## Q8. Agent 的记忆系统怎么设计？Stage 里怎么处理的？

### L1：短期记忆和长期记忆分别是什么？
- **短期记忆**：当前 conversation transcript、scratch、最近 tool results。
- **长期记忆**：跨 session 的历史任务、用户偏好、项目知识、失败/成功案例。

### L2：context window 满了怎么办？
- **截断**：保留最近 N 轮，丢弃早期消息。
- **摘要**：把早期对话压缩成 summary，替换原始消息。
- **分层**：system prompt + summary + recent messages + retrieved memories。

### L3：RAG 在 Agent 里怎么用？
- 用户输入 / Agent 当前 task 做 embedding。
- 从向量库检索相关文档、历史任务、代码片段。
- 检索结果注入 prompt 或 StateBoard context。
- 减少 hallucination，提高 domain-specific 任务成功率。

### L4：记忆检索不准怎么办？
- 多路召回：向量检索 + 关键词检索 + 结构化过滤。
- 重排序（rerank）模型对召回结果打分。
- 元数据过滤：按项目、任务类型、时间范围筛选。
- 反馈循环：Agent 标记"这条记忆有用/无用"，优化检索。

### L5：Agent 会不会"记错"？怎么纠正？
- 会。LLM 可能把幻觉当记忆，或记混相似任务。
- 纠正：
  - 记忆来源可追溯（which task / which file / which time）。
  - 允许人类编辑/删除记忆。
  - 定期用 eval 验证记忆检索的准确性。
  - 对关键记忆做 checksum 或签名。

---

## Q9. Stage 怎么评估 Agent 的效果？LLM-as-judge 有什么问题？

### L1：eval harness 有哪几类？
- **HumanEval**：函数级代码生成，看 pass@k。
- **SWE-bench-lite**：真实 GitHub issue 修复，看 patch 是否正确。
- **Custom harness**：项目自定义任务，如"创建 FastAPI TODO API"。

### L2：LLM-as-judge 的流程？
- 准备一个 rubric（评分维度：正确性、可读性、安全性、效率）。
- 用独立 judge LLM 阅读输入、输出、标准答案，给出分数和理由。
- 多 judge 投票降低单一模型 bias。

### L3：judge 的 bias 怎么解决？
- 多个 judge 投票，取平均或多数。
- judge 不知道输出来自哪个模型，避免偏好知名模型。
- 定期用人类标注校准 judge。
- 对边界案例，judge 输出"uncertain"而非强行打分。

### L4：pass@k、MBPP、HumanEval 指标差异？
- **pass@k**：生成 k 个候选，至少一个通过即算成功。
- **HumanEval**：手写 164 道 Python 函数题，经典代码生成基准。
- **MBPP**： Mostly Basic Python Problems，更注重基础编程能力。
- **SWE-bench**：端到端 issue 修复，难度远高于 HumanEval。

### L5：怎么防止 eval 过拟合？
- 训练/测试隔离：eval 数据不能出现在训练数据里。
- 动态更新 eval 集，定期加入新任务。
- 多维度评估：不仅看 pass rate，还看 token 效率、人工评分、运行时稳定性。
- 对 HumanEval 等公开 benchmark，警惕模型在训练时"见过"题目。

---

## Q10. Agent 的安全与护栏：怎么防止 Agent 做坏事或胡说？

### L1：sandbox 怎么做？
- 容器化：Docker / gvisor / firecracker。
- 文件系统隔离：只挂载必要目录，禁止访问 ~/.ssh、/etc 等。
- 网络隔离：默认无网络，需要外网访问时走代理并审计。
- 资源限制：CPU、内存、执行时间上限。

### L2：prompt injection 怎么防？
- 区分 system prompt 和 user content，避免用户内容覆盖系统指令。
- 输入消毒：过滤特殊 token、限制长度。
- 输出过滤：检测敏感信息泄露（API key、密码）。
- 多层防御：LLM 前加规则过滤器，后加输出校验。

### L3：tool 权限分级怎么做？
- **readonly**：read_file、list_directory、glob。
- **prompt**：write_file、bash 等写操作需要确认。
- **auto**：在低风险环境或高信任模式下自动执行。
- Stage 里的 `permission_mode` 就是做这个的。

### L4：输出内容怎么校验？
- 文件写入后 read_file 验证内容非空、路径正确。
- bash 输出检查是否有敏感信息、恶意命令痕迹。
- 代码用 ruff/pytest 等 linter/tester 跑一遍。
- 对 LLM 自然语言输出做事实性校验（RAG  grounding）。

### L5：Agent 有自己的"目标"怎么办？alignment 问题怎么解？
- 这是更深层问题：Agent 可能为了"完成任务"而采取捷径或有害手段。
- 解决方向：
  - 明确目标函数：不只是"完成任务"，还要"安全、可验证、符合人类意图"。
  - Constitutional AI / RLHF：让模型学会拒绝有害请求。
  - 人在回路：关键决策必须 human approval。
  - 审计与回滚：记录所有 action，事后可追责、可撤销。

---

## Q11. Token 预算和成本控制：Stage 怎么做的？

### L1：全局预算 vs per-agent 预算？
- **全局预算**：Director 需要整体视野，决定资源分配。
- **per-agent 预算**：防止单个 Agent 挥霍。
- Stage 用全局 Budget，同时在 Agent 级别做软限制。

### L2：怎么预估一次 LLM 调用的 token？
- 用 tokenizer（tiktoken / 模型自带 tokenizer）计算 prompt token。
- max_completion_tokens 是上限，实际消耗可能更少。
- 对工具调用链，预估 = prompt + tools schema + expected tool results + completion。

### L3：预算快耗尽时怎么办？
- 发送 warning event 给 Director。
- Director 决策：简化任务、换小模型、ask_human、finalize。
- 对非关键任务 skip。

### L4：caching 能省多少？
- Prompt caching：重复的系统 prompt、tool schema 可缓存，省 50%~90% prompt token。
- Result caching：相同输入直接返回缓存结果。
- 具体收益取决于任务重复度；对多 Agent 协作，系统 prompt 通常很长，缓存收益明显。

### L5：模型降级策略怎么做？
- 简单任务用便宜模型（gpt-3.5 / 本地小模型）。
- 复杂任务用强模型（gpt-4 / Claude）。
- 先用小模型尝试，失败后再升级（cascading）。
- 实时根据预算余量动态选择模型。

---

## Q12. 人机协同：什么情况下必须 ask_human？怎么设计 human channel？

### L1：ask_human 触发条件？
- 任务目标模糊，需要澄清。
- 涉及权限、敏感操作（如删除文件、发送邮件、部署生产）。
- Agent 连续失败，无法自动恢复。
- 预算即将耗尽，需要人决策是否继续。

### L2：human 回复怎么回注到 Agent？
- human 回复写入 StateBoard._pending_messages 或 human channel。
- Agent 通过 check_messages / 推送收到回复。
- 回复作为新的 user message 进入 conversation，继续 ReAct 循环。

### L3：人长时间不回复怎么办？
- 设置超时（如 10 分钟）。
- 超时后任务标记为 `needs_human`，释放资源。
- 可配置：超时后 skip、abort、或转给其他 Agent。

### L4：怎么避免频繁打扰人？
- 批量提问：把多个澄清问题合并成一条消息。
- 只在高不确定性或高影响时 ask_human。
- 学习用户偏好：常用决策以后自动做。

### L5：未来 Agent 还需要人吗？
- 短期：关键决策、 creative 方向、异常处理仍需要人。
- 中期：人可能从"执行者"变成"目标设定者 + 审计者"。
- 长期：在明确边界内的任务可完全自主，但边界外的仍需人。
- 核心：Agent 的 reliability 和 alignment 决定 human 参与程度。

---

## 加分题（如果还有时间）

### Q13. Agent 怎么实现自我改进？

#### L1：Agent 能从失败中学习吗？
- 能。记录每次失败的错误类型、原因、最终解决方案。
- 形成"pitfall journal"（类似 Stage 里的 pitfall-journal-pipeline）。
- 下次遇到相似任务时，把相关经验注入 prompt。

#### L2：经验怎么存储和检索？
- 结构化：{task_type, error_pattern, root_cause, fix_action, verified}。
- 存入向量数据库，按 task embedding 检索。
- 也可以存入规则库，由规则引擎直接匹配。

#### L3：会不会"学错"？
- 会。如果某次失败的修复其实是 workaround，Agent 可能把 workaround 当最佳实践。
- 需要 human review 或 eval 验证经验是否真正有效。
- 设置置信度，低置信度经验只作为参考。

#### L4：自我改进和 overfitting 怎么平衡？
- 区分"通用经验"和"项目特定经验"。
- 通用经验经过多任务验证后才能升级。
- 定期用新 eval 集测试，防止对旧任务过拟合。

#### L5：未来 Agent 会自己写 prompt 和工具吗？
- 已经在发生：prompt optimization、auto tool learning。
- Agent 可以 A/B 测试不同 prompt，选择效果更好的。
- 自动生成 tool wrapper：看到 API 文档后生成对应 tool schema 和调用代码。
- 但这要求极强的 eval 和护栏，否则会变成"自己改自己，越改越偏"。

---

### Q14. 多 Agent 系统中的"涌现"现象你遇到过吗？怎么看待？

#### L1：什么叫涌现？
- 单个 Agent 没有的能力，通过协作产生。
- 例如：coder 写代码 + reviewer 找 bug + tester 跑测试，组合后产生高质量软件。

#### L2：Stage 里有涌现的例子吗？
- producer↔checker 闭环就是一种简单涌现：单独 coder 容易遗漏问题，加上 reviewer 后整体质量提升。
- Director 的全局调度也是一种涌现：没有 Director，Agent 会各自为战。

#### L3：涌现一定是好的吗？
- 不一定。也可能涌现负面行为：
  - Agent 互相等待，整体卡顿。
  - Agent 互相推诿责任。
  - 多个 Agent 重复做同样的事。

#### L4：怎么设计系统让涌现偏向正面？
- 明确角色和职责边界。
- 全局状态可见（StateBoard）。
- 预算和超时约束，防止无限循环。
- 人类可介入、可审计。

#### L5：多 Agent 协作的终极目标是什么？
- 不是替代人，而是形成一个"人类 + 多个 Specialist Agent"的混合智能系统。
- 人负责目标、价值判断、异常处理；Agent 负责执行、检查、迭代。
- 最终形态可能是"组织即代码"：把团队分工写成可运行的 Agent 系统。

---

## 面试建议

1. **开场定调**：用"导演-戏子-戏台"三句话讲清楚 Stage，让面试官快速进入语境。
2. **先讲实践，再讲理论**：每道题先说 Stage 里怎么做，再抽象到 Agent 系统设计。
3. **带数字说话**：568 个测试、3 个原子 commit、100K token budget、8-step ReAct。
4. **诚实兜底**：对不确定的前沿问题，说出思考路径，不要硬编。
5. **准备故事**：
   - 一个"成功协作"的故事：多 Agent 闭环怎么提升产出质量。
   - 一个"踩坑"的故事：stream reset、状态机不收敛、或 budget 超限是怎么排查的。
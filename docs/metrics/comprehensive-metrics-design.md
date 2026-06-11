# 戏台 (Xitai) 全方位评估指标体系

## 一、框架总览

多 Agent 编排系统的评估不能只看"任务是否完成"。戏台作为 Director-Agent 架构的编排引擎，评估需要覆盖 8 个维度：

```
┌─────────────────────────────────────────────────────────────┐
│                    评估指标体系                               │
├─────────────┬─────────────┬─────────────┬─────────────────┤
│ 任务完成度   │ 编排效率     │ 资源效率     │ 协作质量         │
├─────────────┼─────────────┼─────────────┼─────────────────┤
│ 系统健壮性   │ 产出质量     │ Human-in-Loop│ 可观测性         │
└─────────────┴─────────────┴─────────────┴─────────────────┘
```

每个维度下的指标按优先级分为三级：
- **P0** — 核心指标，必须实现
- **P1** — 重要指标，建议实现
- **P2** — 增强指标，按需实现

---

## 二、维度详解

### 2.1 任务完成度 (Task Completion)

评估"任务做没做完、做对没做对"。

| 指标 | 定义 | 计算方式 | 优先级 | 数据来源 |
|------|------|---------|--------|---------|
| **success_rate** | 任务成功率 | `completed / total` | P0 | StateBoard.task.status |
| **pass_rate** | 验证通过率 | `passed_verifications / total_verifications` | P0 | EvalResult.verify_scores |
| **success_score** | 综合完成度 | 验证规则的平均得分 (0-1) | P0 | EvalResult |
| **partial_completion_rate** | 部分完成率 | 至少 1 个 artifact 产出但未全部完成的占比 | P1 | StateBoard.artifacts |
| **terminal_state_distribution** | 终态分布 | COMPLETED / FAILED / SKIPPED 的比例 | P1 | StateBoard.tasks |

**已有实现**：`success_score`、`pass_rate` 在 `eval/base.py` 中已实现。

---

### 2.2 编排效率 (Orchestration Efficiency)

评估 Director/TeamLeader 的调度决策质量。

| 指标 | 定义 | 计算方式 | 优先级 | 数据来源 |
|------|------|---------|--------|---------|
| **scheduling_score** | 调度质量 | `expected_graph` 与 `actual_graph` 的结构相似度 (Jaccard/编辑距离) | P0 | StateBoard.tasks + EvalTask.expected_graph |
| **parallelism_peak** | 峰值并行度 | 同一时刻 RUNNING 状态的 Agent 数量最大值 | P0 | StateBoard.events (agent.registered / agent.done) |
| **parallelism_utilization** | 并行利用率 | 实际并行度峰值 / 理论最大并行度（拓扑分层中的最大层宽） | P1 | TaskGraph.topological_layers |
| **dependency_wait_avg** | 平均依赖等待时间 | PENDING → RUNNING 的平均时间差 | P1 | StateBoard.events (task.pending → task.running) |
| **replan_frequency** | 重规划频率 | replan 工具调用次数 / total_tasks | P1 | StateBoard.events (replan.called) |
| **plan_adherence** | 计划 adherence | 实际执行的任务图与初始分解的任务图的节点交集比例 | P2 | 对比 initial_task_graph vs executed_task_graph |

**已有实现**：`scheduling_score` 在 `eval/custom/harness.py` 中是 hardcode 0.5（TODO）。

---

### 2.3 资源效率 (Resource Efficiency)

评估 token、time、steps 的使用效率。

| 指标 | 定义 | 计算方式 | 优先级 | 数据来源 |
|------|------|---------|--------|---------|
| **token_efficiency** | Token 效率 | `total_tasks / token_used` (每 token 完成多少任务) | P0 | StateBoard.budget |
| **step_efficiency** | Step 效率 | `total_tasks / steps_taken` | P0 | StateBoard.budget |
| **time_efficiency** | 时间效率 | `total_tasks / duration_sec` | P0 | StateBoard.budget.start_time |
| **budget_exhaustion_rate** | 预算耗尽率 | `budget.exhausted == true` 的任务占比 | P0 | StateBoard.budget |
| **token_per_task** | 单任务平均 token | `token_used / total_tasks` | P1 | StateBoard.budget |
| **token_per_agent_type** | 按 Agent 类型的 token 分布 | 各 agent_type 的 token 占比 | P1 | StateBoard.agents.token_used |
| **step_per_task** | 单任务平均 step | `steps_taken / total_tasks` | P1 | StateBoard.budget |
| **idle_token_ratio** | 空闲 token 占比 | 等待/重试期间的 token 消耗 / 总 token | P2 | StateBoard.events (细分 token 消耗事件) |
| **overhead_ratio** | 编排开销占比 | Director/TeamLeader 的 token / 总 token | P2 | StateBoard.agents (按 agent_type 聚合) |

**已有实现**：`efficiency_score` 在 `eval/custom/harness.py` 中公式为 `min(max_steps / steps, 1.0)`，**公式反了**（steps 越多分数越高），需要修复为 `max(0, 1 - steps / max_steps)` 或 `max_steps / max(steps, max_steps)`。

---

### 2.4 协作质量 (Collaboration Quality)

评估 Agent 间通信和协作的效率。

| 指标 | 定义 | 计算方式 | 优先级 | 数据来源 |
|------|------|---------|--------|---------|
| **iteration_rounds** | 迭代轮数 | coder→reviewer 的平均往返次数 | P0 | StateBoard.events (reviewer_requested_fix / reviewer_approved) |
| **message_round_trip_avg** | 平均消息往返 | `send_message 次数 / total_tasks` | P1 | StateBoard.events (message.sent) |
| **message_response_time** | 消息响应时间 | send → reply 的平均时间差 | P1 | StateBoard._pending_messages (ts 差值) |
| **collaboration_success_rate** | 协作成功率 | 协作模式下完成且 approved 的任务 / 协作模式总任务 | P1 | StateBoard.tasks (agent_type=coder + status=COMPLETED after REVIEW) |
| **thread_utilization** | 对话线程利用率 | 有消息的 thread 数 / 总 thread 数 | P2 | StateBoard.conversation_threads |
| **cross_agent_mention_count** | 跨 Agent 提及次数 | 一个 Agent 的 message 被另一个 Agent 引用的次数 | P2 | conversation_threads.messages 内容分析 |

**已有实现**：无。

---

### 2.5 系统健壮性 (System Resilience)

评估系统在异常、压力下的恢复能力。

| 指标 | 定义 | 计算方式 | 优先级 | 数据来源 |
|------|------|---------|--------|---------|
| **recovery_rate** | 恢复率 | FAILED → COMPLETED 的任务数 / 总 FAILED 数 | P0 | StateBoard.tasks (状态变更轨迹) |
| **retry_success_rate** | 重试成功率 | 重试后成功的任务 / 总重试任务 | P0 | StateBoard.tasks (retry_count > 0 && status=COMPLETED) |
| **resilience_score** | 恢复力综合评分 | (recovery_rate * 0.5 + retry_success_rate * 0.3 + auto_finalize_rate * 0.2) | P0 | 综合计算 |
| **stuck_detection_rate** | 卡死检测率 | 被 Monitor 检测为 stuck 并恢复的比例 | P1 | StateBoard.events (orchestrator.resident_stuck + 后续恢复事件) |
| **heartbeat_timeout_rate** | 心跳超时率 | heartbeat timeout 次数 / total heartbeat 次数 | P1 | StateBoard.events (monitor.heartbeat_timeout) |
| **api_error_rate** | API 错误率 | llm.failed 次数 / (llm.succeeded + llm.failed) | P1 | StateBoard.events (sdk.llm.failed / sdk.llm.succeeded) |
| **tool_failure_rate** | 工具失败率 | tool.failed 次数 / (tool.succeeded + tool.failed) | P1 | StateBoard.events (sdk.tool.failed / sdk.tool.succeeded) |
| **auto_finalize_rate** | 自动收尾率 | auto_finalize 次数 / total runs | P1 | StateBoard.events (orchestrator.auto_finalized) |

**已有实现**：`resilience_score` 在 `eval/custom/harness.py` 中始终为 0（未计算）。

---

### 2.6 产出质量 (Output Quality)

评估 Agent 产出物的实际质量。

| 指标 | 定义 | 计算方式 | 优先级 | 数据来源 |
|------|------|---------|--------|---------|
| **artifact_verification_rate** | Artifact 验证率 | verified artifacts / claimed artifacts | P0 | StateBoard.artifacts (status=verified / total) |
| **artifact_empty_rate** | 空 artifact 率 | 大小为 0 的文件 / 总 claimed 文件 | P0 | ArtifactStore / filesystem check |
| **test_pass_rate** | 测试通过率 | `project_context.test_reports` 中 passed / (passed + failed) | P1 | StateBoard.project_context.test_reports |
| **code_complexity_delta** | 代码复杂度变化 | 产出代码的 cyclomatic complexity vs 基线 | P2 | 静态分析工具 (ast) |
| **diff_similarity** | 期望产出相似度 | 实际产出与 expected_artifacts 的文本相似度 | P2 | Levenshtein / diff ratio |
| **artifact_coverage** | Artifact 覆盖率 | actual_artifacts / expected_artifacts 的交集比例 | P1 | StateBoard.tasks (actual_artifacts vs expected_artifacts) |

**已有实现**：`state_score` 在 `eval/custom/harness.py` 中基于不存在的 `artifact_records` 属性，需要修复为 `StateBoard.artifacts`。

---

### 2.7 Human-in-the-Loop 效率

评估人类介入的效率和影响。

| 指标 | 定义 | 计算方式 | 优先级 | 数据来源 |
|------|------|---------|--------|---------|
| **human_intervention_frequency** | 人工介入频率 | ask_human 次数 / total_tasks | P1 | StateBoard._human_questions |
| **human_response_time** | 人类响应时间 | ask → reply 的平均时间差 | P1 | StateBoard._human_questions (ts 差值) |
| **human_reply_rate** | 人类回复率 | 被回答的 question / 总 question | P1 | StateBoard._human_questions |
| **human_post_impact** | 人工干预影响力 | human_post 后 task 状态变更的比例 | P2 | StateBoard._human_messages + 关联 task 状态变更 |
| **human_escalation_accuracy** | 升级准确性 | ask_human 后 task 最终成功的比例 | P2 | StateBoard._human_questions + 关联 task 终态 |

**已有实现**：无。

---

### 2.8 可观测性 (Observability)

评估系统是否"可被理解和调试"。

| 指标 | 定义 | 计算方式 | 优先级 | 数据来源 |
|------|------|---------|--------|---------|
| **event_coverage** | 事件覆盖率 | 有 event 记录的 task 数 / 总 task 数 | P1 | StateBoard.events |
| **event_density** | 事件密度 | 总 events / total_tasks | P1 | StateBoard.events |
| **trace_completeness** | 轨迹完整性 | 从 task.PENDING → 终态的完整事件链占比 | P2 | StateBoard.events (按 task_id 分组检查状态链) |
| **log_signal_ratio** | 信号噪音比 | 有意义的 event 类型数 / 总 event 类型数 | P2 | StateBoard.events (去重 event_type) |

**已有实现**：无。

---

## 三、指标汇总表

| 维度 | 指标数 | P0 | P1 | P2 | 已有 | 待修复 | 待新增 |
|------|--------|----|----|----|------|--------|--------|
| 任务完成度 | 5 | 3 | 2 | 0 | 3 | 0 | 2 |
| 编排效率 | 5 | 2 | 2 | 1 | 0 | 1 | 4 |
| 资源效率 | 9 | 4 | 3 | 2 | 1 | 1 | 7 |
| 协作质量 | 6 | 1 | 3 | 2 | 0 | 0 | 6 |
| 系统健壮性 | 8 | 3 | 4 | 1 | 0 | 1 | 7 |
| 产出质量 | 6 | 2 | 2 | 2 | 1 | 1 | 4 |
| Human-in-the-Loop | 5 | 0 | 3 | 2 | 0 | 0 | 5 |
| 可观测性 | 4 | 0 | 2 | 2 | 0 | 0 | 4 |
| **总计** | **48** | **15** | **21** | **12** | **5** | **4** | **39** |

---

## 四、与现有 eval 框架的映射

| EvalResult 字段 | 当前实现 | 应映射的指标 | 状态 |
|----------------|---------|-------------|------|
| success_score | 验证平均分 | ✅ 正确 | OK |
| scheduling_score | hardcode 0.5 | 应 = `scheduling_score` (Jaccard) | **修复** |
| execution_score | = success_score | 应 = 独立执行质量评估 | **修复** |
| efficiency_score | 反公式 | 应 = `token_efficiency` + `step_efficiency` | **修复** |
| resilience_score | 始终 0 | 应 = `recovery_rate` + `retry_success_rate` | **修复** |
| state_score | 读错属性 | 应 = `artifact_verification_rate` | **修复** |

---

## 五、实施路线图

### Phase 1：修复现有 6 维度评分（本周）
1. 修复 `efficiency_score` 公式
2. 实现 `scheduling_score`（expected_graph vs actual 对比）
3. 实现 `resilience_score`（recovery + retry）
4. 修复 `state_score`（用正确的 artifacts 属性）
5. 让 `execution_score` 独立于 success_score（加入步骤效率权重）

### Phase 2：核心 P0 指标（2 周）
1. 资源效率 4 个指标（token/step/time/budget_exhaustion）
2. 系统健壮性 3 个指标（recovery/retry/stuck）
3. 任务完成度 2 个指标（partial/terminal_distribution）

### Phase 3：P1 增强（后续按需）
1. 协作质量 3 个指标
2. Human-in-the-Loop 3 个指标
3. 产出质量 2 个指标
4. 可观测性 2 个指标

### Phase 4：P2 深度（长期）
1. 代码复杂度分析
2. diff 相似度
3. 跨 Agent 引用分析
4. 轨迹完整性检查

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

---

## 附录：48 个指标公式详解

以下所有公式中，符号约定：

- `T` = 总任务数 (`len(StateBoard.tasks)`)
- `T_c` = COMPLETED 任务数
- `T_f` = FAILED 任务数
- `T_s` = SKIPPED 任务数
- `B` = Budget 对象 (`StateBoard.budget`)
- `E` = Events 列表 (`StateBoard.events`)
- `A` = Agents 字典 (`StateBoard.agents`)
- `Ar` = Artifacts 字典 (`StateBoard.artifacts`)
- `ts(x)` = 事件 x 的时间戳

---

### A. 任务完成度（5 个）

#### A1. success_rate
```python
success_rate = T_c / T
```

#### A2. pass_rate
```python
pass_rate = sum(1 for v in verify_scores.values() if v >= 0.8) / len(verify_scores)
```

#### A3. success_score
```python
success_score = sum(verify_scores.values()) / len(verify_scores) if verify_scores else 0.0
```

#### A4. partial_completion_rate
```python
partial = sum(
    1 for t in tasks.values()
    if t.actual_artifacts and len(t.actual_artifacts) < len(t.expected_artifacts)
)
partial_completion_rate = partial / T
```

#### A5. terminal_state_distribution
```python
terminal_state_distribution = {
    "completed": T_c / T,
    "failed":    T_f / T,
    "skipped":   T_s / T,
}
```

---

### B. 编排效率（5 个）

#### B1. scheduling_score
基于期望任务图与实际任务图的 Jaccard 相似度：

```python
def graph_jaccard(expected: TaskGraph, actual: TaskGraph) -> float:
    expected_nodes = {t.task_id for t in expected.tasks}
    actual_nodes   = {t.task_id for t in actual.tasks}
    node_sim = len(expected_nodes & actual_nodes) / len(expected_nodes | actual_nodes)

    expected_edges = {(t.task_id, d) for t in expected.tasks for d in t.dependencies}
    actual_edges   = {(t.task_id, d) for t in actual.tasks   for d in t.dependencies}
    edge_sim = len(expected_edges & actual_edges) / len(expected_edges | actual_edges) if expected_edges else 1.0

    return node_sim * 0.6 + edge_sim * 0.4
```

#### B2. parallelism_peak
```python
from collections import defaultdict

agent_events = defaultdict(list)  # agent_id -> [(event_type, ts)]
for e in E:
    if e.agent_id:
        agent_events[e.agent_id].append((e.event_type, e.ts))

# 计算任意时刻的 RUNNING agent 数
running_counts = []
for agent_id, events in agent_events.items():
    for etype, ts in events:
        if etype in ("agent.running", "resident.started"):
            running_counts.append((ts, +1))
        elif etype in ("agent.done", "agent.failed", "resident.stopped"):
            running_counts.append((ts, -1))

running_counts.sort()
peak = 0
current = 0
for _, delta in running_counts:
    current += delta
    peak = max(peak, current)

parallelism_peak = peak
```

#### B3. parallelism_utilization
```python
layers = task_graph.topological_layers()
max_layer_width = max(len(layer) for layer in layers) if layers else 1
parallelism_utilization = parallelism_peak / max_layer_width
```

#### B4. dependency_wait_avg
```python
waits = []
for e in E:
    if e.event_type == "task.running" and e.task_id:
        pending_events = [x for x in E if x.event_type == "task.pending" and x.task_id == e.task_id]
        if pending_events:
            wait = e.ts - min(x.ts for x in pending_events)
            waits.append(wait)

dependency_wait_avg = sum(waits) / len(waits) if waits else 0.0
```

#### B5. replan_frequency
```python
replan_count = sum(1 for e in E if e.event_type == "replan.called")
replan_frequency = replan_count / T
```

#### B6. plan_adherence (P2)
```python
initial_ids = {t.task_id for t in initial_graph.tasks}
executed_ids = {t.task_id for t in actual_graph.tasks}
plan_adherence = len(initial_ids & executed_ids) / len(initial_ids | executed_ids)
```

---

### C. 资源效率（9 个）

#### C1. token_efficiency
```python
token_efficiency = T / max(B.token_used, 1)
```

#### C2. step_efficiency
```python
step_efficiency = T / max(B.steps_taken, 1)
```

#### C3. time_efficiency
```python
duration_sec = time.time() - B.start_time
time_efficiency = T / max(duration_sec, 1)
```

#### C4. budget_exhaustion_rate
```python
budget_exhaustion_rate = int(B.exhausted)  # 0 或 1，单次 run；多次 run 取平均
```

#### C5. token_per_task
```python
token_per_task = B.token_used / T
```

#### C6. token_per_agent_type
```python
from collections import defaultdict

agent_tokens = defaultdict(int)
for agent_id, agent_state in A.items():
    agent_tokens[agent_state.agent_type] += agent_state.token_used

token_per_agent_type = {
    atype: tokens / B.token_used
    for atype, tokens in agent_tokens.items()
}
```

#### C7. step_per_task
```python
step_per_task = B.steps_taken / T
```

#### C8. idle_token_ratio (P2)
```python
# 近似：Director + Monitor + TeamLeader 的 token 视为 "编排开销"
overhead_agents = {"director", "monitor", "team_leader"}
overhead_tokens = sum(
    a.token_used for a in A.values()
    if a.agent_type in overhead_agents
)
idle_token_ratio = overhead_tokens / max(B.token_used, 1)
```

#### C9. overhead_ratio (P2)
```python
# 与 idle_token_ratio 相同，换个视角
overhead_ratio = overhead_tokens / max(B.token_used, 1)
```

---

### D. 协作质量（6 个）

#### D1. iteration_rounds
```python
# 对每个经过 REVIEW 状态的任务，统计 fix_needed → approved 的轮数
review_tasks = [t for t in tasks.values() if any(
    h["action"].startswith("reviewer") for h in t.iteration_history
)]

rounds = []
for t in review_tasks:
    fix_count = sum(1 for h in t.iteration_history if "fix" in h["action"])
    rounds.append(fix_count + 1)  # +1 for initial review

iteration_rounds = sum(rounds) / len(rounds) if rounds else 0.0
```

#### D2. message_round_trip_avg
```python
message_count = sum(1 for e in E if e.event_type == "message.sent")
message_round_trip_avg = message_count / T
```

#### D3. message_response_time
```python
# 简化为 mailbox 中 send → 下一次 check_messages 的时间差
# 精确实现需要按 conversation thread 配对
response_times = []
for thread in conversation_threads.values():
    msgs = sorted(thread.messages, key=lambda m: m["ts"])
    for i, msg in enumerate(msgs):
        if i + 1 < len(msgs):
            response_times.append(msgs[i + 1]["ts"] - msg["ts"])

message_response_time = sum(response_times) / len(response_times) if response_times else 0.0
```

#### D4. collaboration_success_rate
```python
collab_tasks = [t for t in tasks.values() if t.agent_type == "coder" and any(
    h["action"] == "reviewer_approved" for h in t.iteration_history
)]
collaboration_success_rate = len(collab_tasks) / max(len([t for t in tasks.values() if t.agent_type == "coder"]), 1)
```

#### D5. thread_utilization
```python
active_threads = sum(1 for t in conversation_threads.values() if t.messages)
thread_utilization = active_threads / max(len(conversation_threads), 1)
```

#### D6. cross_agent_mention_count (P2)
```python
# 简单实现：检查消息内容中是否包含其他 agent 的 ID
mentions = 0
for thread in conversation_threads.values():
    for msg in thread.messages:
        for agent_id in agents.keys():
            if agent_id != msg.get("from") and agent_id in msg.get("content", ""):
                mentions += 1
```

---

### E. 系统健壮性（8 个）

#### E1. recovery_rate
```python
# 追踪 FAILED → COMPLETED 的状态转换
recovered = 0
for t in tasks.values():
    if t.status == TaskStatus.COMPLETED:
        # 检查是否曾经 FAILED
        if any(h["action"] == "retry" or "failed" in h["action"] for h in t.iteration_history):
            recovered += 1

recovery_rate = recovered / max(T_f, 1)
```

#### E2. retry_success_rate
```python
retried_tasks = [t for t in tasks.values() if t.retry_count > 0]
retry_success = sum(1 for t in retried_tasks if t.status == TaskStatus.COMPLETED)
retry_success_rate = retry_success / max(len(retried_tasks), 1)
```

#### E3. resilience_score
```python
auto_finalize_count = sum(1 for e in E if e.event_type == "orchestrator.auto_finalized")
auto_finalize_rate = auto_finalize_count / max(total_runs, 1)

resilience_score = (
    recovery_rate       * 0.5 +
    retry_success_rate  * 0.3 +
    auto_finalize_rate  * 0.2
)
```

#### E4. stuck_detection_rate
```python
stuck_events = [e for e in E if e.event_type == "orchestrator.resident_stuck"]
stuck_recovered = sum(1 for e in stuck_events if any(
    x.event_type.startswith("task.completed") and x.task_id == e.task_id
    for x in E if x.ts > e.ts
))
stuck_detection_rate = stuck_recovered / max(len(stuck_events), 1)
```

#### E5. heartbeat_timeout_rate
```python
heartbeat_sent = sum(1 for e in E if e.event_type == "monitor.heartbeat_sent")
heartbeat_timeout = sum(1 for e in E if e.event_type == "monitor.heartbeat_timeout")
heartbeat_timeout_rate = heartbeat_timeout / max(heartbeat_sent, 1)
```

#### E6. api_error_rate
```python
llm_ok    = sum(1 for e in E if e.event_type == "sdk.llm.succeeded")
llm_fail  = sum(1 for e in E if e.event_type == "sdk.llm.failed")
api_error_rate = llm_fail / max(llm_ok + llm_fail, 1)
```

#### E7. tool_failure_rate
```python
tool_ok   = sum(1 for e in E if e.event_type == "sdk.tool.succeeded")
tool_fail = sum(1 for e in E if e.event_type == "sdk.tool.failed")
tool_failure_rate = tool_fail / max(tool_ok + tool_fail, 1)
```

#### E8. auto_finalize_rate
```python
auto_finalize_rate = auto_finalize_count / max(total_runs, 1)
```

---

### F. 产出质量（6 个）

#### F1. artifact_verification_rate
```python
claimed = [a for a in Ar.values() if a.status in ("claimed", "verified")]
verified = [a for a in claimed if a.status == "verified"]
artifact_verification_rate = len(verified) / max(len(claimed), 1)
```

#### F2. artifact_empty_rate
```python
from pathlib import Path

empty = 0
for path, rec in Ar.items():
    full = Path(path)
    if full.exists() and full.stat().st_size == 0:
        empty += 1

artifact_empty_rate = empty / max(len(Ar), 1)
```

#### F3. test_pass_rate
```python
reports = project_context.get("test_reports", [])
if reports:
    latest = reports[-1]
    total_tests = latest.get("passed", 0) + latest.get("failed", 0)
    test_pass_rate = latest.get("passed", 0) / max(total_tests, 1)
else:
    test_pass_rate = 0.0
```

#### F4. code_complexity_delta (P2)
```python
import ast

def cyclomatic_complexity(source: str) -> int:
    tree = ast.parse(source)
    complexity = 1
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While, ast.For, ast.ExceptHandler, ast.With, ast.Assert)):
            complexity += 1
        elif isinstance(node, ast.BoolOp):
            complexity += len(node.values) - 1
    return complexity
```

#### F5. diff_similarity (P2)
```python
def levenshtein(a: str, b: str) -> int:
    # 标准 Levenshtein 距离实现
    m, n = len(a), len(b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1): dp[i][0] = i
    for j in range(n + 1): dp[0][j] = j
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            cost = 0 if a[i-1] == b[j-1] else 1
            dp[i][j] = min(dp[i-1][j] + 1, dp[i][j-1] + 1, dp[i-1][j-1] + cost)
    return dp[m][n]

def diff_similarity(actual: str, expected: str) -> float:
    dist = levenshtein(actual, expected)
    max_len = max(len(actual), len(expected))
    return 1.0 - (dist / max_len) if max_len > 0 else 1.0
```

#### F6. artifact_coverage
```python
actual_set   = set(t.actual_artifacts or [])
expected_set = set(t.expected_artifacts or [])
artifact_coverage = len(actual_set & expected_set) / max(len(expected_set), 1)
```

---

### G. Human-in-the-Loop（5 个）

#### G1. human_intervention_frequency
```python
human_intervention_frequency = len(_human_questions) / T
```

#### G2. human_response_time
```python
response_times = []
for q in _human_questions:
    if q["answer"] is not None and q.get("ts"):
        # 需要给 question 也记录 ts
        response_times.append(reply_ts - question_ts)

human_response_time = sum(response_times) / len(response_times) if response_times else 0.0
```

#### G3. human_reply_rate
```python
answered = sum(1 for q in _human_questions if q["answer"] is not None)
human_reply_rate = answered / max(len(_human_questions), 1)
```

#### G4. human_post_impact (P2)
```python
# human_post 后 60 秒内是否有 task 状态变更
impacted = 0
for msg in _human_messages:
    post_ts = msg["ts"]
    for e in E:
        if e.ts > post_ts and e.ts < post_ts + 60 and e.event_type.startswith("task."):
            impacted += 1
            break

human_post_impact = impacted / max(len(_human_messages), 1)
```

#### G5. human_escalation_accuracy (P2)
```python
escalated_tasks = []
for q in _human_questions:
    # 找到关联 task（通过 agent_id 或最近 task）
    task = find_related_task(q["from"])
    if task:
        escalated_tasks.append(task)

successful = sum(1 for t in escalated_tasks if t.status == TaskStatus.COMPLETED)
human_escalation_accuracy = successful / max(len(escalated_tasks), 1)
```

---

### H. 可观测性（4 个）

#### H1. event_coverage
```python
tasks_with_events = len({e.task_id for e in E if e.task_id})
event_coverage = tasks_with_events / T
```

#### H2. event_density
```python
event_density = len(E) / T
```

#### H3. trace_completeness (P2)
```python
# 检查每个 task 是否有 PENDING → RUNNING → 终态的完整链
complete = 0
for task_id in tasks.keys():
    task_events = [e for e in E if e.task_id == task_id]
    types = {e.event_type for e in task_events}
    has_pending = any(t.startswith("task.pending") for t in types)
    has_terminal = any(t.startswith("task.completed") or t.startswith("task.failed") for t in types)
    if has_pending and has_terminal:
        complete += 1

trace_completeness = complete / T
```

#### H4. log_signal_ratio (P2)
```python
# "有意义" = 非 budget.steps / budget.tokens 这类高频噪音事件
noise_types = {"budget.tokens", "budget.steps"}
all_types = {e.event_type for e in E}
meaningful_types = all_types - noise_types
log_signal_ratio = len(meaningful_types) / max(len(all_types), 1)
```

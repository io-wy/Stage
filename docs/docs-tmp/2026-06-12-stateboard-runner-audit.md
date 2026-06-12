# StateBoard & Runner 审计报告

日期: 2026-06-12

## 架构评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 正确性 | B+ | 核心逻辑正确，但存在静默吞错和边界条件未覆盖 |
| 可维护性 | C | state_board.py(1545行) + runner.py(1634行) 都严重违反 SRP |
| 可观测性 | B | 事件系统好，但缺少聚合指标和延迟直方图 |
| 并发安全 | B- | sync/async 桥接脆弱，mailbox 访问无锁 |
| 测试覆盖 | C+ | 核心路径有测试，但协作状态机、竞态、边界均未测试 |
| 资源管理 | C | 事件日志无界、transcript 无界、无 backpressure |

---

## StateBoard 问题清单

### P0 (立即修复)
1. **事件日志无界增长** — events[] 永远 append 不清除，长时间运行 OOM
2. **update_task/update_agent 对未知 ID 静默忽略** — 调用方不知道失败了
3. **`_maybe_snapshot` 每次 mutation 都触发** — 高频写入时浪费 IO

### P1 (本迭代修复)
4. **缺少任务优先级** — Director 无法按优先级调度
5. **缺少任务 deadline** — 任务可能永远不超时
6. **sync/async 桥接脆弱** — `_run_sync` 用线程池 + asyncio.run() 嵌套
7. **Broad except Exception** — 3处，吞掉具体错误信息

### P2 (后续)
8. **快照 quality** — 缺少聚合信号（如 avg_completion_time, success_rate_by_type）
9. **Budget 仅全局** — 无 per-task 或 per-agent-type 预算
10. **StateBoard 过大** — 应拆分为 TaskTracker, AgentTracker, ArtifactTracker, EventLog 等

---

## Runner 问题清单

### P0 (立即修复)
1. **协作模式无迭代上限** — coder ↔ reviewer 无限循环
2. **协作模式用 asyncio.sleep(2) 轮询** — 应改为事件驱动
3. **Transcript 内存无界** — _SessionStore 永不清理

### P1 (本迭代修复)
4. **并发限制硬编码** — _spawn_sem=3, MAX_CONCURRENT_RESIDENTS=2
5. **Resident 健康阈值硬编码** — 120s，不同 agent 类型可能需要不同阈值
6. **错误恢复薄弱** — Director 失败 → 无第三方案
7. **`_wrapped` 闭包捕获** — 虽然用了默认参数技巧，但可读性差

### P2 (后续)
8. **无 Agent 池/预热** — 每次冷启动
9. **无背压机制** — 只靠 semaphore 限流，无队列深度控制
10. **Runner 过大** — 应拆分为 AgentSpawner, CollaborationLoop, DirectorBridge 等

---

## 改进优先级

本轮实施 P0+P1:
1. TaskNode 添加 priority + deadline
2. 协作模式添加 max_iterations 熔断
3. 并发限制改为构造函数参数
4. 修复静默吞错
5. 事件日志上限
6. Resident 连续错误熔断

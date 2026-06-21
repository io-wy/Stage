# 戏台 (Xitai) 目录结构规范 v1.0

```
src/openagents_orchestration/
├── __init__.py                 # 包级统一导出（懒加载）
│
├── core/                       # === 单项目核心引擎 ===
│   ├── __init__.py
│   ├── runner.py               # OrchestratorRunner
│   ├── state_board.py          # StateBoard + Event + AgentState + Budget
│   ├── sub_state_board.py      # SubStateBoard（Team 子面板）
│   ├── resident.py             # ResidentAgent + ResidentState
│   └── collaboration.py        # 协作信号（CollaborationSignal）
│
├── projects/                 # === 企业级多项目扩展 ===
│   ├── __init__.py
│   ├── global_orchestrator.py  # GlobalOrchestrator
│   ├── project.py              # Project + ProjectStatus
│   ├── team.py                 # Team + TeamSpec + TeamStatus
│   ├── human_channel.py        # HumanChannel + HumanQuestion/HumanMessage
│   ├── monitor_agent.py        # MonitorAgent（主动心跳）
│   ├── security.py             # CapabilityToken + AgentIdentity + AuditLog
│   ├── events.py               # OrchestrationEvent
│   └── metrics.py              # OrchestrationMetrics（Prometheus 格式）
│
├── transport/                  # === 通信层 ===
│   ├── __init__.py
│   ├── channel_policy.py       # ChannelPolicy + 默认策略
│   ├── routing.py              # RoutingTable + RouteEntry
│   ├── matrix_transport.py     # Matrix 传输后端
│   └── mailbox/
│       ├── __init__.py
│       ├── base.py             # Mailbox ABC
│       ├── memory.py           # InMemoryMailbox
│       └── redis.py            # RedisMailbox
│
├── models/                     # === 数据模型（跨层共享） ===
│   ├── __init__.py
│   ├── task.py                 # TaskGraph + TaskNode + TaskStatus
│   ├── delivery.py             # DeliveryReport + TaskResult
│   ├── message.py              # StructuredMessage + MessageHeader
│   └── trace.py                # TraceContext
│
├── patterns/                   # === Agent 行为模式 ===
│   ├── __init__.py
│   ├── corecoder.py            # CoreCoderPattern
│   ├── director.py             # DirectorPattern
│   └── team_leader.py          # TeamLeaderPattern
│
├── tools/                      # === 工具插件 ===
│   ├── __init__.py             # 工具注册中心
│   ├── corecoder/              # 文件/代码工具
│   │   ├── read_file.py
│   │   ├── write_file.py
│   │   ├── edit_file.py
│   │   ├── bash_tool.py
│   │   ├── glob_tool.py
│   │   ├── grep_tool.py
│   │   ├── apply_patch.py
│   │   ├── semantic_edit.py
│   │   ├── todo.py
│   │   ├── run_claude_code.py
│   │   └── web_search.py
│   ├── director/               # 导演调度工具
│   │   ├── spawn_agent.py
│   │   ├── replan.py
│   │   ├── finalize.py
│   │   ├── show_state.py
│   │   ├── ask_human.py
│   │   ├── check_messages.py
│   │   ├── send_message.py
│   │   ├── recover_task.py
│   │   └── correct_task_status.py
│   ├── monitor/                # 监控工具
│   │   ├── inspect_state.py
│   │   ├── analyze_event_pattern.py
│   │   ├── diagnose_agent.py
│   │   ├── predict_budget.py
│   │   ├── send_alert.py
│   │   ├── verify_alert_effectiveness.py
│   │   └── check_dlq.py
│   ├── resident/               # 常驻 Agent 管理工具
│   │   ├── spawn_resident.py
│   │   ├── stop_resident.py
│   │   ├── read_resident_state.py
│   │   └── send_to_resident.py
│   ├── github/                 # GitHub 工具集
│   └── run_skill.py            # Skill 执行
│
├── observability/              # === 可观测性 ===
│   ├── __init__.py
│   └── health_monitor.py       # 被动健康监控
│
├── persistence/                # === 持久化 ===
│   ├── __init__.py
│   ├── event_recorder.py       # JSONL 事件记录
│   ├── event_replayer.py       # 事件回放
│   ├── state_snapshotter.py    # 状态快照
│   └── session_resumer.py      # 会话恢复
│
├── reporting/                  # === 报告生成 ===
│   ├── __init__.py
│   ├── summarizer.py
│   ├── verifier.py
│   └── recovery.py
│
├── store/                      # === 存储抽象 ===
│   ├── __init__.py
│   └── artifact_store.py       # ArtifactStore + LocalArtifactStore
│
└── utils/                      # === 通用工具 ===
    ├── __init__.py
    ├── runtime_compat.py       # SDK 兼容层
    ├── structured_generate.py  # 结构化生成
    └── semantic_edit.py        # 语义编辑
```

## 设计原则

1. **分层边界清晰**：core（单项目）→ projects（多项目）→ transport（通信）→ models（数据）
2. **单一职责**：每个模块只做一件事，文件名即职责
3. **向下依赖**：上层可依赖下层，下层不依赖上层
   - `projects/` 可导入 `core/`、`models/`、`transport/`
   - `core/` 可导入 `models/`、`transport/`
   - `tools/` 可导入 `core/`、`models/`
   - 禁止循环依赖
4. **__init__.py 统一导出**：每个子包的 `__init__.py` 导出该层所有公共 API
5. **测试平行结构**：`tests/` 目录与 `src/` 保持相同结构

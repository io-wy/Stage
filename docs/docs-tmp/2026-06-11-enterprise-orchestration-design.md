# 戏台企业级编排架构设计（完整版）

参考 HiClaw 的 Manager-Workers 编排思想，在戏台现有 Director-Agent 架构上引入 Team 层级自治，支撑企业级多项目协作场景。

**设计目标**：全局 Director 保持唯一视野，中下层引入 Team 自治，Project 级别隔离，Human 全链路可见。

**版本**：v2.0（基于当前已实现的 Mailbox v2 + 分布式追踪 + 路由表）

---

## 1. 架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                     GlobalOrchestrator                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ GlobalDirector│  │   Monitor    │  │   HumanChannel       │  │
│  │   (Pattern)   │  │   Agent      │  │   (Gateway)          │  │
│  └──────┬────────┘  └──────┬───────┘  └──────────┬───────────┘  │
│         │                  │                      │              │
│         └──────────────────┼──────────────────────┘              │
│                            │                                     │
│    ┌───────────────────────┼───────────────────────┐             │
│    ▼                       ▼                       ▼             │
│  Project A               Project B               Project C       │
│  ├─ StateBoard           ├─ StateBoard           ...             │
│  ├─ Budget               ├─ Budget                              │
│  ├─ ArtifactStore        ├─ ArtifactStore                       │
│  ├─ TaskGraph            ├─ TaskGraph                            │
│  ├─ Team A1              └─ Team B1                              │
│  │   ├─ TeamLeader                                                    │
│  │   ├─ coder-A1-1                                                    │
│  │   └─ reviewer-A1-1                                                 │
│  └─ Team A2                                                            │
│      ├─ TeamLeader                                                     │
│      └─ ...                                                            │
└─────────────────────────────────────────────────────────────────┘
```

### 核心概念

| 概念 | 定义 | 责任边界 |
|------|------|---------|
| **GlobalOrchestrator** | 全局编排器入口 | 管理多 Project、全局预算、Monitor、HumanChannel |
| **Project** | 独立执行单元 | 有自己的 StateBoard、Budget、TaskGraph、ArtifactStore、Teams |
| **Team** | Project 内的子执行单元 | 由 TeamLeader + Workers 组成，处理 subgraph |
| **TeamLeader** | 子级统筹 Agent | 管理 team 内部 TaskGraph 调度，对 GlobalDirector 汇报 |
| **Worker** | 战术执行 Agent | coder / reviewer / tester 等，由 TeamLeader 调度 |
| **ArtifactStore** | 共享存储抽象 | Project 级 + Team 级命名空间隔离 |
| **ChannelPolicy** | 通信权限控制 | 决定谁可以给谁发消息 |
| **HumanChannel** | 人机交互网关 | 支持 human 主动/被动参与，全链路可见 |
| **Monitor** | 跨 Project 健康监控 | 主动心跳、超时检测、异常上报 |

### 与现有架构映射

| 现有 | 演进后 | 兼容性策略 |
|------|--------|-----------|
| `OrchestratorRunner` | `GlobalOrchestrator`（管理多 Project） | `OrchestratorRunner` 保留为单 Project 兼容别名 |
| `StateBoard` | `Project` 的成员 | 接口不变，新增 project/team 上下文字段 |
| `DirectorPattern` | `GlobalDirectorPattern` + `TeamLeaderPattern` | TeamLeader 继承 DirectorPattern，限制工具集 |
| `TaskGraph` | 支持嵌套 `subgraph` | `TaskNode` 新增可选 `subgraph`、`team_id` |
| `ResidentAgent` | 四态生命周期 | 新增 `UPDATING`，保留 `RUNNING/SLEEPING/STOPPED` |
| `Mailbox v2` | Project/Team 命名空间隔离 | 路由地址扩展为 `project_id:agent_id` 或 `team_id:agent_id` |

---

## 2. 数据模型详细设计

### 2.1 Project 模型

```python
@dataclass
class Project:
    project_id: str
    objective: str
    state_board: StateBoard
    budget: Budget
    work_dir: Path
    teams: dict[str, Team] = field(default_factory=dict)
    artifact_store: ArtifactStore = field(default_factory=lambda: LocalArtifactStore(".artifacts"))
    human_channel: HumanChannel = field(default_factory=HumanChannel)
    status: ProjectStatus = ProjectStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)

    # -- lifecycle --
    async def start(self) -> None: ...
    async def pause(self) -> None: ...
    async def resume(self) -> None: ...
    async def terminate(self, reason: str = "") -> DeliveryReport: ...

    # -- team management --
    def create_team(self, team_spec: TeamSpec) -> Team: ...
    def get_team(self, team_id: str) -> Team | None: ...
    def list_teams(self, status: TeamStatus | None = None) -> list[Team]: ...

    # -- snapshot --
    def to_dict(self) -> dict[str, Any]: ...
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Project: ...
```

**ProjectStatus**: `PENDING | RUNNING | PAUSED | COMPLETED | FAILED | TERMINATED`

### 2.2 Team 模型

```python
@dataclass
class Team:
    team_id: str
    project_id: str
    name: str
    sub_state_board: SubStateBoard
    leader_id: str | None = None
    workers: dict[str, ResidentAgent] = field(default_factory=dict)
    artifact_prefix: str = ""
    channel_policy: ChannelPolicy = field(default_factory=lambda: DEFAULT_TEAM_POLICY)
    status: TeamStatus = TeamStatus.IDLE

    # -- lifecycle --
    async def start(self, runner: Any) -> None: ...
    async def pause(self) -> None: ...
    async def resume(self) -> None: ...
    async def stop(self) -> None: ...

    # -- worker management --
    async def spawn_worker(self, agent_type: str, task_id: str) -> ResidentAgent: ...
    async def stop_worker(self, worker_id: str) -> None: ...
    def get_worker(self, worker_id: str) -> ResidentAgent | None: ...

    # -- messaging --
    async def route_message(self, msg: StructuredMessage) -> bool: ...

@dataclass
class TeamSpec:
    name: str
    agent_types: list[str] = field(default_factory=lambda: ["coder", "reviewer"])
    max_workers: int = 5
    channel_policy: ChannelPolicy | None = None
```

**TeamStatus**: `IDLE | RUNNING | PAUSED | STOPPED | ERROR`

### 2.3 TaskNode 扩展

```python
@dataclass
class TaskNode:
    task_id: str
    description: str
    agent_type: str
    status: TaskStatus = TaskStatus.PENDING
    dependencies: list[str] = field(default_factory=list)
    input_context: str = ""
    expected_artifacts: list[str] = field(default_factory=list)
    actual_artifacts: list[str] = field(default_factory=list)
    assigned_agent: str = ""
    team_id: str = ""           # NEW: 所属 team
    subgraph: TaskGraph | None = None  # NEW: team 内部子图
    iteration_history: list[dict] = field(default_factory=list)
```

**Team 类型节点约定**：
- `agent_type == "team"` 表示这是一个 team 节点
- `subgraph` 非空，由 TeamLeader 进一步分解
- 执行时 spawn TeamLeader，TeamLeader 消费 subgraph

### 2.4 ChannelPolicy 模型

```python
@dataclass
class ChannelPolicy:
    """通信权限控制。

    rules 格式：{sender_pattern: [recipient_pattern, ...]}
    pattern 支持：
    - 精确 agent_id，如 "director"
    - 通配符前缀，如 "coder-*"
    - 特殊标记 "*" 表示任意
    - 角色标记 "*_leader" 表示任意 team leader
    """
    rules: dict[str, list[str]] = field(default_factory=dict)

    def allows(self, sender: str, recipient: str) -> bool: ...
    def assert_allowed(self, sender: str, recipient: str) -> None: ...

DEFAULT_GLOBAL_POLICY = ChannelPolicy({
    "director": {"*"},
    "*_leader": {"director", "*_leader"},
    "coder-*": {"reviewer-*", "*_leader", "director"},
    "reviewer-*": {"coder-*", "*_leader", "director"},
    "tester-*": {"coder-*", "reviewer-*", "*_leader", "director"},
    "monitor-*": {"director", "*_leader"},
})
```

**跨 Project/Team 通信规则**：
- 默认情况下，不同 Project 的 Agent 不能直接通信
- 需要显式配置 `cross_project_rules`
- GlobalDirector 可以转发跨 Project 消息

### 2.5 HumanChannel 模型

```python
@dataclass
class HumanQuestion:
    qid: str
    project_id: str
    team_id: str | None
    from_agent: str
    question: str
    options: str
    answer: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    answered_at: datetime | None = None

class HumanChannel:
    def __init__(self):
        self._questions: dict[str, HumanQuestion] = {}
        self._messages: list[dict] = []  # human 主动消息

    async def ask(
        self,
        project_id: str,
        from_agent: str,
        question: str,
        *,
        team_id: str | None = None,
        options: str = "",
    ) -> str: ...

    async def answer(self, qid: str, answer: str) -> bool: ...

    async def post_message(
        self,
        from_human: str,
        content: str,
        *,
        project_id: str = "",
        team_id: str = "",
        target_agent: str = "",
    ) -> None: ...

    def get_pending_questions(self, project_id: str | None = None) -> list[HumanQuestion]: ...
    def get_human_activity(self, project_id: str | None = None) -> list[dict]: ...
```

---

## 3. 组件接口完整定义

### 3.1 GlobalOrchestrator

```python
class GlobalOrchestrator:
    def __init__(
        self,
        config_path: str | Path,
        *,
        persist_dir: str | None = None,
        collaborative_mode: str = "auto",
        enable_monitor: bool = True,
    ):
        self._config_path = Path(config_path)
        self._projects: dict[str, Project] = {}
        self._global_budget: Budget = Budget(token_limit=-1, time_limit_s=-1)
        self._monitor: MonitorAgent | None = None
        self._human_channel: HumanChannel = HumanChannel()
        self._persist_dir = Path(persist_dir) if persist_dir else None
        self._collaborative_mode = collaborative_mode

    # -- public API --
    async def run(
        self,
        objective: str,
        *,
        project_id: str | None = None,
        budget: Budget | None = None,
        work_dir: str | None = None,
        team_specs: list[TeamSpec] | None = None,
    ) -> DeliveryReport: ...

    async def create_project(
        self,
        objective: str,
        *,
        budget: Budget | None = None,
        work_dir: str | None = None,
        team_specs: list[TeamSpec] | None = None,
    ) -> Project: ...

    async def pause_project(self, project_id: str) -> None: ...
    async def resume_project(self, project_id: str) -> None: ...
    async def terminate_project(self, project_id: str, reason: str = "") -> DeliveryReport: ...

    def get_project(self, project_id: str) -> Project | None: ...
    def list_projects(self, status: ProjectStatus | None = None) -> list[Project]: ...

    # -- internal --
    async def _initial_decompose(self, objective: str) -> TaskGraph: ...
    def _allocate_budget(self, requested: Budget | None) -> Budget: ...
    async def _global_react_loop(self) -> None: ...
    async def _dispatch_team(self, project: Project, task: TaskNode) -> None: ...
```

### 3.2 GlobalDirectorPattern

```python
class GlobalDirectorPattern(DirectorPattern):
    """全局导演模式。

    与 DirectorPattern 相比：
    - show_state 返回 Project 级聚合快照
    - 工具集包含 project/team 级调度工具
    - 不直接处理 team 内部任务
    """

    _PRINCIPLES = GLOBAL_DIRECTOR_PRINCIPLES

    async def _should_continue_step(self, step: int) -> bool: ...
    async def _should_accept_text_response(self, text: str) -> bool: ...
```

### 3.3 TeamLeaderPattern

```python
class TeamLeaderPattern(DirectorPattern):
    """Team 内部导演模式。

    与 GlobalDirectorPattern 相比：
    - max_steps=30
    - 工具集不含 ask_human（human escalation 上报 GlobalDirector）
    - show_state 返回 team 内部 SubStateBoard snapshot
    - 可 spawn_agent（coder/reviewer/tester）
    """

    _PRINCIPLES = TEAM_LEADER_PRINCIPLES

    async def _should_continue_step(self, step: int) -> bool: ...
```

### 3.4 MonitorAgent

```python
class MonitorAgent:
    HEARTBEAT_INTERVAL_S = 60.0
    HEARTBEAT_TIMEOUT_S = 30.0

    def __init__(self, orchestrator: GlobalOrchestrator):
        self._orchestrator = orchestrator
        self._task: asyncio.Task[Any] | None = None
        self._last_heartbeat: dict[str, float] = {}

    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    async def _heartbeat_loop(self) -> None: ...
    async def _send_heartbeat(self, resident: ResidentAgent) -> None: ...
    async def _handle_timeout(self, project_id: str, team_id: str, resident_id: str) -> None: ...
```

### 3.5 扩展 StateBoard

```python
class StateBoard:
    def __init__(
        self,
        objective: str,
        budget: Budget | None = None,
        *,
        project_id: str = "",
        team_id: str = "",
        channel_policy: ChannelPolicy | None = None,
        human_channel: HumanChannel | None = None,
        ...
    ): ...

    # -- project/team context --
    @property
    def project_id(self) -> str: ...
    @property
    def team_id(self) -> str: ...

    # -- human integration --
    async def ask_human(
        self,
        question: str,
        *,
        options: str = "",
        from_agent: str = "",
    ) -> str: ...

    async def post_human_message(
        self,
        human_id: str,
        content: str,
        *,
        target_team: str = "",
    ) -> None: ...
```

---

## 4. 事件总线设计

企业级场景下，Project/Team 之间需要松耦合的事件通知。

```python
class OrchestrationEvent(StrEnum):
    PROJECT_CREATED = "project.created"
    PROJECT_STARTED = "project.started"
    PROJECT_PAUSED = "project.paused"
    PROJECT_COMPLETED = "project.completed"
    PROJECT_FAILED = "project.failed"
    TEAM_CREATED = "team.created"
    TEAM_STARTED = "team.started"
    TEAM_COMPLETED = "team.completed"
    TEAM_FAILED = "team.failed"
    AGENT_SPAWNED = "agent.spawned"
    AGENT_STOPPED = "agent.stopped"
    AGENT_HEARTBEAT_TIMEOUT = "agent.heartbeat_timeout"
    TASK_STATUS_CHANGED = "task.status_changed"
    HUMAN_QUESTION_ASKED = "human.question_asked"
    HUMAN_QUESTION_ANSWERED = "human.question_answered"
    HUMAN_MESSAGE_POSTED = "human.message_posted"
    DLQ_MESSAGE_DETECTED = "dlq.message_detected"
    CROSS_PROJECT_MESSAGE = "cross_project.message"
```

**事件路由规则**：
- Project 内部事件：默认留在 Project 内
- Team 失败/预算耗尽：上报 GlobalDirector
- Human 消息：投递到对应 Project/Team
- 跨 Project 消息：经 GlobalDirector 转发

---

## 5. 持久化与恢复

### 5.1 持久化分层

| 层级 | 内容 | 存储 | 触发时机 |
|------|------|------|---------|
| 事件日志 | 所有 mutation 事件 | `events.jsonl` / PostgreSQL | 每次 mutation |
| 状态快照 | Project/StateBoard 全量状态 | `snapshot-{seq}.json` / DB | 每 N 次 mutation |
| Resident transcript | 常驻 Agent 对话历史 | `residents/{id}.json` | 每次消息处理 |
| Artifact | 代码/文件产物 | ArtifactStore（Local/S3/MinIO） | 产生时 |
| Human 对话 | 问答记录 | `human.jsonl` / DB | 每次交互 |

### 5.2 PostgreSQL 持久化方案（可选）

```python
class PostgresStateStore:
    async def save_project(self, project: Project) -> None: ...
    async def load_project(self, project_id: str) -> Project | None: ...
    async def list_projects(self, status: ProjectStatus | None = None) -> list[Project]: ...
    async def append_event(self, project_id: str, event: dict) -> None: ...
    async def get_events_after(self, project_id: str, seq: int) -> list[dict]: ...
```

**默认策略**：
- 开发/测试：JSONL + 快照（当前方式）
- 生产：PostgreSQL + 对象存储

### 5.3 恢复流程

```
1. GlobalOrchestrator.resume(project_id)
2. 从 store 加载 Project 快照
3. 回放 seq 之后的事件
4. 恢复 Resident transcript 路径
5. 重新启动 Monitor
6. 对 RUNNING 状态的 team 恢复运行
```

---

## 6. API 设计

### 6.1 Python API

```python
orchestrator = GlobalOrchestrator("agent.json")

# 提交一个 objective
report = await orchestrator.run(
    "Build a FastAPI TODO API",
    budget=Budget(token_limit=100_000, time_limit_s=3600),
    team_specs=[
        TeamSpec(name="backend", agent_types=["coder", "reviewer", "tester"]),
        TeamSpec(name="docs", agent_types=["coder"]),
    ],
)

# 管理多项目
project = await orchestrator.create_project("Fix auth bug")
await orchestrator.pause_project(project.project_id)
await orchestrator.resume_project(project.project_id)
```

### 6.2 REST API（未来）

```
POST   /api/v1/projects              # 创建项目
GET    /api/v1/projects              # 列出项目
GET    /api/v1/projects/{id}         # 项目状态
POST   /api/v1/projects/{id}/pause   # 暂停
POST   /api/v1/projects/{id}/resume  # 恢复
DELETE /api/v1/projects/{id}         # 终止
POST   /api/v1/projects/{id}/human   # human 回复/主动消息
GET    /api/v1/projects/{id}/events  # 事件流
```

---

## 7. 配置模型（agent.json 扩展）

```json
{
  "agents": [
    {
      "id": "director",
      "pattern": {
        "impl": "openagents_orchestration.patterns.director.GlobalDirectorPattern"
      },
      "tools": [
        {"id": "show_state", ...},
        {"id": "spawn_team", ...},
        {"id": "pause_project", ...},
        {"id": "ask_human", ...},
        {"id": "check_dlq", ...}
      ]
    },
    {
      "id": "team_leader",
      "pattern": {
        "impl": "openagents_orchestration.patterns.team_leader.TeamLeaderPattern"
      },
      "tools": [
        {"id": "show_state", ...},
        {"id": "spawn_agent", ...},
        {"id": "send_message", ...},
        {"id": "stop_resident", ...}
      ]
    }
  ],
  "orchestration": {
    "channel_policy": {
      "rules": {
        "director": ["*"],
        "*_leader": ["director", "*_leader"],
        "coder-*": ["reviewer-*", "*_leader"],
        "reviewer-*": ["coder-*", "*_leader"]
      }
    },
    "monitor": {
      "enabled": true,
      "heartbeat_interval_s": 60,
      "heartbeat_timeout_s": 30
    },
    "human_channel": {
      "enabled": true,
      "allow_proactive_posts": true
    }
  }
}
```

---

## 8. 通信协议

### 8.1 地址格式

```
# 同一 Project 内
agent_id              ->  "coder-t1"

# 跨 Team
team_id:agent_id      ->  "team-backend:coder-t1"

# 跨 Project
project_id:agent_id   ->  "proj-abc123:coder-t1"
project_id:team_id:agent_id
```

### 8.2 跨 Project 消息转发

```python
# coder in Project A 想给 Project B 的 reviewer 发消息
msg = StructuredMessage.from_text(
    sender="proj-A:coder-t1",
    recipient="proj-B:reviewer-t2",
    text="Please review my auth change",
)

# ChannelPolicy 检查：默认不允许跨 Project
# 需要 GlobalDirector 显式授权或配置 cross_project_rules

# 若授权，GlobalDirector 转发到目标 Project 的 mailbox
```

### 8.3 Human 消息格式

```python
{
    "type": "human.post",
    "human_id": "alice",
    "project_id": "proj-abc123",
    "team_id": "team-backend",
    "target_agent": "director",
    "content": "Please prioritize the auth task",
    "ts": 1718000000.0,
}
```

---

## 9. 安全模型

### 9.1 身份认证

- 每个 Agent 启动时获得 `AgentIdentity`（agent_id + capability token）
- Mailbox 接收消息时校验 sender 身份
- 跨 Project 消息必须经 GlobalDirector 签名

### 9.2 能力令牌

```python
@dataclass
class CapabilityToken:
    issuer: str
    bearer: str
    actions: list[str]
    scope: list[str]
    expires_at: datetime
    signature: bytes
```

### 9.3 审计日志

- 所有消息发送/接收记录到 audit log
- 所有任务状态变更记录到 audit log
- 所有 human 交互记录到 audit log
- 保留时间：默认 30 天

---

## 10. 可观测性

### 10.1 Metrics

| Metric | Type | Labels |
|--------|------|--------|
| `orchestration_projects_total` | gauge | status |
| `orchestration_teams_total` | gauge | project_id, status |
| `orchestration_agents_total` | gauge | project_id, team_id, status |
| `orchestration_tasks_total` | gauge | project_id, status |
| `orchestration_messages_delivered_total` | counter | project_id, topology |
| `orchestration_messages_dlq_total` | counter | project_id, agent_id |
| `orchestration_heartbeat_latency_ms` | histogram | project_id, agent_id |
| `orchestration_budget_tokens_used` | gauge | project_id |
| `orchestration_llm_calls_total` | counter | project_id, agent_id |
| `orchestration_llm_latency_ms` | histogram | project_id, agent_id |

### 10.2 Tracing

- 每个 Project 一个 root trace
- Team spawn 创建 child span
- Agent spawn 创建 child span
- 消息传递创建 span（sender -> mailbox -> receiver）
- LLM 调用创建 span
- 输出格式：OpenTelemetry

### 10.3 Logging

- 结构化 JSON 日志
- 字段：`project_id`, `team_id`, `agent_id`, `task_id`, `trace_id`, `event_type`
- 分级：DEBUG / INFO / WARNING / ERROR / CRITICAL

---

## 11. 部署架构

### 11.1 单机部署（当前延续）

```
[User] -> [GlobalOrchestrator] -> [Project(s)] -> [Team(s)] -> [Workers]
                │
                └── [Monitor]
                └── [HumanChannel]
```

### 11.2 企业级多实例部署（未来）

```
[Load Balancer]
      │
      ├── [GlobalOrchestrator Instance 1]
      ├── [GlobalOrchestrator Instance 2]
      │
      └── [Shared PostgreSQL + Redis + Object Storage]
```

**关键设计**：
- GlobalOrchestrator 实例无状态
- Project 状态持久化到 PostgreSQL
- Mailbox 持久化到 Redis
- Artifact 存储到 S3/MinIO
- Leader 选举：用于 Monitor 单实例（可选）

---

## 12. 实施路线图

| Phase | 内容 | 工期 | 关键产出 |
|-------|------|------|---------|
| 0 | **ChannelPolicy + 通信权限** | 2d | `channel_policy.py`，`send_message` 集成权限校验 |
| 1 | **Project 抽象** | 3d | `Project` 类，`OrchestratorRunner` 内部持默认 Project，API 兼容 |
| 2 | **HumanChannel 升级** | 2d | `HumanChannel` 类，支持 human 主动消息，按 project/team 路由 |
| 3 | **Team / TeamLeader** | 5d | `Team` 类，`TeamLeaderPattern`，`TaskNode.subgraph`，协作模式下沉 |
| 4 | **GlobalOrchestrator** | 4d | 多 Project 管理，全局预算分配，项目生命周期 |
| 5 | **Monitor 主动心跳** | 2d | `MonitorAgent` 主动 heartbeat，超时检测 |
| 6 | **Agent 四态生命周期** | 2d | `UPDATING` 状态，配置热重载 |
| 7 | **PostgreSQL 持久化（可选）** | 5d | `PostgresStateStore`，企业级恢复 |
| 8 | **可观测性** | 3d | Metrics + OpenTelemetry + 结构化日志 |
| 9 | **REST API + 部署** | 5d | FastAPI 网关，Docker Compose / K8s 部署 |
| **总计** | | **~33d** | |

**建议起点**：Phase 0（ChannelPolicy），因为：
- 改动面小，可独立验证
- 为后续多 Project/Team 的通信隔离打基础
- 不影响现有单 Project 行为

---

## 13. 风险与回滚

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 架构改造引入回归 | 高 | 每阶段必须有测试覆盖，保留 `OrchestratorRunner` 兼容层 |
| 状态持久化性能瓶颈 | 中 | 异步事件追加 + 批量快照，优先 JSONL，DB 可选 |
| 跨 Team 通信死锁 | 中 | ChannelPolicy + 超时机制 + DLQ |
| Human 消息淹没 Director | 中 | HumanChannel 聚合展示，Director prompt 限制 |
| Monitor 误报 | 低 | 可配置阈值，支持静音期 |

**回滚策略**：
- 每个 Phase 独立可回滚
- 保留 `legacy_mode` 开关
- 通过 feature flag 逐步启用新功能

---

## 14. 与当前已实现能力的衔接

当前已落地的能力可直接复用：

| 已落地能力 | 在企业级架构中的位置 |
|-----------|-------------------|
| `Mailbox v2` | Project/Team 内部 Agent 通信基础 |
| `RoutingTable` | Team 内消息路由 + 跨 Team 路由 |
| `TraceContext` | Project 级 trace root + Team/Agent span |
| `EventRecorder` | Project 事件日志基础 |
| `StateSnapshotter` | Project 快照恢复基础 |
| `DLQ + check_dlq` | 跨 Project 死信监控 |
| `ArtifactStore` | Project/Team 共享存储抽象 |

---

## 15. 参考来源

- [HiClaw Architecture v1.1.0](https://github.com/agentscope-ai/HiClaw/blob/main/docs/architecture.md)
- [HiClaw Declarative Resource Management](https://github.com/agentscope-ai/HiClaw/blob/main/docs/declarative-resource-management.md)
- [HiClaw v1.1.0 Release](https://github.com/agentscope-ai/HiClaw/blob/main/blog/hiclaw-1.1.0-release.md)

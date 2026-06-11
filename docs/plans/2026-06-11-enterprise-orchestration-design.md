# 戏台企业级编排架构设计

参考 HiClaw 的 Manager-Workers 编排思想，在戏台现有 Director-Agent 架构上引入 Team 层级自治，支撑企业级多项目协作场景。

**设计目标**：全局 Director 保持唯一视野，中下层引入 Team 自治，Project 级别隔离，Human 全链路可见。

---

## 1. 架构总览

```
GlobalDirector (全局统筹)
    ├── Project A (独立 StateBoard + Budget)
    │     ├── Team A1 (TeamLeader + Workers)
    │     └── Team A2 (TeamLeader + Workers)
    ├── Project B (独立 StateBoard + Budget)
    │     └── Team B1 (TeamLeader + Workers)
    └── Monitor (跨 Project 心跳监控)
```

### 核心概念

| 概念 | 定义 |
|------|------|
| **Project** | 独立执行单元，有自己的 StateBoard、Budget、TaskGraph、ArtifactStore |
| **Team** | Project 内的子执行单元，由 TeamLeader + Workers 组成 |
| **TeamLeader** | 子级统筹 Agent，管理内部 TaskGraph 调度 |
| **ArtifactStore** | Team 级共享存储抽象 |
| **ChannelPolicy** | 通信权限控制 |

### 与现有架构映射

| 现有 | 演进后 |
|------|--------|
| `OrchestratorRunner` | `GlobalOrchestrator`（管理多 Project） |
| `StateBoard` | `Project` 的成员（每个 Project 一个） |
| `DirectorPattern` | `GlobalDirectorPattern` + `TeamLeaderPattern` |
| `TaskGraph` | 支持嵌套 `subgraph` |
| `ResidentAgent` | 四态生命周期 `RUNNING/SLEEPING/STOPPED/UPDATING` |

---

## 2. 组件详细设计

### 2.1 GlobalOrchestrator

```python
class GlobalOrchestrator:
    def __init__(self, config_path, *, persist_dir=None, collaborative_mode="auto"):
        self._projects: dict[str, Project] = {}
        self._global_budget: Budget
        self._monitor: MonitorAgent

    async def run(self, objective, *, project_id=None, budget=None, work_dir=None):
        project = Project(objective, budget=self._allocate_budget(budget), work_dir=work_dir)
        self._projects[project.project_id] = project
        # 启动 GlobalDirector 调度
```

### 2.2 Project

```python
class Project:
    def __init__(self, objective, budget, work_dir):
        self.project_id = f"proj-{uuid.uuid4().hex[:8]}"
        self.state_board = StateBoard(objective, budget=budget)
        self.work_dir = Path(work_dir)
        self.teams: dict[str, Team] = {}
        self.artifact_store: ArtifactStore = LocalArtifactStore(work_dir / ".artifacts")
```

### 2.3 Team / TeamLeader

```python
class Team:
    def __init__(self, team_spec, project, runner):
        self.team_id = f"{project.project_id}-{team_spec.name}"
        self.sub_state_board = SubStateBoard(parent=project.state_board)
        self.artifact_store = ProjectArtifactStore(
            parent=project.artifact_store,
            prefix=f"teams/{self.team_id}/"
        )
```

**TeamLeaderPattern** 继承 `DirectorPattern`：
- `max_steps=30`（GlobalDirector 是 100）
- 工具集不含 `ask_human`（human escalation 上报 GlobalDirector）
- `show_state` 返回 team 内部 snapshot

### 2.4 Agent 生命周期四态化

```python
class AgentLifecycle(StrEnum):
    RUNNING = "running"
    SLEEPING = "sleeping"   # 休眠：保留状态，暂停 LLM 调用
    STOPPED = "stopped"     # 停止：释放资源
    UPDATING = "updating"   # 更新中：配置热重载
```

**对协作模式的影响**：
- 当前：reviewer 用完 `stop()`，下次修复后重新 spawn（冷启动开销）
- 新：reviewer 用完 `sleep()`，coder 修复后 `wake()`（保留上下文）
- 最终 approve 后 `stop()`

### 2.5 心跳机制强化

**Monitor Agent 主动 heartbeat + Resident 被动响应**：

```python
class MonitorAgent:
    HEARTBEAT_INTERVAL_S = 60.0

    async def _heartbeat_loop(self):
        for project in self._projects.values():
            for team in project.teams.values():
                for resident in team.workers.values():
                    if resident.state.status in ("running", "idle"):
                        await self._send_heartbeat(resident)
```

Resident 收到 `HEARTBEAT[...]` 消息后回复当前进度。Monitor 检测 30s 超时，超时则上报 GlobalDirector。

### 2.6 Human-in-the-Loop 升级

当前：单向 `ask_human → reply_human`。

新设计：

```python
class HumanChannel:
    async def ask(self, question: str, ...) -> str: ...
    async def post_message(self, from_human: str, content: str, *, project_id="", team_id="") -> None: ...
    def get_conversation_log(self, project_id: str) -> list[dict]: ...
```

- Human 可主动向 project/team 发消息
- StateBoard 记录完整 `human_messages` 轨迹
- GlobalDirector `show_state` 暴露 `human_activity`（未回答的问题 + human 主动消息）

### 2.7 ArtifactStore 抽象

```python
class ArtifactStore(ABC):
    async def put(self, task_id: str, path: str, content: str | bytes) -> str: ...
    async def get(self, task_id: str, path: str) -> str | bytes: ...
    async def exists(self, task_id: str, path: str) -> bool: ...
```

默认 `LocalArtifactStore`（本地目录）。`write_file` 工具写入本地后自动 `push` 到 store。

### 2.8 ChannelPolicy

```python
@dataclass
class ChannelPolicy:
    allowed_senders: dict[str, set[str]]

DEFAULT_POLICY = ChannelPolicy({
    "director": {"*"},
    "*_leader": {"director"},
    "coder-*": {"reviewer-*", "*_leader"},
    "reviewer-*": {"coder-*", "*_leader"},
})
```

---

## 3. 数据流

```
User Objective
    │
    ▼
GlobalOrchestrator.run(objective)
    │
    ├── _initial_decompose(objective) → TaskGraph
    │       └── 顶层 task nodes 可能包含 team 节点 (type="team", subgraph=...)
    │
    ├── 创建 Project，分配子 Budget
    │
    ├── Project.add_tasks(TaskGraph)
    │
    ├── 对每个 PENDING 的 team 节点：
    │       spawn TeamLeader + 绑定 subgraph
    │       TeamLeader 内部再分解为 coder/reviewer
    │
    ├── 对非 team 节点：
    │       直接 spawn_agent（兼容现有行为）
    │
    └── 全局 ReAct 循环：
            show_state → 看到 project 级聚合快照
            → spawn team / finalize / ask_human
            team 内部自治，Director 不介入
```

### Team 内部数据流

```
TeamLeader ReAct 循环
    ├── show_state → team 内部 snapshot
    ├── spawn_agent(coder) → 绑定 task
    ├── 收到 coder TASK_REVIEW_READY → spawn_agent(reviewer)
    ├── 收到 reviewer TASK_APPROVED → 标记 task COMPLETED
    └── 收到 reviewer TASK_FIX_NEEDED → 唤醒 coder，发修复消息
```

---

## 4. 状态转换

### ResidentAgent 生命周期

```
        start()
           │
           ▼
    ┌──────────┐
    │ RUNNING  │◄──────┐
    └────┬─────┘       │
         │ sleep()      │ wake()
         ▼              │
    ┌──────────┐       │
    │ SLEEPING │───────┘
    └────┬─────┘
         │ stop()
         ▼
    ┌──────────┐
    │ STOPPED  │
    └──────────┘
```

### TaskNode 状态（Team 内部扩展）

```
PENDING ──► RUNNING ──► REVIEW ──► COMPLETED
                │           │
                ▼           ▼
             FAILED      FIX_NEEDED ──► RUNNING
```

---

## 5. 错误处理

| 场景 | 处理策略 |
|------|---------|
| Team 失败 | 回传到 Project StateBoard → GlobalDirector 看到 FAILED → replan/skip/ask_human |
| Budget 耗尽 | Project 级别 budget 耗尽 → Team 优雅停止 → GlobalDirector 汇总报告 |
| Agent stuck | Monitor 心跳超时 → 标记 FAILED → 尝试 graceful stop |
| 跨 Team 通信越权 | ChannelPolicy 拦截 → tool_result is_error=True |
| Artifact 冲突 | `.processing` marker（TTL 15min）→ 检测后阻止并发修改 |

---

## 6. 与现有架构的兼容性

| 兼容性策略 | 说明 |
|-----------|------|
| `agent.json` | 向后兼容，新增可选字段 `skills`、`channel_policy` |
| `OrchestratorRunner` | 保留现有类名作为别名，内部委托给 `GlobalOrchestrator` |
| `StateBoard` | 接口不变，新增 `human_messages`、`channel_policy` 字段 |
| `TaskNode` | 新增可选 `subgraph: TaskGraph`、`team_id: str` |
| 协作模式 | 完全保留，Team 内部可开启协作闭环 |

---

## 7. 测试策略

| 测试层级 | 覆盖内容 |
|---------|---------|
| 单元测试 | `ArtifactStore` 抽象、`ChannelPolicy`、Agent 生命周期转换 |
| 集成测试 | `GlobalOrchestrator` 多 project 调度、Team delegation、心跳超时 |
| E2E 测试 | 完整编排：objective → decomposition → team spawn → coder → reviewer → finalize |
| Mock 策略 | 所有测试 Mock LLM 调用，不依赖真实 API |

---

## 8. 参考来源

- [HiClaw Architecture v1.1.0](https://github.com/agentscope-ai/HiClaw/blob/main/docs/architecture.md)
- [HiClaw Declarative Resource Management](https://github.com/agentscope-ai/HiClaw/blob/main/docs/declarative-resource-management.md)
- [HiClaw v1.1.0 Release](https://github.com/agentscope-ai/HiClaw/blob/main/blog/hiclaw-1.1.0-release.md)

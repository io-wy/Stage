# 戏台持久化 + Checkpoint 设计方案

> 参考：Claude Code (append-only JSONL + parentUUID chain)、LangChain DeltaChannel、Durable Execution 论文

---

## 一、核心原则

1. **Append-only 是一切的基础** — 只追加，不原地修改。崩溃最多丢最后一条，不会损坏。
2. **Checkpoint 是存储操作，不是可靠性保证** — 持久化让 resume 成为可能，但自动恢复是另一层。
3. **Idempotent tool calls** — 重放时同一 tool_call 执行两次必须结果相同。

---

## 二、数据分三层

```
┌─────────────────────────────────────────────────┐
│                  Layer 1: Events                 │
│          append-only JSONL (per session)          │
│  每行: {uuid, parent_uuid, type, payload, ts}    │
│                                                   │
│  parent_uuid 形成链表，支持分支和 compaction       │
│  类型: user / assistant / tool_call / tool_result │
│        / summary / checkpoint / state_snapshot    │
├─────────────────────────────────────────────────┤
│              Layer 2: StateBoard Snapshots        │
│     periodic full snapshots (every K mutations)   │
│   JSON 文件: stateboard-{session-id}-{seq}.json   │
│                                                   │
│  存: tasks / agents / artifacts / budget / events │
│  压缩后 ~10-50KB, K=20 时对 ~500K token 会话     │
│  额外开销 < 2%                                   │
├─────────────────────────────────────────────────┤
│           Layer 3: Resident Transcripts           │
│    已存在: .residents/{resident-id}.json          │
│    仅需: add parent_uuid linking to main chain    │
└─────────────────────────────────────────────────┘
```

---

## 三、运行流程

### 写路径（正常运行时）

```
Director 每轮决策:
  → spawn_agent / replan / finalize
  → StateBoard.update_task() / update_agent() / etc.
    → log_event() 写入 Event 日志
    → if pending_events >= 10: flush_events()
      → append JSONL
    → if mutation_count % 20 == 0: save_checkpoint()
      → dump StateBoard snapshot as JSON file

Agent 每一步:
  → LLM generate → append {type: "llm_call", ...}
  → tool_call → append {type: "tool_call", ...}
  → tool_result → append {type: "tool_result", ...}
  → assistant_msg → append {type: "assistant", ...}
  → 每 5 步 flush 一次
```

### 恢复路径（resume 时）

```
OrchestratorRunner.resume(session_id):
  1. 读 JSONL，从 root 重建对话链
  2. 找最新的 StateBoard snapshot → 恢复到 checkpoint 时的状态
  3. 重放 checkpoint 后的 Event 日志 → 恢复到最新状态
  4. 检查最后一条 event:
     - 是 tool_call 但没有 tool_result → 标记 tool 待重试
     - 是 assistant 正常结束 → 从该处继续
     - 是中断 → Director 重新 show_state 继续
  5. 恢复所有 resident agents (从 JSONL 重建消息队列)
```

### 检测中断

```
根据 JSONL 最后几条消息判断:
  最后是 tool_call, 没有 tool_result → mid-tool
  最后是 user, 没有 assistant → interrupted_prompt
  最后是 assistant → completed_turn（正常结束）
  最后是 checkpiont + 空 → 正常完成
```

---

## 四、JSONL 格式

```
~/.xitai/sessions/{session-hash}/
├── {session-id}.jsonl         # 主对话链
├── {session-id}.meta.json     # 元数据（62KB head/tail window）
├── residents/
│   └── {resident-id}.jsonl    # 常驻 agent 独立链
└── checkpoints/
    ├── stateboard-{seq}.json  # StateBoard 快照
    └── stateboard-{seq}.index # 对应的 JSONL 行号
```

JSONL 每行:

```json
{"uuid": "a1", "parent_uuid": null, "type": "user", "payload": {"content": "Build a TODO API"}, "ts": 1712345678, "session_id": "s1"}
{"uuid": "a2", "parent_uuid": "a1", "type": "assistant", "payload": {"content": "I'll decompose this..."}, "ts": 1712345680, "session_id": "s1"}
{"uuid": "a3", "parent_uuid": "a2", "type": "tool_call", "payload": {"tool": "spawn_agent", "params": {"task_id": "t1"}}, "ts": 1712345690, "session_id": "s1"}
{"uuid": "a4", "parent_uuid": "a3", "type": "tool_result", "payload": {"tool": "spawn_agent", "result": {"status": "completed"}}, "ts": 1712345800, "session_id": "s1"}
{"uuid": "a5", "parent_uuid": "a4", "type": "checkpoint", "payload": {"seq": 1, "stateboard": "stateboard-1.json"}, "ts": 1712345801, "session_id": "s1"}
```

从 checkpoint 恢复时:

```
1. 读 stateboard-1.json → 恢复 StateBoard
2. 从 JSONL 找到 a5 的行号
3. 跳过 a5 之前的所有行（已被快照覆盖）
4. 从 a5 之后的行继续重放
```

---

## 五、到哪里开始改

### Step 1: EventRecorder — 独立的写日志模块

新建 `src/openagents_orchestration/persistence/event_recorder.py`:

```python
class EventRecorder:
    """Append-only JSONL recorder with parent_uuid chain."""
    
    def __init__(self, session_dir: Path, session_id: str):
        self._file = session_dir / f"{session_id}.jsonl"
        self._buffer: list[dict] = []
        self._flush_threshold = 10
        self._last_uuid: str | None = None
    
    def append(self, type: str, payload: dict) -> str:
        uuid = generate_uuid()
        entry = {
            "uuid": uuid,
            "parent_uuid": self._last_uuid,
            "type": type,
            "payload": payload,
            "ts": time.time(),
        }
        self._buffer.append(entry)
        self._last_uuid = uuid
        if len(self._buffer) >= self._flush_threshold:
            self.flush()
        return uuid
    
    def flush(self):
        if not self._buffer:
            return
        with open(self._file, "a") as f:
            for entry in self._buffer:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._buffer.clear()
```

### Step 2: StateSnapshotter — 定期快照

```python
class StateSnapshotter:
    """Periodic StateBoard snapshots with cleanup."""
    
    def __init__(self, snapshot_dir: Path):
        self._dir = snapshot_dir
        self._mutation_count = 0
        self._snapshot_interval = 20  # Every 20 mutations
    
    def on_mutation(self, board: StateBoard) -> str | None:
        self._mutation_count += 1
        if self._mutation_count % self._snapshot_interval != 0:
            return None
        return self._save_snapshot(board)
    
    def _save_snapshot(self, board: StateBoard) -> str:
        seq = self._mutation_count // self._snapshot_interval
        path = self._dir / f"stateboard-{seq}.json"
        with open(path, "w") as f:
            json.dump(board.snapshot(), f, ensure_ascii=False)
        return path.name
```

### Step 3: SessionResumer — 恢复入口

```python
class SessionResumer:
    """Resume a session from persisted state."""
    
    async def resume(self, session_id: str) -> ResumeResult:
        chain = self._build_conversation_chain(session_id)
        latest = self._find_latest_checkpoint(chain)
        events_after = self._get_events_after(latest.checkpoint_line)
        board = self._replay_events(latest.snapshot, events_after)
        status = self._detect_interruption(chain)
        return ResumeResult(board=board, status=status, chain=chain)
```

### Step 4: 接入 OrchestratorRunner

```python
class OrchestratorRunner:
    def __init__(self, config_path, *, persist_dir: str | None = None):
        ...
        self._persist_dir = Path(persist_dir) if persist_dir else None
        self._recorder: EventRecorder | None = None
        self._snapshotter: StateSnapshotter | None = None
    
    async def run(self, objective: str, *, resume_session_id: str | None = None):
        if resume_session_id:
            return await self._resume(resume_session_id)
        return await self._fresh_run(objective)
```

---

## 六、与 Claude Code 的差异决策

| | Claude Code | 戏台方案 | 理由 |
|---|---|---|---|
| 存储格式 | 纯 JSONL | JSONL + periodic snapshot | 戏台有 StateBoard 需要快速恢复，纯 JSONL 重放太慢 |
| 元数据窗口 | 64KB head/tail | 同 | 好的设计直接复用 |
| Subagent | sidechain JSONL | Resident JSONL | 戏台没有通用 subagent，只有 resident |
| 权限持久化 | 永不存储 | 同 | 每次 resume 重新验证 |
| Branching | parentUUID 链支持 | 先不做 | MVP 不需要 fork session |
| 外部存储 | SessionStore adapter | 暂不做 | MVP 先本地文件 |

---

## 七、工作量估计

| 步骤 | 文件 | 新增代码 | 工作量 |
|------|------|---------|--------|
| EventRecorder | `persistence/event_recorder.py` | ~120 行 | 小 |
| StateSnapshotter | `persistence/state_snapshotter.py` | ~80 行 | 小 |
| SessionResumer | `persistence/session_resumer.py` | ~150 行 | 中 |
| 接入 Runner | `runner.py` | ~50 行 | 小 |
| Director 感知 resume | `state_board.py` + prompt | ~30 行 | 小 |
| 测试 | `tests/test_persistence.py` | ~200 行 | 中 |
| **合计** | | **~630 行** | **2-3 天** |

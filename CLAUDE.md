# 戏台 (Xitai) — 项目指南

多 Agent 编排引擎。导演统筹全局，戏子各尽其能。

---

## 核心概念

**导演 (Director)** — 唯一持有全局视野的统筹 Agent。基于 DirectorPattern（继承 CoreCoderPattern），系统 prompt 教其调度而非编码。每轮通过 `show_state` 读取戏台快照，然后决定 spawn/replan/intervene/finalize。

**戏子 (Agent)** — 战术执行单元。复用 CoreCoderPattern，通过 agent.json 配置区分角色（coder/reviewer/tester）。差异仅在 system prompt、工具集、memory 配置。

**戏台 (StateBoard)** — 全局状态面板。导演决策的唯一信息来源。追踪任务、戏子、产出、预算、消息、事件。

**两种戏子模式**：
- 一次性：spawn_agent → run → return → destroy
- 常驻：ResidentAgent 常驻内存，通过 asyncio.Queue 接收消息，持久化 transcript

---

## 文件组织

task-orchestration/
- `agent.json` — Agent 配置声明。Director + coder + reviewer + tester 四角色。
- `src/openagents_orchestration/`
  - `runner.py` — OrchestratorRunner。加载配置、管理戏台、调度导演、spawn 戏子。
  - `state_board.py` — StateBoard + Budget + AgentState + ResidentState + ArtifactRecord。
  - `resident.py` — ResidentAgent。常驻戏子的消息循环和生命周期管理。
  - `patterns/director.py` — DirectorPattern。覆盖 compose_system_prompt，注入调度指令。
  - `patterns/corecoder.py` — CoreCoderPattern（从 SDK 复制）。
  - `models/task.py` — TaskGraph / TaskNode / TaskStatus。拓扑分层 + 环检测。
  - `models/delivery.py` — DeliveryReport / TaskResult。
  - `tools/` — Director 调度工具 + Agent 通信工具。
  - `tools/corecoder/` — 戏子文件工具（read/write/edit/bash/glob/grep）。
- `tests/` — 测试。目标：任何改动必须通过现有测试。

---

## 关键设计决策

### StateBoard 为什么是独立数据结构，不塞进 Director 的对话历史？

对话历史受 context window 限制，会被压缩/截断。StateBoard 不受限，是导演的"白板"——结构化、完整、可查询。

### 为什么 claimed ≠ verified？

戏子经常声称创建了文件但实际上失败（路径错误、内容为空）。两阶段验证让 Director 能区分"声称了"和"真的在"。

### 为什么常驻戏子用"拉模式"（check_messages）而非"推模式"（实时注入）？

CoreCoderPattern.execute() 是顺序循环，卡在 `await llm.generate()` 时无法接收外部消息。不改 Pattern 的前提下，拉模式是最小改动方案。

### 为什么全局 Budget 而非每个 Agent 独立？

导演需要知道整体消耗才能做全局决策（"还剩 2000 token，t3 和 t4 只能跑一个"）。

---

## 运行时流程

```
OrchestratorRunner.run(objective)
  → _initial_decompose(objective)  // Director 的 LLM 分解为 TaskGraph
  → StateBoard(objective) + add_tasks(TaskGraph)
  → 启动 Director
      Director ReAct 循环:
        → show_state → 决策
        → spawn_agent / spawn_resident / replan / finalize
        → 工具执行 → 更新 StateBoard
        → 下一轮
  → StateBoard.to_report() → DeliveryReport
```

---

## 开发指南

### 添加新工具

1. 在 `tools/` 下新建文件，继承 `ToolPlugin`
2. 实现 `name`, `description`, `execution_spec()`, `schema()`, `invoke()`
3. 如需访问 StateBoard，从 `context.deps.state_board` 获取
4. 如需访问 runner，从 `context.deps.runner` 获取
5. 更新 `tools/__init__.py`
6. 更新 `agent.json` Director tools 列表
7. 写测试

### 添加新 Agent 类型

1. 在 `agent.json` 新增 agent 配置块
2. 指定 pattern（CoreCoderPattern 或自定义）
3. 配置工具集和 system prompt
4. 更新 `_build_agents_info()` 中的描述

### 修改 StateBoard

- StateBoard 是**唯一可变状态源**。所有状态变更必须通过其方法。
- 新增字段 → 同步更新 `snapshot()` 和 `to_report()`
- 保持 snapshot 的 LLM 友好格式（结构化、信号预计算）

---

## 测试

```bash
uv run pytest tests/ -v              # 全部测试
uv run pytest tests/test_resident.py -v  # 常驻戏子
uv run pytest tests/test_integration.py -v # 集成测试
```

测试要求：
- 任何代码改动必须有对应测试
- 测试必须能通过才能提交
- Mock LLM 调用，不依赖真实 API
- 在demo文件夹里面进行测试

---

## 环境

- Python >= 3.11
- 依赖：`io-openagent-sdk`, `pydantic`
- 包管理：`uv`
- 配置：`.env` 文件（LLM_API_KEY / LLM_API_BASE / LLM_MODEL）

---

## 常见模式

### Director 看到任务失败后的决策链

```
show_state → 看到 task FAILED
  → 判断：
    - 超时/429 → retry（spawn_agent 同一任务）
    - 复杂度太高 → replan（拆成子任务）
    - 非关键 → skip
    - 需要人确认 → ask_human
```

### 常驻戏子的消息处理

```
ResidentAgent._loop:
  inbox.get() → 收到消息
    → _build_input(msg) → input_text
    → _run_resident_single() → CoreCoderPattern 执行
    → 保存 transcript
    → _send_reply() → 写入 StateBoard._pending_messages
```

### 戏子间通信

```
Agent A: send_message(to="B", content="...")
  → StateBoard._pending_messages.append(...)

Agent B: check_messages()
  → 读取 _pending_messages
  → 可选 clear
```

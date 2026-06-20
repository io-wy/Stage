# 戏台 (Xitai) — 项目级 AI Coding 约束（项目宪法）

> 本文件是戏台约束体系的第一层（项目宪法）。所有 AI 在本项目工作必须遵守。
> 范式借自 Coding-Vibe-Go，内容对齐戏台真实实现：Python / async / 多 Agent 编排。

戏台是多 Agent 编排引擎——**导演统筹全局，戏子各尽其能**。

---

## 1. 上下文体系

四层递进，按需加载：

| 层级            | 位置                                      | 内容                                                              | 加载时机       |
| --------------- | ----------------------------------------- | ----------------------------------------------------------------- | -------------- |
| L1 项目宪法     | `CLAUDE.md`（本文件）                     | 操作原则、编码约束、流程约束                                      | 每次会话       |
| L2 全局协议     | `~/.claude/CLAUDE.md`                     | io-wy 工作协议 v18.6                                               | 每次会话       |
| L3 领域知识     | `docs/`（docs-proj·docs-ref·docs-tmp）    | 架构决策、方法论参考、临时缓存                                    | 涉及对应模块时 |
| L4 工作流 Skill | `.claude/skills/*.md`                     | adversarial-review·change-impact-scan·pitfall-journal·pre-verify  | 任务级匹配     |

**加载规则**：遇到不确定的实现细节，按 L3→L4 查找并 Grep 代码，禁止凭训练记忆编造。

**两套 skill 必须区分**（极易混淆）：
- `.claude/skills/*.md` — 给 **AI** 的工作流方法论（上表 L4），借自 Coding-Vibe-Go
- `skills/<name>/SKILL.md` — 给**戏子运行时**的同一批方法论 playbook（内容拷自 `.claude/skills/`，见 `skills/README.md`），由 `read_skill` 工具取阅后照着做（read-and-follow，无执行层）

---

## 2. 核心概念

**导演 (Director)** — 唯一持有全局视野的统筹 Agent。基于 DirectorPattern（继承 CoreCoderPattern），系统 prompt 教其调度而非编码。每轮通过 `show_state` 读戏台快照，决定 spawn / replan / intervene / finalize。

**戏子 (Agent)** — 战术执行单元。复用 CoreCoderPattern，由 `agent.json` 配置区分角色。差异仅在 system prompt、工具集、memory。当前 **7 角色**：

| 角色           | Pattern            | 职责                                                        |
| -------------- | ------------------ | ----------------------------------------------------------- |
| `director`     | DirectorPattern    | 全局调度（spawn / replan / finalize / ask_human）           |
| `coder`        | CoreCoderPattern   | 写码（最全工具集 + apply_patch / semantic_edit / sub_agent）|
| `reviewer`     | CoreCoderPattern   | 代码审查                                                    |
| `researcher`   | CoreCoderPattern   | 调研（web_search / web_fetch）                              |
| `github_agent` | CoreCoderPattern   | PR / Issue / CI / Repo                                       |
| `monitor`      | CoreCoderPattern   | 健康诊断 / 预算预测 / 告警                                   |
| `team_leader`  | TeamLeaderPattern  | 团队层级调度 + recover_task / correct_task_status           |

**戏台 (StateBoard)** — 全局状态面板，导演决策的唯一信息来源。追踪任务、戏子、产出、预算、消息、事件。**唯一可变状态源**（见 X-02）。

**三种戏子模式**：
- **一次性**：`spawn_agent` → `run_agent` → 返回 → 销毁
- **常驻**：`ResidentAgent` 常驻内存，`asyncio.Queue` 收消息，持久化 transcript（`_spawn_resident_for_task`）
- **协作**：producer/checker 常驻对，靠 `TASK_*` 信号自驱 approve/fix 循环（`_run_collaborative`，开关 `collaborative_mode: auto|on|off`）

### 关键设计决策（保留「为什么」）

- **StateBoard 为何独立于对话历史**：对话历史受 context window 限制会被压缩/截断；StateBoard 不受限，是结构化、完整、可查询的「白板」。
- **claimed ≠ verified 为何分两阶段**：戏子常声称创建了文件但实际失败（路径错、内容空）。两阶段验证让导演区分「声称了」和「真的在」。
- **常驻戏子为何用拉模式 `check_messages`**：CoreCoderPattern.execute() 是顺序循环，卡在 `await llm.generate()` 时无法接收外部消息。不改 Pattern 的前提下，拉模式是最小改动。
- **为何全局 Budget**：导演需知整体消耗才能做全局决策（「还剩 2000 token，t3/t4 只能跑一个」）。

---

## 3. 系统架构与模块地图

代码根：`src/openagents_orchestration/`（**注意：不是早期文档写的 `task-orchestration/`**）

| 模块          | 内容                                                                                             |
| ------------- | ------------------------------------------------------------------------------------------------ |
| `core/`       | runner · **agent_loader（一文件一 agent 编译层）** · state_board · resident · collaboration(+executor +state_machine) · task_state_machine · decision_history · sub_state_board · resident_prompts |
| `enterprise/` | global_orchestrator · team · human_channel · security · metrics · monitor_agent · events · project |
| `mailbox/`    | memory + redis（消息总线抽象，取代裸 `_pending_messages`）                                        |
| `transport/`  | matrix（matrix-nio，跨进程 / 分布式通信）                                                         |
| `tools/`      | director · resident · monitor · github · mcp · corecoder（戏子文件工具）· read_skill             |
| `patterns/`   | corecoder · director · team_leader                                                                |
| `models/`     | task · message · delivery                                                                         |
| `eval/`       | swe_bench_lite · humaneval · custom + judge（自带评估框架）                                       |
| 其它          | context · hooks · intent_classifier · memory · prompts · observability · persistence · store · reporting |

**入口**：
- `run.py` — 企业编排器，完整 objective → DeliveryReport
- `phased_run.py` — 分阶段调试：`plan` / `run-agent` / `run-task`，单独验证每个 phase
- `scripts/run_corecoder.py` — 单跑 CoreCoder

**角色定义：一文件一 agent**（取代早期 monolithic `agent.json`）：
- `agents/_base.json` — 共享 base（pattern.impl · llm · memory · context_assembler · 基础工具集 · 默认 hooks）
- `agents/<role>.json` — 每角色一文件，声明相对 base 的 delta：
  - `extends`：继承哪个 base（深合并）
  - `prompts`：**有序引用列表**，指向 prompt 符号（如 `prompts.roles.coder:ROLE`），按序拼接成角色层 system prompt，叠加在 pattern 的 `_PRINCIPLES` 之上。**不内联正文**
  - `tools`：工具增量 `+tool` / `-tool`（base 之上增删），impl 由 `agent_loader.TOOL_REGISTRY` 解析（工具 id→impl 单一信源）
  - `hooks`：声明式 hook（按 `agent_loader.HOOK_REGISTRY` 名解析），取代写死注册
- `agent.json` — 瘦身为纯 runtime/events/logging 配置（不再含 `agents[]`）
- 编译链：`agent_loader.load_agent_specs("agents/")` → extends 合并 + 工具展开 + prompts/hooks 下沉 `pattern.config` → 标准 `AgentDefinition`（SDK 零感知）。`runner._load_agent_definitions` 接入；agents/ 缺失时回退 agent.json 内联 `agents[]`（旧布局兼容）
- **prompt 分层**：`prompts.roles.<role>:ROLE`（角色特化）+ `prompts.constraints:*`（可复用硬约束）+ pattern 的 `_PRINCIPLES`（CoreCoder 的 CORE 是底座 / Director 的 PRINCIPLES 是完整层，由 `_PRINCIPLES_IS_BASE` 区分，防双重注入）
- **动态角色**：`runner.register_agent_spec(dict)` / `sub_agent` 的 `agent_spec` 参数 → spawn 时现写 json 造临时角色（同编译层校验，工具限 TOOL_REGISTRY 白名单）

**运行时流程**：`OrchestratorRunner.run(objective)` → 意图分类 → 初始分解 `TaskGraph` → `StateBoard` → Director ReAct 循环（`show_state` → spawn / replan / finalize）→ `to_report()` → `DeliveryReport`

---

## 4. 编码约束（反例免疫格式）

每条三要素：WRONG + CORRECT + Why。让 AI 看见边界，而非揣摩期望。

### X-01：工具调用必须线程安全

```python
# WRONG: 工具 invoke 内跨线程裸碰 runner 的 asyncio 对象 / 共享状态
def invoke(self, params, ctx):
    ctx.deps.runner.state_board.update_task(...)

# CORRECT: 经 asyncio.to_thread 桥接（runner 已统一包装）
async def _thread_safe_invoke(self, params, ctx):
    return await asyncio.to_thread(_sync_runner)
```

Why: 见 `core/runner.py:2036` `_make_thread_safe_invoke`。戏子工具在 SDK 线程池执行，裸碰事件循环会 RuntimeError 或数据竞争。所有 invoke 已被自动包线程安全，新工具不要绕过这层。

### X-02：StateBoard 是唯一可变状态源

```python
# WRONG: 旁路直改字段
task.status = "completed"
board.artifacts[path].status = "verified"

# CORRECT: 只走 StateBoard 方法
board.update_task(task_id, status=TaskStatus.COMPLETED)
board.verify_artifact(path, exists=True)
```

Why: 旁路改状态绕过 `log_event` 与 `snapshot()` 同步，导演看到的快照与真实脱节，全局决策失准。状态变更入口：`add_task / update_task / claim_artifact / verify_artifact / register_resident / log_event`。

### X-03：claimed ≠ verified，禁信戏子自报

```python
# WRONG: 戏子说「已写 x.py」→ 直接判完成
if "created" in agent_output:
    board.update_task(tid, status=COMPLETED)

# CORRECT: 先 claim 再落地核验
board.claim_artifact(task_id, ["x.py"])              # 戏子声称
board.verify_artifact("x.py", exists=path.exists())  # 真相
```

Why: 见 `core/state_board.py:109`，status 取值 `claimed|verified|missing|conflict`。戏子常声称成功但实际失败（路径错、内容空）。

### X-04：协作协议集中在 collaboration.py

```python
# WRONG: 散落手写解析
if msg.startswith("TASK_APPROVED"):
    ...

# CORRECT: 唯一解析入口
from openagents_orchestration.core.collaboration import parse_collaboration_message
sig = parse_collaboration_message(content)   # → CollaborationMessage(signal, task_id, body)
```

Why: 真实标记仅 `TASK_REVIEW_READY` / `TASK_APPROVED` / `TASK_FIX_NEEDED`，正则与枚举定义在 `core/collaboration.py`。散落字面量 = 改协议名要全局猎、必漏。生成端 `core/resident_prompts.py` 也只在此处教戏子发信号。

### X-05：配置 / 密钥 / URL → env，禁硬编码

```python
# WRONG
api_base = "http://10.x.x.x:8000/v1"

# CORRECT: .env + agent.json 占位
# .env:        LLM_API_KEY / LLM_API_BASE / LLM_MODEL
# agent.json:  "api_base": "${LLM_API_BASE}"
```

Why: 密钥进代码 = 泄漏；硬编码 URL = 换环境就改码。

### X-06：async 资源必须配对释放

```python
# WRONG: 开了不关
rid = await runner.start_resident("coder")

# CORRECT: 配对 + try/finally
rid = await runner.start_resident("coder")
try:
    ...
finally:
    await runner.stop_resident(rid)
```

Why: 常驻戏子 / MCP 连接泄漏 = 内存与句柄累积，长跑 OOM。配对关系：`spawn_resident↔stop_resident`、`_ensure_mcp_connected↔_close_mcp`。

### X-07：异常不吞

```python
# WRONG
try:
    await agent.run(task)
except Exception:
    pass

# CORRECT: 记事件 + 让导演可恢复
except Exception as e:
    board.log_event("agent.error", task_id=tid, message=str(e))
    raise   # 或显式降级
```

Why: 戏子静默失败 = 导演看不到，任务永远卡 RUNNING（参 `core/resident.py` 的 TASK_REVIEW_READY fallback 设计）。

### X-08：依赖引入——标准库 > 已有依赖 > 新依赖

引入新依赖前自查：① stdlib 是否已有等价？② `pyproject.toml` 已有依赖能否覆盖？③ 必须引入则评估活跃度 / 许可 / CVE，并告知 io-wy。

### X-09：公共函数 / 新逻辑必须有测试，且 Mock LLM

```python
# WRONG: 测试打真实 LLM
result = await runner.run("build X")

# CORRECT: Mock llm.generate，断言行为
fake_llm.generate.return_value = canned_response
```

Why: 真实 API 慢 / 贵 / 不稳定 / 不可复现。测试在 `tests/` 或 demo 目录跑（见 §7）。

### X-10：单次写入 ≤ 400 行 / 12KB

超限分批（先 Write 前半，再 Edit 追加），避免单次输出爆限。io-wy 要求完整输出时可突破。

---

## 5. 流程阻塞约束

以下顺序不可跳过、不可并行：

```
功能实现 → 单元测试 → 自审 / 对抗审查 → 提交
```

- 功能未完成不得开始测试；测试未通过不得标记功能完成
- 审查未过不得 commit；AI 不可自主跳过任何阶段
- io-wy 明确要求跳过时，记录原因后执行

## 6. 改不全预防

每次修改代码必做（方法论见 `.claude/skills/change-impact-scan` 与 `pre-verify`）：

1. **调用点扫描**：改函数签名 / 接口 → Grep 所有调用点，列影响范围后再改
2. **正反配对**：新增 spawn / claim / connect / start → 确认有对应 stop / verify / close（呼应 X-06）
3. **配置同步**：① 新增工具 → 在 `agent_loader.TOOL_REGISTRY` 登记 id→impl（单一信源），再在对应 `agents/<role>.json` 的 `tools` 增量引用；② 改角色 prompt → 改 `prompts/roles/<role>.py` 或 `prompts/constraints.py`（json 只存引用，不存正文）；③ 新增 hook → 在 `agent_loader.HOOK_REGISTRY` 登记
4. **测试同步**：改实现逻辑 → 检查 `tests/` 对应用例是否需更新
5. **StateBoard 字段同步**：新增字段 → 同步 `snapshot()`（`state_board.py:1751`）与 `to_report()`（`:1888`），保持 snapshot 的 LLM 友好格式
6. **文档同步**：改公共接口 / 角色 / 模块 → 回头更新本文件对应小节，防止再次漂移

执行方式：单文件自主检查随 commit 输出；多文件先列影响范围；接口变更走 Plan 模式。

## 7. 测试约束

```bash
uv run pytest tests/ -q                       # 全量回归，提交前须全绿
uv run pytest tests/test_collaboration.py -v  # 协作模式
uv run pytest tests/test_resident.py -v       # 常驻戏子
ruff check src/ tests/                         # 静态检查（E,F,I,B,UP,SIM；line 88）
```

- 任何代码改动必须有对应测试，且全量回归通过才能提交
- Mock LLM 调用，不依赖真实 API（`asyncio_mode=auto`，`pythonpath=src`）
- 集成 / demo 验证在 `examples/` 下进行，不污染源码树
- redis 相关用例在未装 optional dep（`redis`）时自动 skip，属预期

## 8. 不确定性声明

查证链（来自 L2 全局协议）：Grep 项目 → 依赖文件 → Context7 → WebSearch，无法验证则标 `# TODO: verify` 并告知 io-wy。

以下情况必须明说「simimasen，io-wy，我还有东西不知道」：
- SDK（`io-openagent-sdk`）API 签名查证链走完仍无果
- 无法确认 Pattern / 工具在当前 SDK 版本的行为
- 没有实测数据支撑的性能 / 收敛断言

禁止：编造 SDK 方法签名、未读源码时声称「已分析」、跳过实测用模式匹配硬推。

## 9. 多模型对抗审查

对接 `.claude/skills/adversarial-review.md`。触发任一：核心逻辑变更 / 改动 ≥ 5 文件 / ≥ 200 行 / io-wy 要求 / 戏子产出质量可疑。

流程：主模型自审 → 跨模型独立审查 → 双方都报 = 高置信；单方报 = 标「需 io-wy 确认」→ 按 Critical / Major / Minor / Suggestion 输出合并报告。核心理由：同一模型写 + 审共享盲区，跨模型覆盖单模型看不见的部分。

## 10. 踩坑进化闭环（PIT）

对接 `.claude/skills/pitfall-journal.md`。触发：AI 生成错误代码被纠正 / 漏边界致 bug / 用了不存在的 API / 改不全 / 不确定时编造。

闭环：记 PIT 条目 → 同类 ≥ 2 次提炼为本文件新约束（X-NN）→ 约束验证有效 ≥ 2 次考虑固化为独立 skill。

## 11. 开发指南

### 添加新工具
1. `tools/` 下新建文件，继承 `ToolPlugin`
2. 实现 `name` / `description` / `execution_spec()` / `schema()` / `invoke()`
3. 访问戏台：`context.deps.state_board`；访问 runner：`context.deps.runner`
4. 在 `core/agent_loader.py` 的 `TOOL_REGISTRY` 登记 `id→impl`（单一信源），再在需要它的 `agents/<role>.json` 的 `tools` 里加 `+<id>`
5. 写测试（呼应 §6 配置同步）

### 添加新 Agent 类型
1. `agents/<role>.json` 新建一文件：`extends: "_base.json"` + 声明 delta
2. `prompts`：在 `prompts/roles/<role>.py` 写 `ROLE` 等积木，json 里按 `"prompts.roles.<role>:ROLE"` 引用（按需组合多片段）
3. `tools`：用 `+tool`/`-tool` 相对 base 增删；`pattern.config` 覆盖 `max_steps` 等；非 CoreCoder 角色指定 `pattern.impl`
4. 更新 `runner._build_agents_info()` 中的角色描述
5. 临时 / 一次性角色无需建文件：`runner.register_agent_spec(dict)` 或 `sub_agent(agent_spec=...)` 动态注册

### 修改 StateBoard
- 唯一可变状态源，所有变更走其方法（X-02）
- 新增字段 → 同步 `snapshot()` + `to_report()`（§6 第 5 条）

## 12. 常见模式

### Director 看到任务失败的决策链
```
show_state → task FAILED
  → 超时 / 429      → retry（spawn_agent 同一任务）
  → 复杂度过高      → replan（拆子任务）
  → 非关键          → skip
  → 需人确认        → ask_human
  → 状态不一致      → correct_task_status / recover_task（team_leader）
```

### 常驻戏子消息循环
```
ResidentAgent._loop:
  inbox.get() → _build_input(msg) → _run_resident_single()（CoreCoderPattern）
    → 保存 transcript → _send_reply()（经 mailbox 投递）
```

### 戏子间通信（mailbox 抽象）
```
A: send_message(to="B", content="...")   → mailbox 投递
B: check_messages()                       → 拉取（拉模式，见 §2 设计决策）
协作信号 TASK_* 由 collaboration.py 解析（X-04）
```

<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
<!-- SPECKIT END -->

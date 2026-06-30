# 戏台 (Xitai) — 设计理据与核心概念

> L3 领域知识。只装**工具拿不到 + 稳定**的两样东西：核心概念（心智模型）+ 设计决策的「为什么」。
>
> 会变的结构信息——模块地图、文件位置、函数签名、角色清单、运行时流程——**一律不在这里固化**（必过期）。
> 需要时用 `codegraph_explore` / `codegraph_files` / `grep` / `ls` 实时拿（见 `CLAUDE.md` §1 加载规则）。

戏台是多 Agent 编排引擎——**导演统筹全局，戏子各尽其能**。

---

## 核心概念（心智模型）

- **导演 (Director)** — 唯一持有全局视野的统筹 Agent，**只调度不编码**。它是「异常值班员」而非「轮询调度员」：能确定的事交给 hook 自动做，它处理需要判断的部分。
- **戏子 (Agent)** — 战术执行单元，共用一套 Pattern，靠角色配置（system prompt + 工具集 + memory）区分。角色清单与差异 `ls agents/` + 读对应 json 拿。
- **戏台 (StateBoard)** — 全局状态面板，**唯一可变状态源**，导演决策的唯一信息来源。追踪任务 / 戏子 / 产出 / 预算 / 消息 / 事件。

## 关键设计决策（保留「为什么」——代码只有 what，why 只在这）

- **StateBoard 为何独立于对话历史**：对话历史受 context window 限制会被压缩 / 截断；StateBoard 不受限，是结构化、完整、可查询的「白板」。
- **claimed ≠ verified 为何分两阶段**：戏子常声称创建了文件但实际失败（路径错、内容空）。两阶段让导演区分「声称了」和「真的在」。
- **常驻戏子为何用拉模式 `check_messages`**：Pattern 的 `execute()` 是顺序循环，卡在 `await llm.generate()` 时收不到外部消息。不改 Pattern 的前提下，拉模式是最小改动。
- **为何全局 Budget**：导演需知整体消耗才能做全局决策（「还剩 2000 token，t3/t4 只能跑一个」）。
- **max_steps 续命为何「信任 + 护栏」**：判定不打外部进度分——ReAct 是观察后行动、开发非线性（搭架子时分数趴着、重构时先删后写分数倒退），外部静态度量会误杀探索 / 重构期。改为信任 agent 自陈 + 二值验收（verify hook 红绿灯，不打分），系统只画两条护栏：资源边界（Budget + 续命轮数）+ 死循环（机械空转信号）。
- **确定性化方向**：能算的别问 LLM。verify / 续命挂在 `after_execute` 的确定性 hook 链上自动做，导演退居异常驱动——省 token、可复现。

## 分层与依赖方向（架构约束）

模块按职责分层，**依赖只能向下，禁止循环**：

- `projects/`（多项目扩展）→ 可依赖 `core/` · `models/` · `transport/`
- `core/`（单项目引擎）→ 可依赖 `models/` · `transport/`
- `tools/` → 可依赖 `core/` · `models/`
- 下层不依赖上层。

每个子包 `__init__.py` 统一导出该层公共 API；`tests/` 与 `src/` 保持平行结构。
（具体目录树 / 文件清单用 `codegraph_files` / `ls` 实时拿，不在此固化。）

## 编码铁律（Stage 特定，写代码必守；通用编码约束见 `CLAUDE.md`）

- **StateBoard 是唯一可变状态源**：状态变更只走其方法（`add_task` / `update_task` / `claim_artifact` / `verify_artifact` / `register_resident` / `log_event`），禁止旁路直改字段——旁路绕过 `log_event` + `snapshot()` 同步，导演快照与真实脱节、全局决策失准。
- **claimed ≠ verified，禁信戏子自报**：先 `claim_artifact`（戏子声称）再 `verify_artifact`（落地核验），区分「声称了」与「真的在」。戏子常报成功但实际失败（路径错 / 内容空）。
- **协作协议集中**：`TASK_REVIEW_READY` / `TASK_APPROVED` / `TASK_FIX_NEEDED` 只在 `core/collaboration.py`（`parse_collaboration_message`）解析，禁散落字面量；生成端 `core/resident_prompts.py` 也只在此教戏子发信号。
- **工具线程安全**：戏子工具 `invoke` 在 SDK 线程池跑，runner 已统一包 `_make_thread_safe_invoke`，新工具勿绕过（裸碰事件循环会 RuntimeError / 数据竞争）。
- **配置同步（改不全预防）**：① 新工具 → `agent_loader.TOOL_REGISTRY` 登记 id→impl + `agents/<role>.json` 的 `tools` 增量引用；② 改角色 prompt → `prompts/roles/<role>.py` 或 `prompts/constraints.py`（json 只存引用）；③ 新 hook → `agent_loader.HOOK_REGISTRY`；④ StateBoard 新字段 → 同步 `snapshot()` + `to_report()`。
- **测试 mock LLM**：测试在 `tests/`，mock `llm.generate` 不打真实 API（`asyncio_mode=auto` / `pythonpath=src`）；端到端 mock 在 `scripts/`。SDK 为 `io-openagent-sdk`，API 签名查证链走完无果须明说。

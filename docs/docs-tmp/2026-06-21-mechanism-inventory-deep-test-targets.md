# 戏台机制全景与深度测试靶子（Phase 2 · 机制级）

> 记录时间：2026-06-21
> 范围：上一轮（Phase 1 P0/P1）补的是**模块零覆盖**的洞，撞的多是数据结构契约与纯函数（security/mailbox/metrics/models…）。本轮换视角——把戏台当**运行中的机制网**看，按子系统穷举**所有机制**，定位会「**丢数据 / 卡死编排 / 静默死 / 降级失效**」的系统级靶子，为失败注入 + 并发时序测试做准备。
> 方法：每条机制均经 codegraph/Read 实读源码定位，给出 `file:line` 证据与置信度，**未实读的部分明确标注**，不凭训练记忆。

---

## 为什么换视角

Phase 1 暴露的 33 个缺口集中在「**静态契约**」层（枚举分类、HMAC 转义、计数器累加、dataclass 反序列化）。但戏台真正的命脉是**动态行为**：

- **失败降级链**——LLM/工具/agent 挂掉后能不能优雅退化，而不是连锁崩。
- **通信时序与可靠性**——消息会不会丢、乱序、卡死；拉模式 backlog、双通道投递、协作信号循环。
- **生命周期与恢复**——常驻戏子睡/醒/熔断/孤儿、预算切分、人在环路超时。

这些都不是「读一个函数返回值」能测的，要**构造失败场景 + 并发时序 + 跨组件**才能逼出来。下面先把机制铺全，再圈靶子。

图例：✅已测 · ⚠️浅测/部分 · ❌零测 · 🔴已确认缺陷 · 🟡可疑待验

---

## 机制全景（14 子系统）

### A. 调度与决策（导演大脑）
| 机制 | 核心代码 | 脆弱面 | 状态 |
|---|---|---|---|
| A1 意图分类 | `intent_classifier.py:200-211` | `_llm is None` 有降级，但 `structured_generate` 抛异常**无 try/except**，run 第一步即崩 | 🔴❌ |
| A2 初始分解 TaskGraph | runner 初始 plan | 空图 / 单任务 / 环 | ⚠️ |
| A3 Director ReAct 循环 | `runner._run_director_mode` | spawn/replan/finalize 决策分支 | ❌ |
| A4 决策记忆 DecisionHistory | `decision_history.py` 全文 | **只记录不去重**；防循环全靠 LLM 读 `recent_decisions` 自律，无代码兜底 | 🟡❌ |
| A5 决策信号 | snapshot 的 `strategy_signals`/`decision_feedback` | 信号算错 → 导演误判 | ❌ |

### B. 任务状态机
| 机制 | 核心代码 | 脆弱面 | 状态 |
|---|---|---|---|
| B1 合法转换表 | `task_state_machine.py:18-52` | 非法转换 `update_task` 内**静默 continue 不抛**（`state_board.py:454-466`）；同次调用其它字段照写 | ⚠️🟡 |
| B2 retry | `FAILED→PENDING` (`:31`) | 谁触发？retry 次数有无上限？ | ❌ |
| B3 claimed≠verified | `state_board.claim/verify_artifact` | 两阶段产物核验 | ⚠️ |

### C. 戏子生命周期
| 机制 | 核心代码 | 脆弱面 | 状态 |
|---|---|---|---|
| C1 一次性 | spawn→run→destroy | — | ⚠️ |
| C2 常驻 _loop | `resident.py:215` | sleep 子循环 / 熔断 / 自动 stop | ⚠️ |
| C2a sleep backlog | `resident.py:303-310` | 满 100 **静默丢最旧**，发送者无感知 | 🔴❌ |
| C2b wake drain | `resident.py:283-286` | 积压回 inbox **尾部** → 与 wake 后新消息乱序 | 🔴❌ |
| C3 协作 producer-checker | `collaboration_executor.py:90-148` | `_transition_to_review` **从不 wake checker**（与 `_transition_to_fix_needed` 不对称）→ 第二轮 review 永久卡死 | 🔴❌ |
| C4 task binding | `resident.py:131-136` | idle/熔断 auto-stop（`:237-246`/`:362-372`）**不 unbind** → 任务孤儿卡 RUNNING | 🔴❌ |

### D. 通信
| 机制 | 核心代码 | 脆弱面 | 状态 |
|---|---|---|---|
| D1 mailbox | `mailbox/memory.py` | ack 黑洞 / 无 visibility timeout / 双锁竞争 | ✅(P1 已测 6 gap) |
| D2 双通道 _send_reply | `resident.py:560-577` | 协作走 mailbox、普通直送 inbox；投递与 ack 间 `to` stop → 消息蒸发 | 🔴❌ |
| D3 协作信号 TASK_* | `collaboration.py` | 解析集中 | ✅ |
| D4 routing/channel_policy | `transport/` | 广播含发送者 / 通配符 | ✅(P1 已测) |
| D5 matrix 跨进程 | `matrix_transport.py` | 断连重连 / `_trim_rooms_cache`（实读浅） | ❌ |
| D6 trace 传播 | `state_board.propagate_trace` | 跨消息链路 | ⚠️ |

### E. 失败降级（fallback）
| 机制 | 核心代码 | 脆弱面 | 状态 |
|---|---|---|---|
| E1 collaborative→director | `runner.py:480-507` | 三条降级路径（协作崩 / 协作留尾 / 非协作）全未测 | ❌ |
| E2 intent LLM 失败 | =A1 | 不降级 | 🔴❌ |
| E3 structured_generate 重试 | `structured_generate.py:88-130` | generate 重试 × ValidationError 重试**嵌套放大** ~(n+1)²，429 风暴雪上加霜（推断） | 🟡❌ |
| E4 max_steps 产物兜底 | `corecoder` | 步数耗尽时的产物保全 | ❌ |
| E5 budget 耗尽 auto-finalize | `runner.py:509-535` | 耗尽即终、诚实汇报 | ❌ |
| E6 context summarize 降级 | `context.py:313-317` | LLM 失败回退 heuristic | ✅(**这个做对了，作正例**) |

### F. 监控与健康（两套并存，职责重叠）
| 机制 | 核心代码 | 脆弱面 | 状态 |
|---|---|---|---|
| F1 MonitorAgent 心跳 | `monitor_agent.py:145-158` | `_await_reply` 串行 `sleep(timeout)` → N 个 resident = N×timeout，**心跳间隔被拖垮追不上** | 🔴❌ |
| F2 HealthMonitor watchdog | `health_monitor.py:56-59` | `_check` **无 try/except**，一抛整个看门狗静默死（对比 F1 `:88-94` 有防护） | 🔴❌ |
| F3 on_agent_timeout | `monitor_agent.py:160-177` | 超时 → stop resident + 回调；resident 找不到时静默吞 | ❌ |
| F4 DLQ 监控 | `monitor_agent.check_dlq` | 死信巡检 | ⚠️ |

### G. 预算
| 机制 | 核心代码 | 脆弱面 | 状态 |
|---|---|---|---|
| G1 全局 Budget | `state_board.py:121-162` | token/time/steps 三维耗尽 | ⚠️ |
| G2 SubStateBoard 1/4 切分 | `sub_state_board.py:26-30,53-61` | 快照式切父预算 1/4；注释称「team completion 后合并」但**未见 merge 实现**，异常退出疑似泄漏；4 team×1/4=100% 父级无余量 | 🟡❌ |
| G3 全局 budget 不可设 | `_GlobalBudget` | 死代码（P1 H3 已记） | 🔴 |

### H. 人在环路
| 机制 | 核心代码 | 脆弱面 | 状态 |
|---|---|---|---|
| H1 ask_human/reply | `state_board.py:1418-1437`,`human_channel.py:118` | **无超时**；`HumanQuestion` 无 deadline 字段；codegraph 标 no covering tests | ❌ |
| H2 WAITING→FAILED「人没回」 | `task_state_machine.py:45` | 转换**定义了但未见驱动方** → 人不回则永久 WAITING（驱动方待确认） | 🟡❌ |

### I. 团队层级
| 机制 | 核心代码 | 状态 |
|---|---|---|
| I1 GlobalOrchestrator 多项目 | `global_orchestrator.py` | ⚠️(P1 已测 8) |
| I2 Team/TeamLeader | `team.py`,`patterns/team_leader.py`（实读浅） | ❌ |
| I3 SubStateBoard 事件冒泡 | `sub_state_board.py:35-51` | ❌ |
| I4 recover_task/correct_status | `tools/director/` | ⚠️(各 1 例) |

### J. 持久化与恢复
| 机制 | 核心代码 | 状态 |
|---|---|---|
| J1 resident transcript 持久化 | `resident.py:148-153` | ❌ |
| J2 event_replayer 重放恢复 | `persistence/event_replayer.py`（实读浅） | ❌ |
| J3 StateBoard from_dict/to_dict | `state_board` | ⚠️ |
| J4 session MAX_MESSAGES 截断 | `runner.py:139-148` | ❌ |

### K. 上下文组装
| 机制 | 核心代码 | 脆弱面 | 状态 |
|---|---|---|---|
| K1 4 层压缩 | `context.py:81-153` | snip(≥50%)→dedup(≥60%)→summarize(≥70%)→collapse(≥90%) 阈值链 | ❌ |
| K2 dedup exact-match | `context.py:228` | 指纹用规范化后**精确匹配**，差一字节即失效 → 去重几乎无效、过早触发 summarize | 🟡❌ |
| K3 hard_collapse 丢中间 | `context.py:321-339` | 只留头 2 + 尾 5，关键决策在中间 → 直接丢 | ❌ |

### L. 安全
| 机制 | 核心代码 | 状态 |
|---|---|---|
| L1 capability token HMAC | `projects/security.py` | ✅(P1 已测；H1 can 不验签 / H2 注入伪造) |
| L2/L3 channel_policy/routing | `transport/` | ✅(P1 已测) |

### M. Hook
| 机制 | 核心代码 | 脆弱面 | 状态 |
|---|---|---|---|
| M1 HookManager | `hooks/__init__.py:51-62` | `run` 同步，handler 抛异常**不隔离** → 炸整条工具链 | 🟡❌ |
| M2 tool.before/after_invoke | 同上 | 拦截 / 改参 / block | ❌ |
| M3 skill_loader | `hooks/skill_loader.py` | **半成品**，未接 HookManager，runner 直调（自述 `__init__.py:14-17`） | ⚠️ |
| M4 线程安全包装 | `runner.py:2036` | X-01 桥接 `_make_thread_safe_invoke` | ⚠️ |

### N. 可观测
| 机制 | 核心代码 | 状态 |
|---|---|---|
| N1 events 分类 | `projects/events.py` | ✅(P1 已测；global⊄project 不一致) |
| N2 metrics | `projects/metrics.py` | ✅(P1 已测；double-count) |
| N3 trace / N4 health | `state_board`/`observability` | ⚠️/❌ |

---

## 深度测试靶子（按危害排序）

筛选标准：**会丢数据 / 卡死编排 / 静默死 / 降级失效**——只有这类才配叫「机制测试」。

### P0 — 确认级缺陷（已实读源码定位，可直接写失败注入测试逼出）
| # | 机制 | 危害类型 | 一句话 |
|--:|---|---|---|
| D-1 | C3 协作 checker 永不重唤醒 | **卡死编排** | fix→re-review 第二轮 checker 醒不来，任务卡到预算耗尽 |
| D-2 | F1 心跳串行 await_reply | **机制失效** | N 个 resident 心跳串行 N×timeout，间隔追不上，形同虚设 |
| D-3 | F2 HealthMonitor `_check` 不 catch | **静默死** | watchdog 一抛异常整个后台任务死，看门狗自己先没了 |
| D-4 | C4 auto-stop 不 unbind | **任务孤儿** | idle/熔断停止戏子后绑定任务永久 RUNNING |
| D-5 | C2a/C2b sleep backlog 丢+乱序 | **丢数据/乱序** | 睡眠期满 100 静默丢、醒来积压排到新消息后面 |
| D-6 | A1/E2 intent LLM 失败不降级 | **降级失效** | 编排开局第一步意图分类崩，全盘未启动即死 |

### P1 — 高可疑（需构造场景验证，部分实读浅）
| # | 机制 | 危害类型 | 一句话 |
|--:|---|---|---|
| D-7 | E1 collaborative→director 三路降级 | 降级失效 | 协作崩/留尾时落 Director 兜底，三路全未测 |
| D-8 | H1/H2 ask_human 无超时 | 卡死编排 | 人不回 → 永久 WAITING，无代码超时兜底 |
| D-9 | G2 SubStateBoard 预算泄漏 | 资源失真 | team 异常退出，子预算疑似不回报父级 |
| D-10 | K2 dedup exact-match 失效 | 降级失效 | 工具输出差一字节即不去重，压缩链过早升级 |
| D-11 | M1 hook 异常不隔离 | 连锁崩 | 一个 handler 抛异常炸整条 tool 调用链 |
| D-12 | E3 structured_generate retry 嵌套放大 | 降级反噬 | 重试嵌套 ~(n+1)²，429 下放大风暴 |

详细 write-up（机制 / 证据 / 危害 / 建议测法）见下一节。

---

## P0 详细 write-up

### D-1 · 协作 checker 永不被重唤醒（卡死编排）🔴

**机制**：producer(coder) 写完发 `TASK_REVIEW_READY` → executor 把任务转 REVIEW 并让 producer 睡；checker(reviewer) 审完发 `TASK_FIX_NEEDED` → 让 checker 睡、唤醒 producer 去修。如此 fix→re-review 循环直到 `TASK_APPROVED`。

**证据**：`core/collaboration_executor.py` 两个转换**不对称**——
```python
async def _transition_to_review(self, decision, task, from_id, content):   # :90
    self._board.update_task(decision.task_id, status=TaskStatus.REVIEW)
    ...
    producer = self._residents.get(decision.producer_id)
    if producer is not None:
        await producer.sleep(reason="waiting for checker feedback")        # 只睡 producer
    return True                                                            # ← 缺 checker.wake()

async def _transition_to_fix_needed(self, ...):                            # :131
    ...
    await checker.sleep(...)                                               # 睡 checker
    await producer.wake()                                                  # 唤醒 producer
```

**危害**：第一轮 review 时 checker 刚 spawn 是醒的 → 正常。一旦走过一次 FIX_NEEDED（checker 被睡），producer 修完再发 `TASK_REVIEW_READY` → `_transition_to_review` 只睡 producer、**不唤醒已睡的 checker** → 第二轮 review 永不发生 → 任务卡在 REVIEW 直到 budget 耗尽。**协作模式的核心循环只能跑一轮**。

**建议测法**：mock producer/checker 两个 ResidentAgent（带 `_sleeping` 标志），依次驱动 REVIEW_READY→FIX_NEEDED→(producer 修)→REVIEW_READY，断言**第二次** `_transition_to_review` 之后 `checker._sleeping is True`（暴露 bug；修复后应为 False）。

---

### D-2 · 心跳 `_await_reply` 串行阻塞，间隔被拖垮（机制失效）🔴

**机制**：MonitorAgent 每 `heartbeat_interval_s`（默认 60s）给所有 resident 发心跳并等回复，连续 `max_missed` 次没回 → 判超时。

**证据**：`projects/monitor_agent.py`——
```python
async def _send_heartbeats(self):              # :97
    for resident in residents:                 # :102  串行
        ...
        replied = await self._await_reply(resident, timeout=self.heartbeat_timeout_s)  # :125

async def _await_reply(self, resident, timeout):   # :145
    before = resident.state.last_active
    await asyncio.sleep(timeout)               # :157  每个 resident 干睡 30s
    return resident.state.last_active > before
```

**危害**：`_await_reply` 不是真的等回复，而是**干睡 `timeout`（30s）** 再比对 `last_active`。N 个 resident 串行 → `_send_heartbeats` 耗 **N×30s**。3 个 resident = 90s，已超过 60s 心跳间隔；心跳循环永远追不上，且单个 resident 故障检测延迟随 N 线性放大。心跳机制名存实亡。

**建议测法**：注入 3 个 fake resident，monkeypatch `asyncio.sleep` 累加被请求的睡眠时长，跑一次 `_send_heartbeats`，断言总睡眠 ≈ `N×timeout`（串行）而非 ≈ `timeout`（本应并发 `gather`）。

---

### D-3 · HealthMonitor `_check` 不 catch，看门狗静默死（静默死）🔴

**机制**：HealthMonitor 后台 `_loop`：周期 sleep 后调 `_check()` 扫描 RUNNING agent，发现卡住/超步/超 token → `send_mail` 告警导演。

**证据**：`observability/health_monitor.py`——
```python
async def _loop(self):            # :56
    while True:
        await asyncio.sleep(self.check_interval)
        self._check()             # :59  无 try/except
```
对比 `monitor_agent.py:88-94` 的 `_heartbeat_loop` **有** try/except 兜底。两个 monitor 一个设防一个裸奔。

**危害**：`_check` 内 `send_mail` / `log_event` 任一抛异常 → 冒泡出 `_loop` → 该 asyncio task 终止。无人 `await` 这个后台 task 的异常，故**静默死**。此后所有卡住的 agent 再无人告警，导演彻底失去健康视野。

**建议测法**：monkeypatch `board.send_mail` 抛异常，构造一个 RUNNING 且超时的 agent，start watchdog 并推进一个 check 周期，断言后台 task 已 `done()` 且其 `.exception()` 非空（看门狗已死）。

---

### D-4 · 戏子 auto-stop 不 unbind，任务孤儿（任务孤儿）🔴

**机制**：常驻戏子 `bind_task` 把自己绑到任务（`board.bind_agent_to_task`），收尾应 `unbind_task`（`board.unbind_agent_from_task`）解绑。

**证据**：`core/resident.py`——`unbind_task`（:131-136）存在，但三条停止路径都**不调用**它：
- `stop()`（:162-176）：设 `_active=False` + status STOPPED，无 unbind
- idle/sleep timeout（:237-246 / :258-267）：同上
- 熔断（:362-372，连续错误 ≥ max）：同上

**危害**：绑定了任务的常驻戏子一旦 idle 超时或熔断自停，任务在 StateBoard 仍是 RUNNING 且 `bound` 到一个已死的 agent → **孤儿任务永久卡 RUNNING**。唯一兜底是 F1/F2 两个 monitor——而它们本身有 D-2/D-3 缺陷。

**建议测法**：bind 一个 task，把 `max_idle_s` 设极小且不发消息触发 idle auto-stop，断言 resident 状态 STOPPED 但 `board.get_task(tid).status == RUNNING` 且 agent 仍 bound（孤儿）。

---

### D-5 · sleep backlog 静默丢失 + wake 后乱序（丢数据/乱序）🔴

**机制**：睡眠期收到的非 wake/heartbeat 消息缓存进 `_sleep_backlog`，wake 时 drain 回 inbox 保序处理。

**证据**：`core/resident.py`——
```python
# wake drain（:283-286）：积压 put 回 inbox 尾部
for backlog_msg in self._sleep_backlog:
    await self._inbox.put(backlog_msg)

# backlog 满 100（:303-310）：pop(0) 丢最旧，只 log
if len(self._sleep_backlog) >= 100:
    dropped = self._sleep_backlog.pop(0)
    self._board.log_event("resident.backlog_dropped", ...)
```

**危害**：(a) 睡眠期积压 >100 条 → **静默丢最旧**，发送者收不到任何失败/丢弃信号（违反可靠投递）；(b) wake 瞬间若 inbox 已有「wake 之后才到」的新消息，backlog（旧）被 `put` 到这些新消息**之后**处理 → 因果倒置、时序错乱。

**建议测法**：(a) sleep 后灌 101 条消息，wake，断言 backlog 仅余 100 且发出 1 次 `backlog_dropped`；(b) sleep→灌入旧消息→在 drain 前向 inbox 直接塞一条新消息→wake，断言实际处理顺序「新先于旧」（暴露乱序）。

---

### D-6 · 意图分类对 LLM 失败不降级，开局即崩（降级失效）🔴

**机制**：`classify()` 用 LLM 做结构化意图分类；对「没有 LLM」有降级，但对「LLM 调用本身抛异常」无保护。

**证据**：`intent_classifier.py`——
```python
if self._llm is None:                                    # :200
    return IntentResult(..., source="fallback")          # 对「无 LLM」降级
...
schema, _ = await structured_generate(self._llm, ...)    # :211  对「LLM 抛异常」零保护
```
而 `reporting/summarizer.py:117` 的 `classify_failure` **能识别** `transient_upstream`（429/timeout/5xx/server disconnected）——系统明明知道有瞬时上游故障，却不在意图分类这一关降级。**设计内部矛盾**。

**危害**：`runner.run` 第一步就是 `_classify_intent`。LLM 一抖（429/超时）→ 异常上抛 → 整个 objective 在**最开始**崩溃，而非降级到 `unknown` 意图继续编排。

**建议测法**：`fake_llm.generate` 抛 429-like 异常，`await classify(objective)`，断言当前**抛异常**（暴露 bug）；期望行为应返回 `source="fallback"` 的 `unknown` 结果，让编排继续。

---

## P1 简要

| # | 机制 | 测点 |
|--:|---|---|
| D-7 | E1 collaborative→director | mock `_run_collaborative` 抛异常 / 留未完成任务 / 关闭协作，三路均应落 `_run_director_mode`；`runner.run` 当前 no covering tests |
| D-8 | H1/H2 ask_human 无超时 | 提一个 human question 不回复，推进编排，断言任务永久 WAITING_FOR_HUMAN（无超时 → FAILED 驱动方缺失，先确认是否真无驱动方） |
| D-9 | G2 SubStateBoard 预算 | team 异常 stop（非正常 completion），断言子 board 已烧 token 未回报父 budget（先确认 merge 实现位置） |
| D-10 | K2 dedup exact-match | 两条仅差 1 字节的相同工具输出，断言 dedup 不触发、`deduped_count==0`（去重失效） |
| D-11 | M1 hook 不隔离 | 注册一个抛异常的 `tool.before_invoke` handler，断言 `HookManager.run` 直接抛、污染整条工具链 |
| D-12 | E3 retry 嵌套放大 | fake_llm 持续抛异常，统计 `generate` 实际调用次数 ≈ `(max_retries+1)²`（嵌套放大，先实读确认嵌套层级） |

---

## 下一步

1. **先打 P0 六个**（D-1~D-6）——全是确认级、危害最大（卡死/静默死/丢数据/降级失效），最能体现从「数据结构契约」到「系统级失败注入 + 并发时序」的转变。预计落 `tests/test_collaboration_executor_resilience.py`、`tests/test_monitor_resilience.py`、`tests/test_resident_messaging.py`、`tests/test_intent_classifier.py` 等。
2. **P1 六个**需先各自补一次定向实读（D-8/D-9/D-12 标了「先确认」），验证假设再写，避免测偏。
3. 所有测试遵循 §X-09：**Mock LLM**，不打真实 API；断言**当前（有缺陷）行为**并标注期望行为，修复后转红。
4. 修复建议另起文档，不与缺陷暴露混写（暴露 → 确认 → 修复 三段分离）。

## 置信度声明（§8）

- **高置信（已实读源码定位）**：D-1（collaboration_executor 全文）、D-2（monitor_agent `_await_reply`）、D-3（health_monitor `_loop`）、D-4（resident 三条停止路径）、D-5（resident:283-310）、D-6（intent_classifier:200-211）。
- **中置信（实读但驱动方/合并路径未全 grep）**：D-8（WAITING→FAILED 驱动方）、D-9（子预算 merge 实现）、D-12（retry 嵌套层级）。这三个在写测试前会先定向确认。
- **实读浅**：D5 matrix 重连、J2 event 重放、I2 团队层级——列入全景但未深挖，需要时再侦察。


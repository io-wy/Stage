# Stage 测试暴露的缺口（Phase 1 P1 · enterprise / mailbox / models / transport）

> 记录时间：2026-06-20
> 范围：之前清理 54 个低价值测试后，enterprise / mailbox / transport / models 等基础设施模块变为**零覆盖**。本轮针对这些模块写**对抗性测试**（专撞 edge case / 契约违反 / 安全绕过，而非 happy-path），逐一暴露缺口。
> 全量回归：`uv run pytest tests/ -q` → **446 passed**（P1 前 361 → 新增 85）。
> 静态检查：`uv run ruff check tests/` → all checks passed。

所有 `test_gap_*` 测试当前**全绿**——它们 pin 的是**实际（有缺陷的）行为**，每条都已用工具实跑验证，不是凭读码推断。修复后这些测试会变红，提示行为已改。

---

## 本轮新增测试文件

| 文件 | 测试数 | 模块（行数，原测试已删） | 暴露缺口数 |
|------|-------:|--------------------------|-----------:|
| `tests/test_security.py` | 17 | `projects/security.py` (254) | 6 |
| `tests/test_mailbox_memory.py` | 13 | `mailbox/memory.py` (420) | 6 |
| `tests/test_global_orchestrator.py` | 8 | `projects/global_orchestrator.py` (385) | 5 |
| `tests/test_models.py` | 15 | `models/task.py` `message.py` `delivery.py` | 5 |
| `tests/test_channel_policy.py` | 9 | `transport/channel_policy.py` (113) | 3 |
| `tests/test_routing.py` | 9 | `transport/routing.py` (104) | 3 |
| `tests/test_metrics.py` | 8 | `projects/metrics.py` (360) | 3 |
| `tests/test_events.py` | 6 | `projects/events.py` (104) | 2 |
| **合计** | **85** | 8 模块 | **33** |

---

## 缺口总表（按严重度排序）

| # | 严重度 | 模块 | 缺口 | 复现测试 |
|--:|--------|------|------|----------|
| 1 | 🔴 High | security | 能力 token 的 actions/scope **从不被强制**：send 路径只 `verify()`、不 `can()` | `test_gap_can_authorizes_completely_forged_token` |
| 2 | 🔴 High | security | 签名 payload 不转义分隔符 → **分隔符注入伪造**（权限拆分提权） | `test_gap_delimiter_collision_enables_privilege_split` |
| 3 | 🔴 High | global_orchestrator | **全局预算无法设置** → 封顶分支 + `_GlobalBudget` 全为死代码（核心设计未落地） | `test_gap_global_budget_unsettable_and_cap_branch_unreachable` |
| 4 | 🔴 High | mailbox | **无可见性超时**：消费者崩溃后 in-flight 消息既不重投也不进 DLQ（违反 at-least-once） | `test_gap_in_flight_message_has_no_redelivery_timeout` |
| 5 | 🔴 High | mailbox | `ack(未知id)` **黑洞**后续同 id 消息；`_acked` 集合无界增长（内存泄漏） | `test_gap_pre_ack_of_unknown_id_blackholes_future_message` |
| 6 | 🔴 High | metrics | `from_state` 周期调用**重复累加** `llm_calls`（inc 累计值，应 set） | `test_gap_from_state_double_counts_llm_calls_on_repeated_snapshots` |
| 7 | 🟠 Med | security | `can()` 默认 `scope=""` **绕过 scope 检查** | `test_gap_default_scope_bypasses_scope_enforcement` |
| 8 | 🟠 Med | security | 文档承诺的「env 覆盖签名密钥」**零实现**；进程随机密钥 → 持久化 token 重启后 verify 失败 | `test_gap_no_env_override_for_signing_secret` |
| 9 | 🟠 Med | mailbox | `_sync_enqueue` **绕过熔断器**（与 async 路径不一致） | `test_gap_sync_enqueue_bypasses_circuit_breaker` |
| 10 | 🟠 Med | mailbox | sync / async 用**不同锁**守同一状态 → 混用竞态 | `test_gap_sync_and_async_paths_use_distinct_locks` |
| 11 | 🟠 Med | mailbox | `dequeue_specific` **投递过期消息**（违反 ABC「过期即丢」契约） | `test_gap_dequeue_specific_delivers_expired_message` |
| 12 | 🟠 Med | global_orchestrator | `run()` 失败交付也标 `COMPLETED`，且无 try/finally | `test_gap_run_marks_completed_even_on_failed_delivery` |
| 13 | 🟠 Med | global_orchestrator | 显式 `project_id` 碰撞**静默覆盖**前一个项目（并泄漏其 work_dir） | `test_gap_explicit_project_id_collision_clobbers_previous` |
| 14 | 🟠 Med | global_orchestrator | `team_specs` 创建 Team（有副作用）后**丢弃**，无 live 句柄 | `test_gap_teams_created_but_live_objects_discarded` |
| 15 | 🟠 Med | models | 重复 `task_id` 被误报为「循环依赖」+ `topological_layers` 静默丢任务 | `test_gap_duplicate_task_id_*` |
| 16 | 🟠 Med | models | 空 `DeliveryReport` → `all_succeeded=True`（空真值，掩盖零任务规划失败） | `test_gap_empty_delivery_report_claims_all_succeeded` |
| 17 | 🟠 Med | channel_policy | 默认策略 `to_dict()` 产出 **set → 非 JSON 可序列化**（持久化/审计导出崩溃） | `test_gap_to_dict_emits_nonjson_sets_for_default_policies` |
| 18 | 🟠 Med | channel_policy | 通配符**受连字符限制**：`coder*`（无 `-`）被当字面量，静默 deny-all | `test_gap_wildcard_requires_a_dash_to_match` |
| 19 | 🟠 Med | routing | 广播**含发送者自身**（自回显） | `test_gap_broadcast_includes_the_sender_itself` |
| 20 | 🟠 Med | metrics | typo/多余标签名**静默归入空标签序列**（指标错配） | `test_gap_unknown_label_names_silently_collapse_to_empty_series` |
| 21 | 🟠 Med | metrics | Prometheus 标签值**不转义** → 含 `"`/换行即输出畸形 | `test_gap_prometheus_label_values_not_escaped` |
| 22 | 🟡 Low | mailbox | `nack(未知id)` 制造**无 data 的幽灵 DLQ 条目**（无法 replay） | `test_gap_nack_unknown_id_creates_unreplayable_dlq_entry` |
| 23 | 🟡 Low | security | `from_dict` 对畸形 signature/timestamp 抛裸 `ValueError`/`KeyError` | `test_gap_from_dict_raises_valueerror_on_malformed_signature` |
| 24 | 🟡 Low | security | `AuditLog._purge_old` 每次 record 都 O(n) 重建 → O(n²)（仅文档，未写失败用例） | — |
| 25 | 🟡 Low | global_orchestrator | `_allocate_budget` 从不封顶 `time_limit_s` | `test_gap_allocate_budget_ignores_global_time_limit` |
| 26 | 🟡 Low | models | `*.from_dict` 对缺失必填键抛裸 `KeyError`（与 `.get` 风格不一致） | `test_gap_*_from_dict_keyerror_*` |
| 27 | 🟡 Low | models | `from_dict` 对非法枚举值抛 `ValueError` | `test_gap_message_from_dict_valueerror_on_invalid_enum` |
| 28 | 🟡 Low | models | `DeliveryReport` status 是自由字符串，typo（如 `complete`）静默算失败 | `test_gap_delivery_status_typo_silently_counts_as_not_completed` |
| 29 | 🟡 Low | channel_policy | 角色标记 `*_leader` 仅认下划线，`team-leader`（连字符）不匹配 | `test_gap_role_marker_requires_underscore_not_dash` |
| 30 | 🟡 Low | routing | pipeline 解析**保留空 stage**（`a >> b` → `["a","","b"]`） | `test_gap_pipeline_keeps_empty_stages` |
| 31 | 🟡 Low | routing | `type:` 单匹配返回 POINT_TO_POINT、多匹配返回 BROADCAST（拓扑随基数翻转） | `test_gap_type_multicast_single_match_is_point_to_point` |
| 32 | 🟡 Low | events | global 事件**并非** project-level 的子集（cross_project/dlq 全局但项目不可见） | `test_gap_some_global_events_are_not_project_level` |
| 33 | 🟡 Low | events | 两个 DLQ 事件分类矛盾（`message.dlq` vs `dlq.message_detected`，双真相源） | `test_gap_two_dlq_events_classified_inconsistently` |

汇总：🔴 High **6** · 🟠 Med **15** · 🟡 Low **12**。

---

## 🔴 High severity 详解

### H1 · 能力 token 的 actions/scope 从不被强制

**位置**：`projects/security.py:85` `CapabilityToken.can()` ＋ `core/state_board.py:1205-1215`（send 路径）

**问题**：
- `can()` 只检查 `is_expired` + 成员关系，**从不调用 `verify()`**——即一个签名完全伪造的 token（垃圾签名）也能 `can("任意动作")` 返回 True。
- 唯一的生产调用点 `state_board._send_structured`（L1208）只调 `agent._capability_token.verify()`，**从不调 `can()`**。

**影响**：能力 token 在发送鉴权里事实上**只验「签名有效且未过期」，从不验「这个 token 被授权做什么」**。`actions` / `scope` 字段是装饰性的——任何持有效 token 的 agent 能向任何对象发任何消息。authn（你是谁）与 authz（你能干啥）被解耦，且无人强制「先 verify 再 can」。

**建议修复**：
- send 路径在 `verify()` 通过后，再按消息类型/目标调用 `can(action, scope)`；或
- 在 `can()` 内部先 `verify()`（牺牲 authn/authz 分离，但消除「忘了 verify」这一类 footgun）。

---

### H2 · 签名 payload 不转义分隔符 → 分隔符注入伪造

**位置**：`projects/security.py:104-111` `CapabilityToken._sign()`

**问题**：HMAC payload 用 `|` 连字段、`,` 连 actions/scope，**均不转义**：
```python
payload = f"{issuer}|{bearer}|{','.join(actions)}|{','.join(scope)}|{expires}"
```
于是 `actions=["read,write"]`（单个含逗号的动作）与 `actions=["read","write"]`（两个动作）产出**完全相同**的签名字节串。

**影响**：拿到一个合法签名的 token，把一个动作**拆成两个**（或反向合并），签名仍 `verify()` 通过 → 获得原 token 从未授予的独立动作（提权）。本质是**签名未绑定 actions 列表的结构**，只绑定其逗号拼接串。当 issuer/bearer/action/scope 任一来自不可信输入时即可利用。

**建议修复**：签名前对各字段做规范编码（如 `json.dumps(sorted(...))` 或对分隔符转义/长度前缀），让结构进入签名覆盖范围。

---

### H3 · 全局预算无法设置 → 封顶逻辑是死代码

**位置**：`projects/global_orchestrator.py:70`（`__init__`）、`:341-360`（`_allocate_budget`）、`:34-43`（`_GlobalBudget`）

**问题**：
- `__init__` 把全局预算**硬编码**为无限：`self._global_budget = Budget(token_limit=-1, time_limit_s=-1)`，**无构造参数、无 setter**。
- `_allocate_budget` 因此永远走 `token_limit < 0` 的 pass-through 分支，「按全局余额封顶」的分支（`:352-360`）**永不可达**。
- 用于追踪 per-project 分配的 `_GlobalBudget` 子类**从未被实例化**；分配也从不回写全局已用量。

**影响**：CLAUDE.md §2 把「为何全局 Budget——导演需知整体消耗才能做全局决策」列为核心设计理由，但该能力**当前不存在**：100 个项目各请求 100k 各得 100k，全局池永不耗尽。

**建议修复**：`__init__` 增加 `global_budget: Budget | None` 参数；`_allocate_budget` 用 `_GlobalBudget` 记录并扣减分配；或若暂不实现，删除死代码与误导性 docstring，明确「全局预算未实现」。

---

### H4 · 无可见性超时：消费者崩溃后消息既不重投也不进 DLQ

**位置**：`mailbox/memory.py:212-234` `dequeue()`（标记 `_in_flight`）、缺少超时回收

**问题**：消息一旦 `dequeue` 即移入 `_in_flight`，**仅靠显式 ack/nack 才离开**。若消费者在 ack/nack 之间崩溃，消息永久滞留 `_in_flight`：后续 `dequeue` 不会重投，也不会进 DLQ。

**影响**：声称的「ack/nack re-delivery」在消费者崩溃场景失效——**at-least-once 投递被静默违反**。常驻戏子拉取消息后异常退出，该任务消息即丢失流通。

**建议修复**：为 in-flight 消息加入 visibility timeout（记录 dequeue 时间，超时未 ack/nack 则重新入队或计入 nack 次数），呼应 X-07「异常不吞、让导演可恢复」。

---

### H5 · `ack(未知id)` 黑洞后续消息 + `_acked` 无界增长

**位置**：`mailbox/memory.py:268-274` `ack()`、`:219` `dequeue` 的 `_acked` 过滤

**问题**：
- `ack(msg_id)` 只往 `self._acked`（set）里加，**从不清理**。
- `dequeue` 跳过任何 id 在 `_acked` 里的消息。
- 因此 `ack("尚未见过的id")` 会**永久黑洞**之后到达的同 id 消息：`enqueue` 返回 True、消息在 buffer 里，却永不投递。

**影响**：
1. 正确性：id 复用 / 猜测 / 重复，或 buggy 消费者预先 ack，导致消息静默丢失（enqueue 仍报成功）。
2. 内存：长跑常驻戏子每处理一条消息泄漏一个 set 条目（对比 `_idempotency_keys` 有 FIFO 上限，`_acked` 没有）。

**建议修复**：`_acked` 改为带 TTL/容量上限的结构（或处理完即删，仅在 in-flight 窗口内防重）；并区分「已 ack」与「从未入队」。

---

### H6 · `from_state` 周期调用重复累加 llm_calls

**位置**：`projects/metrics.py:347-360` `from_state()`

**问题**：`from_state` 文档明确「周期调用（每 30s）」，但对 LLM 指标用：
```python
self.llm_calls.inc(amount=float(llm_count))      # llm_count 已是累计值
self.llm_latency.observe(avg_latency, ...)
```
而 `agent.llm_call_count` 本就是**累计总数**。`inc` 会把整个累计值再加一遍——tasks/agents/budget 都正确用了幂等的 `.set()`，唯独 LLM 指标用累加路径。

**影响**：每个周期快照都把 LLM 调用数再翻一份，`orchestration_llm_calls_total` 无界膨胀，latency 直方图同样重复观测 → 预算/成本可观测性失真。

**建议修复**：LLM 调用数改用 `.set(llm_count)`（与其它 gauge 一致）；latency 若要直方图，应只在「新增调用」时 observe 增量，而非每次 observe 平均值。

---

## 🟠 Medium severity 详解

**M1 · `can()` 默认 scope 绕过**（`security.py:91-95`）：`can(action)` 不传 scope 时，`if not scope: return True`，跳过 scope 检查。scoped-to-`project-a` 的 token 若调用方忘传 scope，等于全局授权。→ 修复：要么 scope 必填，要么空 scope 视为「需匹配 token 的默认 scope」。

**M2 · 签名密钥无 env 覆盖 + 进程随机**（`security.py:58-60`）：docstring 称「Override in production via env var or config」，但**无任何读 env 的代码**；`_DEFAULT_SECRET = secrets.token_bytes(32)` 在 import 时随机定死。→ 后果：进程 A 签发的 token 在进程 B（持久化重载后）`verify()` 必失败（state_board:1208 会判 auth_failed 拒发）；运维无法 pin 共享密钥。→ 修复：从 env/config 读取并允许显式设置默认密钥。

**M3 · `_sync_enqueue` 绕过熔断器**（`mailbox/memory.py:365-386`）：async `enqueue` 查 `_check_circuit`，sync 包装器不查也不记录成败。熔断打开后 async 拒收、sync 照过 → 背压不一致。→ 修复：sync 路径补齐熔断检查与成败记录，或两路径共用同一逻辑。

**M4 · sync/async 不同锁**（`mailbox/memory.py:51-52`）：`_lock`(asyncio) 与 `_sync_lock`(threading) 守同一 `_buffer`/`_seq`/`_idempotency_keys`，互不排斥。X-01 指出戏子工具在 SDK 线程池经 `to_thread` 执行、StateBoard mail API 是同步——混用即竞态。→ 修复：同步入口也经事件循环串行化，或统一单锁模型。

**M5 · `dequeue_specific` 投递过期消息**（`mailbox/memory.py:194-210`）：不查 `is_expired`，而 `dequeue`/`peek` 都查。Mailbox ABC 注明「过期即丢」。→ 修复：补 `is_expired` 检查与 `dequeue` 对齐。

**M6 · `run()` 失败也标 COMPLETED**（`global_orchestrator.py:141`）：无条件 `project.status = COMPLETED`、记 `project.completed` 审计，且不在 try/finally 内（runner 抛异常则状态卡 RUNNING、审计不记，违反 X-07）。→ 修复：依 `report.all_succeeded`/异常设 COMPLETED/FAILED，并用 try/finally 包裹。

**M7 · 显式 project_id 碰撞覆盖**（`global_orchestrator.py:109-113`）：`self._projects[pid] = self._projects.pop(old_id)`，pid 已存在则静默覆盖前一个项目（其 auto work_dir 泄漏）。→ 修复：碰撞时报错或拒绝。

**M8 · Team 创建后丢弃**（`global_orchestrator.py:178-182`）：`team_specs` 经 `_create_team` 建出带 SubStateBoard 的 live Team（有副作用），却只把 `team.to_dict()` 存进 metadata，live 对象丢弃——无法调度/停止。代码注释自承「Project doesn't have teams dict yet」。→ 修复：Project 增加 `teams` 字典持有 live 对象，或暂不创建。

**M9 · 重复 task_id 误报为循环 + 静默丢任务**（`models/task.py:155-173`、`:185-204`）：`_has_cycle` 用 `visited != len(self.tasks)`，但 `in_degree`/`adj` 按 task_id 建字典；两个无依赖同名任务令 visited(1) ≠ len(2) → 误报「circular dependencies」（根本无边）。跳过 validate 时 `topological_layers` 又按 id 去重，静默丢一个任务。→ 修复：`validate` 显式检测重复 id 并给准确报错。

**M10 · 空 DeliveryReport 谎报 all_succeeded**（`models/delivery.py:60-62`）：`all(...)` 空列表为 True，同时 `success_rate=0.0`——零任务的 run 同时「全部成功」与「0% 成功」，空真值掩盖「规划产出零任务」的失败。→ 修复：`all_succeeded` 在 `task_results` 为空时返回 False（或独立 `has_results`）。

**M11 · 默认策略 `to_dict()` 产出非 JSON 的 set**（`channel_policy.py:90-95`）：注解 `dict[str,list[str]]`，但 DEFAULT 策略全用 set 字面量；`to_dict` 原样保留 set → `json.dumps` 抛 TypeError。持久化/审计导出默认策略即崩。→ 修复：`to_dict` 内 `{k: sorted(v) for ...}`，并把 DEFAULT 改用 list。

**M12 · 通配符受连字符限制**（`channel_policy.py:74-88`）：`_match` 仅当 pattern 以 `-*` 结尾或 `*-` 开头才走 fnmatch；`coder*`（无 `-`）落到精确比较，静默不匹配 `coder-1` → 静默 deny-all。→ 修复：统一用 fnmatch 处理任意 `*`，特例 `*_leader` 保留。

**M13 · 广播含发送者自身**（`routing.py:69-70`）：`route('*')` 返回全部已注册 agent，不排除 sender → 自回显，配合常驻拉模式可能自触发。→ 修复：广播时剔除 `msg.header.sender`。

**M14 · typo 标签静默归入空序列**（`metrics.py:62-65`）：`_key` 只读已声明 label_names，多余/拼错标签被丢、缺失标签变 `""`，观测静默落到空标签序列而非报错。→ 修复：对未知标签名告警或抛错。

**M15 · Prometheus 标签值不转义**（`metrics.py:299-304`）：`f'{k}="{v}"'` 不转义，值含 `"`/`\n`/`\` 即输出畸形，scraper 拒收。→ 修复：转义 `\`→`\\`、`"`→`\"`、`\n`→`\\n`。

---

## 🟡 Low severity（简述）

- **L1**（mailbox）`nack(未知id)` 三次后造无 `data` 的幽灵 DLQ 条目，`dlq_replay` 必返 False。修复：nack 前校验 id 在 in-flight。
- **L2**（security）`CapabilityToken.from_dict`/`AgentIdentity.from_dict` 对畸形 signature(`bytes.fromhex`)、created_at(`fromisoformat`) 抛裸异常。修复：try/except → 返回不可验 token 或 `SecurityError`。
- **L3**（security）`AuditLog._purge_old` 每次 `record` 都 O(n) 列表重建 → n 条记录 O(n²)。修复：改环形缓冲/惰性清理。
- **L4**（global_orchestrator）`_allocate_budget` 从不封顶 `time_limit_s`（即便全局预算可设也只封 token）。
- **L5**（models）`TaskNode.from_dict`/`TaskGraph.from_dict`/`StructuredMessage.from_dict` 对缺失必填键（task_id/description/agent_type/objective/created_at）抛裸 `KeyError`，与其它字段的 `.get` 默认风格不一致。
- **L6**（models）`from_dict` 对非法枚举值（type/priority/status）抛 `ValueError`，无降级。
- **L7**（models）`DeliveryReport.TaskResult.status` 自由字符串，typo（`complete`）静默算非成功。修复：用 StrEnum 约束。
- **L8**（channel_policy）角色标记 `*_leader` 仅认下划线后缀，`team-leader`（连字符）不匹配——依赖命名约定。
- **L9**（routing）pipeline 解析保留空 stage（`a >> b` → `["a","","b"]`），空目标会被尝试投递。
- **L10**（routing）`type:` 单匹配返回 POINT_TO_POINT、多匹配返回 BROADCAST，拓扑随基数翻转，下游按拓扑分派会见到不一致类型。
- **L11**（events）`is_global_level` 并非 `is_project_level` 的子集：`CROSS_PROJECT_MESSAGE`/`DLQ_MESSAGE_DETECTED` 全局但项目不可见——「全局事件从项目层冒泡」的假设不成立。
- **L12**（events）两个 DLQ 事件（`message.dlq` 项目级 vs `dlq.message_detected` 全局级）分类矛盾，DLQ 看板须订阅两者——「消息进 DLQ」的双真相源。

---

## 修复优先级建议

按「影响 × 修复成本」：

1. **先修 H6 + M14/M15（metrics）**——小改动，直接关系预算/成本可观测性，且 H6 是明确的累加 bug。
2. **H4 + H5（mailbox 投递可靠性）**——影响 at-least-once 与内存，是消息总线的正确性根基。
3. **H1（能力 token 不强制 actions/scope）**——安全设计落地；若短期不做完整 authz，至少在 send 路径补 `can()`。
4. **H3（全局预算死代码）**——要么实现，要么删死代码 + 修正 docstring/CLAUDE.md §2，避免「设计文档承诺、代码不存在」的漂移（呼应内存记录的 doc-drift 债）。
5. **H2 + M1/M2（security 其余）**——签名规范化与密钥管理，生产化前必做。
6. **M9/M10（models 契约）**——重复 id 报错、空报告语义，是规划层的正确性护栏。
7. Med 其余 + Low 批量处理（多为 `.get` 默认值 / 转义 / 枚举约束等机械修复）。

> 注：本轮所有 `test_gap_*` 锁定的是**当前行为**。修复任一缺口前，先把对应测试改为断言**期望行为**（红 → 绿），即可作为修复的回归守卫。


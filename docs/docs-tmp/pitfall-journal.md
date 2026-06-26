# Pitfall Journal — 戏台

> 踩坑台账（对接 `.claude/skills/pitfall-journal.md`）。每条记 type / severity /
> root-cause / status；同类 ≥2 次提炼为 CLAUDE.md 的 X-NN 约束。
> 本文件是持续累积的工程台账，非临时缓存。

---

### PIT-001: PatternOutcome repr 当 output 泄漏，污染 artifact 清单

- **Date**: 2026-06-22
- **Type**: protocol_drift
- **Severity**: Medium
- **Scenario**: 写「白名单外角色」回归测试时，trace 日志里发现 artifact 被 claim 成垃圾名
- **Phenomenon**: `artifact.claimed … ['hello.py'] ['usage=None'] ['error=None'] ['metadata={})']` —— 产物清单混入 PatternOutcome repr 的碎片
- **Root cause** (5 Why):
  1. artifact 清单出现 `'usage=None'` 等垃圾名
  2. `_extract_files_created`（hooks/state_sync.py）解析的 output 是 PatternOutcome 的 **repr** 而非干净文本
  3. 某条 run_agent 路径把 PatternOutcome 对象给了 `result.final_output`，`str()`（runner.py:677 `result_output = str(result.final_output)`）后成 repr，正则 `FILES_CREATED:\s*(.+)` 把整行 repr 逗号切出垃圾
  4. `final_output` 类型契约不清——有时 str，有时被多包一层 PatternOutcome
  5. 嵌套 run_agent（director spawn coder）的 outcome 包装/解包层次未统一
- **Fix**: 暂未修。**当前被 artifact 主链路剥离掩盖（休眠）**——hooks/state_sync.py 的 artifact collect/verify 段已整段注释。恢复 artifact 时必复现。届时：① `_extract_files_created` 入口校验 output 是 str；② 排查 `final_output` 为何是 PatternOutcome 对象。
- **Rule extraction**: 暂不（首次）。若再现「`str()` 了本应是 str 的对象当数据用」→ 提炼约束。
- **Status**: new（休眠）

---

### PIT-002: 双 PATTERN_AFTER_EXECUTE 触发点，corecoder.py:697 空转

- **Date**: 2026-06-22
- **Type**: incomplete_change
- **Severity**: Medium
- **Scenario**: phased_run 全链路 trace 跑通后，发现每个 agent 的 `after_execute` 打印两次
- **Phenomenon**: `[HOOK] after_execute agent=coder-t1 type=None task_id=None` 紧跟 `type=coder task_id=t1` —— 同一 agent 触发两次
- **Root cause** (5 Why):
  1. after_execute hook 每 agent 触发两次
  2. `corecoder.execute` 末尾（corecoder.py:697）和 `runner.run_agent` 末尾（runner.py:695）都 `run(PATTERN_AFTER_EXECUTE)`
  3. 重构把状态同步从 pattern 移到 runner 层时，没删 pattern 内的旧触发点
  4. 两触发点 payload 不一致（corecoder 那个缺 agent_type/task_id），靠 state_sync 开头 `if agent_type is None: return` 兜底空转，掩盖了重复
  5. 重构未做「触发点唯一性」审查
- **Fix**: 暂未修。建议删 corecoder.py:697 触发点（状态同步已统一由 runner.py:695 负责），或让其携带完整 payload 并明确分工。trace 工具（phased_run）可直接验证修复（修后每 agent 只应打一次）。
- **Rule extraction**: 暂不（首次）。若再现「重构迁移逻辑层但留旧触发点」→ 提炼「迁移 = 新增 + 删旧 + 触发点唯一性审查」。
- **Status**: new

---

### PIT-003: sub_agent 递归封顶 `_MAX_SUB_AGENT_DEPTH` 是死代码，无生效路径

- **Date**: 2026-06-25
- **Type**: incomplete_change / dead_code
- **Severity**: High（安全相关：coder 可无限递归 spawn coder，agent 树爆炸）
- **Scenario**: io-wy 要「coder spawn 的 agent 不能再递归」，查证当前 `_MAX_SUB_AGENT_DEPTH=2` 实现
- **Phenomenon**: 常量 + depth guard（`current_depth >= MAX → raise`）都在，但两条路径都不生效：
  - runner 模式（正常）：`sub_agent._spawn_via_runner` 调 `run_agent` 没传 depth；`run_agent` 无 `state` 参数；`_run_single` 函数体硬编码 `state: dict = {}` → 子 agent `__sub_agent_depth__` 永远读 0 → 封顶失效
  - standalone 模式：`_spawn_standalone` 调 `_run_single(state={...})`，但 `_run_single` 签名无 `state` 参数 → **TypeError，spawn 直接崩**
- **Root cause** (5 Why):
  1. depth 封顶形同虚设，coder 能无限递归（coder.json 带 `+sub_agent`，子 coder 仍是完整 coder）
  2. depth 在 runner 路径不透传：`run_agent`→`_run_single` 链路没有 state 通道
  3. `_run_single` 硬编码 `state={}`，每个 agent 启动 state 必空
  4. standalone 路径传了 `state=` 但函数不收 → 一调即崩，说明该路径从未被端到端测过
  5. depth 透传是「半成品」——`tests/test_spawn_dynamic_spec.py` 的 `fake_runner.run_agent(..., state=None)` 都预留了参数，真实实现却没补上
- **Fix**: ① `run_agent`/`_run_single` 加 `state` 参数并透传（修透传 + standalone TypeError）；② `MAX 2→1`；③ 叶子化（子 agent 去 `sub_agent` 工具，物理杜绝，双保险）。已修，`tests/test_sub_agent_recursion.py` 7 个 mock 测试固化。
- **Rule extraction**: 与 PIT-002 同属「重构留半成品/死代码」。**第 2 次出现** → 提炼候选约束：**「加保护机制（限流/封顶/鉴权/深度限制）必须有端到端生效路径的测试——常量存在 ≠ 机制生效；guard 写了不等于 guard 会被触达」**。再现 1 次即固化为 X-NN。
- **Status**: fixed

---

### PIT-004: 修坏测试机械补缺失变量，把 NameError 变成 AssertionError

- **Date**: 2026-06-25
- **Type**: process（修复方法论）
- **Severity**: Medium
- **Scenario**: 清 baseline 测试债，`test_tools_resident` 5 个测试报 `NameError: name 'tool' is not defined`
- **Phenomenon**: 机械给每个测试补 `tool = SendToResidentTool()` 让它能跑，结果露出更深的 `AssertionError`——`schema.required` 期望 `["resident_id"]`（实际 `["resident_id","task"]`）、`test_invoke_returns_resident_state` 期望工具返回 resident state（实际返回 sent 确认）。补变量只是把错误从「编译期」推到「断言期」
- **Root cause**:
  1. 补 `tool` 定义只把 NameError 变 AssertionError，没解决根本
  2. 这些测试的**断言基于 SendToResidentTool 旧契约**，与当前实现（要 resident_id+task、不读 board、返回 sent 确认）整体脱节
  3. 修坏测试时只盯「让它别报错」，没先核对断言是否符合当前实现契约
  4. io-wy 当场纠正：「`tool = SendToResidentTool()` 感觉没啥意义」
- **Fix**: 改为按当前契约逐个判断——删测旧契约/重复的（returns_resident_state / not_found / no_board / 与 missing_params 重复的），保留并修正 schema 断言为 `["resident_id","task"]`。
- **Rule extraction**: 首次。**修坏测试的正确顺序：先核对「断言 vs 当前实现契约」→ 判断删（契约已移除）/改（契约变更）/仅补变量（纯结构错且契约未变），而非机械补缺失符号让它通过编译。** 再现 1 次即提炼 X-NN。
- **Status**: fixed

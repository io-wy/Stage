# 多 Agent 机制重构：一文件一 Agent + 通用 Prompt 绑定 + 声明式 Hook

> 日期：2026-06-20 | 模式：推演 | 范围：接口级（≥10 文件，一次做全 Step 1-7）
> 状态：待 io-wy 批准

## 0. 目标（io-wy 的 vision）

把 monolithic 的 751 行 `agent.json` 拆成**一文件一 agent**（`agents/<role>.json`），每个角色文件自带：
- `extends` 共享 base（DRY，干掉 59% 工具重复 + llm/memory/pattern 各抄 7 遍）
- **`prompts` 引用列表**——指向（非内联）**一组** prompt 模块符号，按需组合。不同角色 / 不同场景各取所需，角色第一次有独立、可生长的 system prompt。**不是钉死一个 act_prompt 单槽**，而是「需要什么 prompt 就声明什么、怎么组合」
- `hooks` 声明式配置 → 干掉 runner.py 写死的 hook 注册
- spawn_agent 能**挑现成文件**或**现写一个 json** 造临时角色（动态角色，本轮含）

## 1. 可行性裁决（已全链路验证，零 SDK 修改）

三个 SDK 事实钉死方案边界（本轮 introspect 实测）：

| SDK 事实 | 出处 | 对方案的约束 |
|---|---|---|
| `AppConfig` / `AgentDefinition` 均 `extra: forbid` | `openagents.config.schema` 实测 | `prompts`/`hooks`/`extends` **进不了 agent 顶层** → 必须 Stage 编译下沉 |
| `pattern.config` 是 `dict[str, Any]` 自由字典，`_instantiate(symbol, ref.config)` 原样进 `__init__` | `openagents.plugins.loader._load_plugin_impl` | `prompts`/`hooks` **搭 pattern.config 顺风车**，CoreCoderPattern.__init__ 已在 `self.config.get(...)` 读十几个键，加同构 |
| `load_config(path)` 只收**单文件**、不支持 `$ref/include/extends` | `openagents.config.loader` 实测 | 多文件 + extends 组装**必须 Stage 自建 loader** |

**结论**：不碰 SDK 一行。新增 Stage 编译层，把 `agents/*.json`（含 Stage 扩展字段）编译成 SDK 认的扁平 `list[AgentDefinition]`，喂给现有 `_agents_by_id`。

## 2. 当前 prompt 机制（重构起点）

Prompt 绑在 **Pattern 类的 `_PRINCIPLES` 类属性**，不是绑角色：

| Pattern | `_PRINCIPLES` | 服务角色 |
|---|---|---|
| `CoreCoderPattern` | `CORE_PRINCIPLES` | coder/reviewer/researcher/github_agent/monitor **5 个共享同一份！** |
| `DirectorPattern` | `_select_director_principles()` | director |
| `TeamLeaderPattern` | `DIRECTOR_PRINCIPLES + 团队规则` | team_leader |

- `corecoder.py:306/1309/1629` 调 `compose_system_prompt("")` —— **base_prompt 硬编码空串**
- 角色差异现在全靠 spawn 时塞进 **user message** 的 `CODER_CONSTRAINT`/`REVIEWER_CONSTRAINT`（`spawn_agent._build_input:512-515`），不在 system prompt
- 5 个 CoreCoder 角色 system prompt **完全相同** → 这就是「角色薄」的根因

**通用 prompt 机制如何解决**：`compose_system_prompt` 的空串 → 读 `config["prompts"]` 引用列表，按序解析+拼接成角色 prompt。
最终 system prompt = **[prompts 列表解析拼接（角色层，灵活组合）] + _PRINCIPLES（pattern 层共享）+ 动态片段**。
一个 CoreCoderPattern 继续服务全部 5 角色，无需为每角色建 pattern 子类。

## 3. 迁移波及面（已确认天然收口）

所有 spawn 路径（one-shot / resident / collaborative / sub_agent / team）最终都汇到
`runner._agents_by_id` + `_ensure_bundle(agent_type)` → `load_agent_plugins(AgentDefinition)`。

**单一收口**：只改 `runner.py:186-187`（`load_config`→新 loader），所有路径自动受益。
下游拿到的仍是标准 `AgentDefinition`（prompts/hooks/extends 已在编译期消化），**零感知**。

`sub_agent.py:168` 的 `_spawn_standalone` 自建 `OrchestratorRunner(path)` 也走 runner 构造 → 同样自动收编，双读取口免费消除。

## 4. 目标形态

```
agents/
  _base.json              # 共享默认: pattern.impl, llm, memory, context_assembler, 基础工具集
  director.json           # extends _base, prompts → [director principles], 工具增量
  coder.json
  reviewer.json
  researcher.json
  github_agent.json
  monitor.json
  team_leader.json
agent.json                # 瘦身: 只剩 version/runtime/events/logging + MCP servers (不再有 agents[])

src/prompts/
  roles/                  # 角色 prompt 片段(可组合的积木), 一角色可引多片段
    coder.py    → ROLE
    reviewer.py → ROLE
    ...
  constraints.py          # 现 agent_constraints.py: CODER / REVIEWER ... (可被任意角色引用)
```

单个角色文件示例（prompts 是**有序引用列表**，按需组合）：
```jsonc
// agents/coder.json
{
  "id": "coder",
  "name": "Coder",
  "extends": "_base.json",
  "prompts": [                                  // ← 一组引用, 按序拼接, 不内联
    "prompts.roles.coder:ROLE",                 //   角色定位
    "prompts.constraints:CODER"                 //   角色约束 (复用现 CODER_CONSTRAINT)
  ],
  "tools": ["+apply_patch", "+sub_agent", "+semantic_edit"],  // base 之上增量
  "hooks": { "session.start": ["load_skills_into_context"] },
  "pattern": { "config": { "max_steps": 30 } }  // 覆盖 base
}

// agents/researcher.json —— 不同角色用不同 prompt 组合
{
  "prompts": ["prompts.roles.researcher:ROLE", "prompts.roles.researcher:WEB_GUIDE"],
  ...
}
```

编译后 `prompts` + `hooks` 折进 `pattern.config`，tools 展开成全量 ToolRef，产出标准 `AgentDefinition`。

## 5. 实施步骤（一次做全）

### Step 1 — Stage AgentSpec 编译层（新模块 `core/agent_loader.py`）
- `load_agent_specs(agents_dir, base_runtime) -> list[AgentDefinition]`
- 读 `agents/*.json`（`_base.json` 作 base）
- 解析 `extends`：深合并 base + 角色 override；工具增量语法 `+tool` / `-tool`
- 把 `prompts`（列表）/`hooks` 注入 `pattern.config`
- tools 简写展开成 SDK `ToolRef`（`{id, impl}`），impl 查 `TOOL_REGISTRY`（id→impl，从现 agent.json 提取，单一信源）
- 产出 `AgentDefinition`，pydantic 校验通过
- **容错**：单角色文件坏 → 报错带文件名，不影响其余（呼应 skills_registry 容错）

### Step 2 — 通用 Prompt 引用机制 + prompt 接线
- prompt 积木落 `src/prompts/roles/<role>.py`（角色片段）+ `src/prompts/constraints.py`（共享约束，现 agent_constraints.py 平移）
- **真正分化**（io-wy 定）：coder/reviewer/researcher/github_agent/monitor 各写**专属**角色 prompt，按场景需要引不同积木（coder 引 [role+文件约束]，researcher 引 [role+web 指引]，monitor 引 [role+诊断 playbook]…）。不是统一模板套壳
- `corecoder.py` 改造：
  - `__init__` 读 `self._prompt_refs = self.config.get("prompts", [])`
  - 加 `_resolve_prompts() -> str`：遍历引用列表，每项 `"module:SYMBOL"` 点路径 import（参照 SDK `_import_symbol` 惯例），按序 `\n\n` 拼接，带缓存
  - `compose_system_prompt("")` 三处 → `compose_system_prompt(self._resolve_prompts())`
- **向后兼容**：`prompts` 为空 → 回退现有 `_PRINCIPLES`（director/team_leader 这轮可保留类属性，或一并迁 prompts——见 Step 2b）

### Step 2b — director / team_leader 迁移（io-wy 定：也迁 act_prompt/prompts）
- director.json `prompts` → `[prompts.roles.director:PRINCIPLES]`（现 DIRECTOR_PRINCIPLES 平移，保留 compact 变体逻辑：用环境变量选不同引用）
- team_leader.json `prompts` → `[prompts.roles.director:PRINCIPLES, prompts.roles.team_leader:RULES]`（组合复用，正好示范「积木拼装」）
- DirectorPattern/TeamLeaderPattern 的 `_PRINCIPLES` 类属性退化为「prompts 为空时的兜底」，prompt 真理迁到 json + prompts 积木

### Step 3 — 声明式 Hook 注册
- 现状 `runner.py:221` 写死 `register("session.start", load_skills_into_context)`
- 编译层读每角色 `hooks` → bundle 建立时按声明注册到该 agent 的 hook 管线
- 建 `HOOK_REGISTRY`（name→callable），`"load_skills_into_context"` 按名解析
- **向后兼容**：未声明 hooks 的角色保留全局默认（director 仍自动拿 skill catalog）

### Step 4 — runner 接入
- `runner.py:186-187`：
  - `self._config = load_config(path)`（读瘦身 agent.json 拿 runtime/events/MCP）
  - `self._agents_by_id = {d.id: d for d in load_agent_specs("agents/", self._config.runtime)}`
- `_load_mcp_servers` 不变

### Step 5 — spawn 现写 json（动态角色，本轮含）
- `spawn_agent` / `sub_agent` schema 加可选 `agent_spec`（inline dict）
- 走同一编译层 `compile_one_spec(dict) -> AgentDefinition` → 注册进 `_agents_by_id`（session 级临时）
- 不传 spec 时按 agent_type 查现成文件（现行为）
- 导演 system prompt 补充：何时挑现成角色 vs 现写临时角色

### Step 6 — 测试同步（§7）
- `tests/test_agent_loader.py`：extends 合并 / 工具增量 / prompts+hooks 下沉 / 坏文件容错 / 产物 pydantic 合法 / **编译产物 == 旧 agent.json 等价快照**（迁移保险）
- `tests/test_prompt_refs.py`：引用列表解析 / 多片段按序拼接 / 缓存 / 空回退 _PRINCIPLES / 三处 compose 接线
- `tests/test_spawn_dynamic_spec.py`：inline spec 编译 + 临时注册
- 改任何断言 agent.json.agents 结构的现有用例
- 全量回归绿（现 434 passed 基线）

### Step 7 — 文档同步（§6-6）
- CLAUDE.md §2 角色表、§3 模块地图（加 `core/agent_loader`）、§11「添加新 Agent 类型」→「建 `agents/<role>.json`」、prompt 机制说明

## 6. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 编译层 bug 致角色配置漂移 | 产物先 pydantic 校验；「编译结果 == 旧 agent.json 等价」快照测试做迁移保险 |
| prompt 引用解析失败致 agent 起不来 | 解析失败 → 跳过该片段 + log warning，全空则回退 _PRINCIPLES（呼应 X-07 不吞但降级） |
| 真正分化引入行为变化 | 角色 prompt 首版 = 现 CORE 行为基线 + 角色专属增量，行为变化可观测；快照测试锁住编译等价，prompt 内容变化单独 review |
| 动态 spawn spec 注入风险 | inline spec 同样过编译层 + pydantic 校验，impl 只能引 TOOL_REGISTRY 白名单内工具，不接受任意 import 路径 |

## 7. 落地顺序（一次做全，内部仍分批验证）

1. Step 1+2+2b（编译层 + prompt 机制）→ 跑 test_agent_loader / test_prompt_refs
2. Step 3+4（hook + runner 接入）→ 跑全量回归，确认行为等价
3. Step 5（动态 spawn）→ 跑 test_spawn_dynamic_spec
4. Step 6 全量绿 + Step 7 文档

接口变更走本 Plan 审批；落地按 §5 流程：实现→测试→自审/对抗审查→提交。

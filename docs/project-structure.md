# Stage 项目结构

Stage 当前按“服务治理平台”组织代码，不再按泛用多 Agent 编排器组织。

## 产品主路径

```text
configs/governance/domain_packs/
  sast_service_desk.yaml

configs/governance/adapter_packs/
  service_adapters.yaml

src/openagents_orchestration/control/
  pipeline.py       治理执行主链路
  models.py         case / evidence / audit / action plan / action result 结构
  domain.py         治理包加载和业务域匹配
  router.py         RAG / ClaudeCode / human / subagent 路由规划和 action 约束
  permissions.py    权限和审批字段门禁
  evidence.py       evidence 公开摘要、相关性和敏感性处理
  safety.py         输出安全扫描和脱敏约束
  closure.py        是否允许 closed=true
  traceability.py   source-to-claim trace
  audit.py          audit.jsonl 事件记录
  feedback.py       线上反馈转治理 patch / regression case

src/openagents_orchestration/handler/
  http/             产品 API 入口、HTTP DTO、静态控制台资源

src/openagents_orchestration/service/
  console.py        handler/http 使用的服务门面，只聚合公开用例
  cases.py          demo case / live governance case 执行
  rag.py            RAG 查询服务和受治理 RAG backend
  runs.py           run history / detail / audit 查询
  feedback.py       反馈回流服务
  common.py         路径、JSON、digest 等服务层通用 helper
  settings.py       服务层默认路径和本地配置

src/openagents_orchestration/pkg/
  ...               可复用的纯工具/公共模块，优先放无副作用逻辑
```

产品请求进入 `handler/http`，再进入 `service/console.py`，最后进入
`service/cases.py` 或其他具体 service 模块；治理请求最终进入
`control/pipeline.py`。业务差异优先写到
`configs/governance/domain_packs/*.yaml`，不要写死在 Python 代码里。

真实执行不另起一套平行 execution layer。Stage 复用 `control/router.py`
里的 `GovernancePlan` 和 `control/models.py` 里的 `ActionPlan` /
`ActionResult`，把 ClaudeCode、subagent、RAG、human、adapter 都当作受治理的
backend。所有 backend 返回的执行结果必须进入 postcheck、verify、closure 和 audit。

`adapter_packs/` 只声明受治理执行手的边界，例如 ClaudeCode + lark-cli、
ClaudeCode + gh skill、subagent triage。Stage Core 不直接重写外部系统 API，
也不允许 adapter 自行决定闭环；adapter 只能在 `ActionPlan` 允许的范围内执行，
并把真实结果写回 `ActionResult`。

## RAG 能力

```text
src/openagents_orchestration/rag/
  extract.py        从 wiki / 文档抽取文本
  chunking.py       切块
  embedding.py      Ollama / mock embedding
  vectorstore.py    本地向量存储
  retriever.py      检索和 rerank
  answer.py         基于证据生成回答
  permissions.py    RAG 结果权限过滤
  badcase.py        badcase / regression 数据
  eval.py           RAG eval

scripts/rag_cli.py
scripts/rag_wiki_eval.py
```

RAG 不是和治理链路并列的产品，它是治理 pipeline 的一个可选 backend。单独 RAG 页面和脚本只用于调试知识库质量。

## Runtime 兼容层

```text
src/openagents_orchestration/runtime/
  runner.py
  state_board.py
  global_orchestrator.py
  team.py
  project.py
  human_channel.py
  security.py

src/openagents_orchestration/transport/
  channel_policy.py
```

`runtime/` 是旧编排运行时的兼容层，保留给历史脚本、Matrix adapter 和旧工具使用。它不是 Stage 治理平台的主表达，不再新增治理业务逻辑。

`transport/channel_policy.py` 仍被 `runtime/global_orchestrator.py` 和 `runtime/team.py` 使用，因此暂时保留；后续如果 runtime 下线，再一起删除。

## Eval 和 Demo

```text
eval/case_handling/
  stage_governance_runner.py
  stage_governance_grading.py
  governance_report.py
  stage_governance_demo.py

tests/governance_fixtures.py
```

eval 是 benchmark/dev 验证层，用来证明 Stage 在误闭环、越权、泄密、缺证据、审计完整性上优于 ClaudeCode + skill baseline。它不能出现在产品前端。

测试使用 `tests/governance_fixtures.py` 临时生成 replay baseline，不依赖 `.claude/skills/` 或 `skills/*` 工作区。

## 不进提交的内容

这些属于临时产物或外部 baseline，不作为项目代码提交：

- `skills/case-handling-baseline*`
- `.playwright-mcp/`
- `docs/reports/`
- `stage-console-*.png`
- `newapi-reference.png`
- `__pycache__/`

`.claude/skills/` 是项目级协作 skill，可作为项目配置提交；不要和本地运行产物混在一起。

## 提交边界

推荐拆分：

1. `control + handler + service`：治理主链路、HTTP 入口和应用编排。
2. `service`：HTTP 控制台、RAG 查询、反馈回流和其他应用编排。
3. `rag`：真实检索、embedding、RAG CLI/eval 和相关测试。
4. `runtime cleanup`：`core/`、`projects/` 迁到 `runtime/`，删除无用 state machine / metrics / sub board。
5. `eval replay`：Stage governance runner、comparison reporter、临时 fixture 测试。

不要把 skill 配置、截图、报告产物和代码提交混在一起。

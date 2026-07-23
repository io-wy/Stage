# 戏台 (Stage) - AI 服务治理平台

Stage 不是泛用多 Agent 编排器。它做的是可配置的服务治理：

- 意图识别
- 业务域映射
- 权限和审批门禁
- RAG / ClaudeCode / 人工通道 / subagent 路由
- 受治理 adapter 选择，例如 ClaudeCode + lark-cli、ClaudeCode + gh skill
- 证据、脱敏、闭环、审计、追踪
- 线上反馈回流和 replay 验证

## 现在这套平台解决什么

1. 线上输入进入 Stage 后，先被识别成业务请求，而不是直接当作模型提示词。
2. Stage 依据治理包决定要不要检索、要不要人工、要不要调用 ClaudeCode。
3. Stage 生成 `ActionPlan`，约束 adapter 能用哪些工具、能做什么、不能做什么。
4. 每次输出都要过权限、证据、安全和闭环门禁。
5. 线上反馈会生成治理 patch 和回归 case。
6. replay 通过后，治理包才可以升级发布。

## 主要入口

- 本地 Web 控制台：`http://127.0.0.1:8765`
- 治理平台说明：[`docs/stage-governance-platform.md`](docs/stage-governance-platform.md)
- Adapter 边界说明：[`docs/governance-adapter-boundaries.md`](docs/governance-adapter-boundaries.md)
- 反馈回流说明：[`docs/governance-feedback.md`](docs/governance-feedback.md)
- 业务治理包：[`configs/governance/domain_packs/sast_service_desk.yaml`](configs/governance/domain_packs/sast_service_desk.yaml)
- Adapter pack：[`configs/governance/adapter_packs/service_adapters.yaml`](configs/governance/adapter_packs/service_adapters.yaml)

## 源码结构

```text
configs/governance/domain_packs/
  *.yaml          可迁移治理包：业务流程、风险、权限、证据、闭环、路由规则

configs/governance/adapter_packs/
  *.yaml          受治理执行手：ClaudeCode + lark-cli / gh skill / subagent 等边界

src/openagents_orchestration/
  control/        产品主线：治理执行 pipeline、权限、证据、安全、闭环、审计、反馈
  service/        应用用例层：case 执行、RAG 查询、历史、审计、反馈回流
  handler/        产品入口：HTTP API 和静态 Web 控制台
  rag/            治理 pipeline 内部可调用的知识检索能力
  runtime/        旧编排运行时兼容层，只服务历史工具和 Matrix/agent 运行
  tools/          工具适配层，逐步向 control/runtime 明确边界收口
  models/         跨模块共享的数据模型
  utils/          通用工具函数
  pkg/            可复用的纯模块，优先放无副作用逻辑

eval/             benchmark/dev 验证，不进入产品前端
scripts/          本地调试、RAG、治理 demo 和反馈回流命令
tests/            单元测试和 replay fixture
```

更细的目录边界见：[`docs/project-structure.md`](docs/project-structure.md)

不作为代码提交的内容：`skills/*` baseline 工作区、`.playwright-mcp/`、截图、`docs/reports/` 临时报告、`__pycache__/`。`.claude/skills/` 是项目级协作 skill，可作为项目配置提交。

## 本地运行

```bash
uv sync
uv run --no-sync python scripts/stage_governance_demo.py --output-root docs/reports/stage-governance-demo
```

## 本地 Web 控制台

```bash
PYTHONPATH=src .venv/bin/python -m openagents_orchestration.handler.http.app
```

打开 `http://127.0.0.1:8765`，可以提交治理请求、单独跑 RAG、看审计和反馈回流。

## 测试

```bash
uv run --no-sync pytest tests/test_governance_pipeline.py tests/test_stage_web_console_api.py -q
```

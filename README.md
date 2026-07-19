# 戏台 (Stage) - AI 服务治理平台

Stage 不是泛用多 Agent 编排器。它做的是可配置的服务治理：

- 意图识别
- 业务域映射
- 权限和审批门禁
- RAG / ClaudeCode / 人工通道 / subagent 路由
- 证据、脱敏、闭环、审计、追踪
- 线上反馈回流和 replay 验证

## 现在这套平台解决什么

1. 线上输入进入 Stage 后，先被识别成业务请求，而不是直接当作模型提示词。
2. Stage 依据治理包决定要不要检索、要不要人工、要不要调用 ClaudeCode。
3. 每次输出都要过权限、证据、安全和闭环门禁。
4. 线上反馈会生成治理 patch 和回归 case。
5. replay 通过后，治理包才可以升级发布。

## 主要入口

- 本地 Web 控制台：`http://127.0.0.1:8765`
- 治理平台说明：[`docs/stage-governance-platform.md`](docs/stage-governance-platform.md)
- 反馈回流说明：[`docs/governance-feedback.md`](docs/governance-feedback.md)
- 业务治理包：[`configs/governance/domain_packs/sast_service_desk.yaml`](configs/governance/domain_packs/sast_service_desk.yaml)

## 源码结构

```text
src/openagents_orchestration/
  governance/     Stage 服务治理主域：意图、权限、证据、安全、闭环、审计、反馈
  rag/            知识检索基础设施：抽取、切块、embedding、检索、回答、badcase
  interfaces/     HTTP 和 Web 控制台入口
  runtime/        旧运行时兼容层
  tools/          旧工具适配层
```

## 本地运行

```bash
uv sync
uv run --no-sync python scripts/stage_governance_demo.py --output-root docs/reports/stage-governance-demo
```

## 本地 Web 控制台

```bash
PYTHONPATH=src .venv/bin/python -m openagents_orchestration.interfaces.http.app
```

打开 `http://127.0.0.1:8765`，可以提交治理请求、单独跑 RAG、看审计和反馈回流。

## 测试

```bash
uv run --no-sync pytest tests/test_governance_pipeline.py tests/test_stage_web_console_api.py -q
```

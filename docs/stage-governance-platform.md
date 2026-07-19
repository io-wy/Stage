# Stage 治理平台说明

## 定位

Stage 是一个面向服务治理的执行平台，不是泛用多 Agent 编排器。
它的目标是让请求在可配置的治理包下完成：

- 意图识别
- 业务域映射
- 权限与审批门禁
- RAG / ClaudeCode / 人工通道 / subagent 路由
- 证据、脱敏、追踪、审计
- 线上反馈回流和 replay 验证

## 平台输入

产品入口应该接收这些输入：

- `service_request`：用户真实请求
- `wiki_path`：知识库/制度/文档来源
- `governance_pack_paths`：可选的治理包
- `approvals`：人工审批证据
- `context`：请求上下文
- `embedding` / `top_k` / `kb_path`：RAG 参数

评测入口可以继续接收：

- `eval_id`
- `evals_json`
- `baseline_workspace`

但这些只用于 benchmark，不应出现在产品前端。

## 平台输出

每次运行都应产出：

- `governance.json`：治理产物
- `case_result.json`：最终对外结果
- `audit.jsonl`：审计事件
- `feedback/*`：反馈回流产物
- `regression_case.json`：回归用例

## 业务边界

Stage 现在默认承载的是服务治理类业务，例如：

- 身份与访问支持
- 开发平台权限审批
- 协作平台变更审批
- 内网访问支持
- 设备使用审批
- 事故和缺陷处理

业务变化优先改治理包，不要直接改代码。

## 哪些改代码

只有当平台缺少新的治理原语时才改代码，例如：

- 新权限检查能力
- 新证据类型
- 新安全扫描规则
- 新路由节点类型
- 新 replay / feedback 机制

## 哪些改配置

下面这些优先放到治理包里：

- 业务流程映射
- 风险等级
- 路由策略
- 权限字段
- 证据要求
- 闭环规则
- 安全禁止项
- 回归标签

## 当前约束

- 公开页面必须中文优先
- 敏感证据不能在 public evidence 里明文展示
- 没有相关证据时不能闭环
- 有人工交接要求时必须转人工
- replay 通过后才发布新治理包

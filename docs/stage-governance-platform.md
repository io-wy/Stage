# Stage 治理平台说明

## 定位

Stage 是一个面向服务治理的执行平台，不是泛用多 Agent 编排器。
它的目标是让请求在可配置的治理包下完成：

- 意图识别
- 业务域映射
- 权限与审批门禁
- RAG / ClaudeCode / 人工通道 / subagent 路由
- 受治理 adapter 选择，例如 ClaudeCode + lark-cli、ClaudeCode + gh skill
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

产品入口不接收 `eval_id`。`eval_id` 属于 benchmark/dev，不允许混进真实服务治理请求。

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
- `action_plan`：允许谁执行、用哪个 adapter、能做什么、不能做什么
- `action_result`：实际是否执行、做了什么、外部引用、验证声明
- `feedback/*`：反馈回流产物
- `regression_case.json`：回归用例

## 执行边界

Stage 是治理控制面，不是所有外部 API 的重写工程。真实执行可以交给：

- ClaudeCode + lark-cli / lark skill：文档权限、审批、消息、知识库操作。
- ClaudeCode + gh-cli / gh skill：GitHub 仓库权限、PR、issue、branch protection。
- ClaudeCode + 其他业务 CLI：Jira、GitLab、CMDB、内部运维工具。
- subagent：日志总结、复现梳理、非写入 triage。
- human channel：审批、缺字段补充、责任人确认。

但所有执行都必须满足：

- 执行前有 `ActionPlan`。
- 外部写操作必须通过权限 preflight。
- adapter 只能使用 `adapter_tools` 中声明的工具。
- adapter 不能自行闭环。
- adapter 必须返回 `ActionResult`。
- Stage 根据 `ActionResult`、证据、安全和权限 postcheck 决定是否允许 closed。

adapter 的能力边界放在 `configs/governance/adapter_packs/*.yaml`；业务规则仍然
放在 `configs/governance/domain_packs/*.yaml`。

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

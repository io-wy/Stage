# Stage Adapter 边界

Stage 的 adapter 不是普通工具封装，而是受治理的执行手。Stage 不重写
Lark、GitHub、GitLab、Jira、Kubernetes 等外部系统 API；Stage 负责判断
什么能做、什么不能做、谁批准、证据在哪、怎么验证、怎么审计。

## 职责划分

```text
Stage Core
  负责：意图识别、业务域、风险、权限、证据、安全、闭环、审计、反馈回流

Governance Pack
  负责：业务流程、风险等级、路由策略、权限字段、证据要求、闭环规则

Adapter Pack
  负责：声明可用执行手、工具边界、支持动作、禁止动作、执行后验证要求

Adapter Runtime
  负责：实际调用 ClaudeCode、subagent、lark-cli、gh-cli、jira-cli 等工具

External System
  负责：真实权限、文档、工单、仓库、审批状态
```

Stage 可以让 ClaudeCode 执行，但 ClaudeCode 不能绕过 Stage。任何外部写操作都
必须先形成 `ActionPlan`，通过权限 preflight，再由 adapter runtime 执行，最后
返回 `ActionResult` 进入 postcheck、safety、closure 和 audit。

## ActionPlan 是边界合同

`ActionPlan` 表达这次最多允许做什么：

- `executor`：执行器类型，例如 `human`、`claude_code`、`subagent`、`rag`。
- `adapter_id`：具体 adapter，例如 `claude_code_gh_repo_permission`。
- `adapter_tools`：允许使用的工具，例如 `claude_code`、`gh-cli`。
- `side_effect_level`：副作用等级，例如 `read_only`、`sensitive_read`、
  `workspace_write`、`privileged_write`。
- `allowed_actions`：允许动作。
- `forbidden_actions`：禁止动作。
- `required_approval_fields`：必须补齐的审批字段。
- `required_evidence`：必须引用或验证的证据。
- `verify_requirements`：执行后必须做的校验。
- `rollback_plan`：出错后的回滚要求。

`ActionResult` 表达实际做了什么：

- `executed`：是否真的执行了外部动作。
- `actions_taken`：实际动作列表。
- `side_effects`：产生的副作用。
- `external_refs`：外部系统引用，例如审批单、PR、权限变更记录。
- `verification_claims`：adapter 声称完成的验证。
- `errors`：失败原因。

闭环只能基于 `ActionPlan + ActionResult + evidence + permission + safety`，不能
只基于模型文本。

## 典型 Adapter

文档权限：

```text
用户请求
  -> Stage 判断为 document_permission_grant / privileged_write
  -> 缺审批：executor=human, executed=false
  -> 审批齐全：adapter_id=claude_code_lark_doc_permission
  -> ClaudeCode 使用 lark-cli / lark-drive 执行
  -> 返回 ActionResult
  -> Stage 验证权限状态和审计引用
```

GitHub 仓库权限：

```text
用户请求
  -> Stage 判断为 repo_permission_grant / privileged_write
  -> adapter_id=claude_code_gh_repo_permission
  -> allowed_tools=[claude_code, gh-cli, gh-skill]
  -> 禁止 grant_admin_without_approval
  -> 执行后必须 verify_repo_permission_state
```

事故/缺陷处理：

```text
用户请求
  -> Stage 判断为 incident_triage / sensitive_read
  -> adapter_id=subagent_service_triage
  -> subagent 只能总结日志、复现路径和下一步建议
  -> 禁止 mutate_external_system
  -> 不能因为生成了建议就 closed=true
```

## 改配置还是改代码

新增业务使用已有治理原语时，改治理包和 adapter pack：

- 哪个业务流程用哪个 adapter。
- 需要哪些审批字段。
- 允许哪些工具。
- 哪些动作禁止。
- 执行后怎么验证。

只有缺少新的治理原语时才改 Stage 代码：

- 新副作用等级。
- 新权限检查类型。
- 新证据类型。
- 新安全扫描能力。
- 新 ActionResult postcheck。
- 新 adapter runtime 协议。

## 不允许的边界穿透

- adapter 不能自行决定 `closed=true`。
- adapter 不能自行降级风险。
- adapter 不能绕过 `required_approval_fields`。
- adapter 不能把敏感证据直接放进公开输出。
- ClaudeCode 不能自由选择额外工具执行外部写操作。
- RAG 只能提供证据，不能代表业务完成。
- eval/baseline 不能进入产品前端或线上请求模型。

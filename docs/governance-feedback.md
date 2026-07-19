# Stage 线上反馈回流

线上 case 如果出现误闭环、越权、漏证据、错误路由等问题，不直接改代码。
先记录结构化反馈，再生成治理包 patch 和 regression case，最后 replay 验证。

## 常用标签

- `wrong_closure`：不应该闭环。
- `should_not_close`：必须阻断。
- `should_human_handoff`：应该人工介入。
- `should_call_human`：路由应包含人工通道。
- `should_use_rag`：应该检索制度或知识库证据。
- `should_cite_doc`：回答必须引用文档证据。
- `should_not_use_claude_code`：不应该调用 ClaudeCode。
- `should_require_approval`：必须有审批字段。
- `should_require_owner`：必须有 owner。
- `should_redact_secret`：输出应脱敏。
- `forbid:<pattern>`：把指定文本加入安全禁止输出规则。
- `redact:<pattern>`：把指定文本加入安全禁止输出规则，语义上表示应脱敏。
- `risk:privileged_action`：风险等级应为特权操作。
- `risk:sensitive`：风险等级应为敏感。
- `business_process:<name>`：业务流程应改为指定流程。

## 生成反馈产物

```bash
uv run --no-sync python scripts/governance_feedback.py \
  --governance docs/reports/stage-governance-demo/stage-runs/eval-3-gitlab-admin-access-approval-denial/governance.json \
  --case-result docs/reports/stage-governance-demo/stage-runs/eval-3-gitlab-admin-access-approval-denial/outputs/case_result.json \
  --label wrong_closure \
  --label should_human_handoff \
  --label should_require_approval \
  --note "GitLab 权限必须补齐审批人、范围和工单号后才能闭环"
```

产物会写到 `docs/feedback/<case_id>/`：

- `feedback.json`：原始结构化反馈。
- `governance_patch.yaml`：建议合并到治理包的规则片段。
- `regression_case.json`：可 replay 的回归 case。
- `feedback.md`：给人看的反馈说明。

## 发布要求

治理包 patch 不能直接发布。必须先把 `regression_case.json` 加入回归集，跑 replay。
只有当前 case 修复且旧 case 没有回归，才能发布新的治理包版本。

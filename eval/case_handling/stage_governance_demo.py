"""Human-readable Stage governance demo report for hard-v2 service cases."""

from __future__ import annotations

import json
from collections.abc import Iterable
from html import escape
from pathlib import Path
from typing import Any

from eval.case_handling.stage_governance_runner import run_stage_governance_benchmark
from eval.case_handling.stage_governance_types import StageGovernanceEvalResult

DEFAULT_DEMO_EVAL_IDS = (0, 3, 6, 7, 8, 12)

_REQUIRED_HUMAN_FIELDS = {
    "identity_and_access_support": ["identity_state", "recovery_path"],
    "developer_platform_access_approval": [
        "sponsor",
        "resource_scope",
        "approval_owner",
    ],
    "developer_service_defect_triage": ["logs", "reproduction_steps", "environment"],
    "collaboration_platform_change_approval": [
        "change_scope",
        "approval_owner",
        "environment",
    ],
    "infrastructure_power_incident": ["live_status", "authorized_operator"],
}


def build_stage_governance_demo(
    *,
    evals_json: str | Path,
    baseline_workspace: str | Path,
    output_root: str | Path,
    eval_ids: Iterable[int] = DEFAULT_DEMO_EVAL_IDS,
    baseline_summary_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run the selected hard-v2 cases and write a demo JSON/Markdown report."""

    output_root_path = Path(output_root)
    output_root_path.mkdir(parents=True, exist_ok=True)
    baseline_workspace_path = Path(baseline_workspace)
    baseline_summary_file = (
        Path(baseline_summary_path)
        if baseline_summary_path is not None
        else baseline_workspace_path / "grading_summary.json"
    )
    baseline_summary = _read_json(baseline_summary_file) if baseline_summary_file.exists() else {}

    summary = run_stage_governance_benchmark(
        evals_json=evals_json,
        baseline_workspace=baseline_workspace_path,
        output_root=output_root_path / "stage-runs",
        eval_ids=eval_ids,
        baseline_summary_path=baseline_summary_file,
    )
    baseline_by_id = {
        int(item["eval_id"]): item for item in baseline_summary.get("evals", [])
    }
    cases = [
        _build_demo_case(item, baseline_by_id.get(item.eval_id, {}))
        for item in summary.evals
    ]
    report = {
        "demo_name": "Stage 服务治理演示",
        "demo_note": (
            "Stage 会展示治理计划节点和当前 pipeline 已执行的治理门槛；"
            "这不是完整 DAG 执行器。"
        ),
        "demo_case_count": len(cases),
        "selected_eval_ids": [case["eval_id"] for case in cases],
        "summary": _build_demo_summary(cases),
        "feedback_workflow": _build_feedback_workflow(),
        "cases": cases,
    }

    json_path = output_root_path / "stage_governance_demo.json"
    markdown_path = output_root_path / "stage_governance_demo.md"
    html_path = output_root_path / "index.html"
    report["json_path"] = str(json_path)
    report["markdown_path"] = str(markdown_path)
    report["html_path"] = str(html_path)
    _add_relative_artifact_links(report, output_root_path)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(_render_demo_markdown(report), encoding="utf-8")
    html_path.write_text(_render_demo_html(report), encoding="utf-8")
    return report


def _build_demo_case(
    item: StageGovernanceEvalResult,
    baseline_grade: dict[str, Any],
) -> dict[str, Any]:
    baseline_case = _read_json(Path(item.baseline_case_result_path))
    permission = _permission_summary(item)
    audit_events = _read_audit_events(item.audit_path)
    stage_decision = {
        "closed": item.governed_case_result.get("closed"),
        "family": item.governed_case_result.get("family"),
        "failure_mode": item.governed_case_result.get("failure_mode"),
        "human_questions": list(item.governed_case_result.get("human_questions", [])),
    }
    baseline_decision = {
        "closed": baseline_case.get("closed"),
        "family": baseline_case.get("family"),
        "failure_mode": baseline_case.get("failure_mode"),
        "human_questions_count": len(baseline_case.get("human_questions", [])),
        "pass_rate": baseline_grade.get("pass_rate"),
    }
    return {
        "eval_id": item.eval_id,
        "eval_name": item.eval_name,
        "expected_family": item.expected_family,
        "expected_closed": item.expected_closed,
        "business_process": item.governance["domain"]["business_process"],
        "pack": {
            "pack_id": item.governance["domain"].get("pack_id", "unknown"),
            "pack_version": item.governance["domain"].get("pack_version", "unversioned"),
            "rule_id": item.governance["domain"].get("rule_id", "unknown"),
        },
        "stage_decision": stage_decision,
        "baseline_decision": baseline_decision,
        "route": item.governance["route"],
        "permission": permission,
        "evidence": {
            "count": len(item.governance.get("evidence", [])),
            "sources": [
                evidence.get("source_ref")
                for evidence in item.governance.get("evidence", [])
                if evidence.get("source_ref")
            ],
        },
        "safety": item.governance["safety"],
        "closure": item.governance["closure"],
        "claim_trace": item.governance.get("claim_trace", []),
        "audit_events": audit_events,
        "artifacts": {
            "governance": item.governance_path,
            "case_result": item.case_result_path,
            "audit": item.audit_path,
        },
        "feedback_template": _build_feedback_template(item, permission, stage_decision),
        "baseline_gaps": _baseline_gaps(item, baseline_grade, baseline_case),
    }


def _build_feedback_template(
    item: StageGovernanceEvalResult,
    permission: dict[str, Any],
    stage_decision: dict[str, Any],
) -> dict[str, Any]:
    governance = item.governance
    route = governance.get("route", {})
    domain = governance.get("domain", {})
    case_id = str(governance.get("case_id") or f"hard-v2-{item.eval_id}")
    return {
        "case_id": case_id,
        "eval_id": item.eval_id,
        "eval_name": item.eval_name,
        "prompt": str(governance.get("routing_prompt", "")),
        "governance_ref": item.governance_path,
        "case_result_ref": item.case_result_path,
        "audit_ref": item.audit_path,
        "pack": {
            "pack_id": domain.get("pack_id", "unknown"),
            "pack_version": domain.get("pack_version", "unversioned"),
            "rule_id": domain.get("rule_id", "unknown"),
        },
        "observed": {
            "closed": item.governed_case_result.get("closed"),
            "stage_decision": stage_decision,
            "route": list(route.get("backends", [])),
            "permission_decision": permission["decision"],
            "permission_required_fields": list(permission["required_human_fields"]),
            "safety_blocked": bool(governance.get("safety", {}).get("blocked")),
            "closure_reason": governance.get("closure", {}).get("reason"),
        },
    }


def _build_feedback_workflow() -> dict[str, Any]:
    return {
        "title": "线上反馈回流",
        "labels": [
            {
                "id": "wrong_closure",
                "name": "不应该闭环",
                "description": "Stage 或 baseline 把缺信息/高风险 case 过早关闭了。",
            },
            {
                "id": "should_human_handoff",
                "name": "应该人工介入",
                "description": "必须补审批人、范围、工单号、现场状态等字段。",
            },
            {
                "id": "should_require_approval",
                "name": "应该要求审批",
                "description": "涉及权限、变更、资源操作，不能只给建议就闭环。",
            },
            {
                "id": "should_cite_doc",
                "name": "应该引用制度",
                "description": "结论必须绑定制度文档、日志或服务状态证据。",
            },
            {
                "id": "should_not_use_claude_code",
                "name": "不该调用 ClaudeCode",
                "description": "这个 case 应由 RAG、权限门或人工确认处理。",
            },
            {
                "id": "should_redact_secret",
                "name": "应该脱敏",
                "description": "输出里不应暴露账号、token、内部地址或敏感字段。",
            },
            {
                "id": "risk:privileged_action",
                "name": "风险应为特权操作",
                "description": "请求会触发写入、授权、变更或高权限动作。",
            },
        ],
        "replay_publish_gate": [
            "生成候选治理包 patch",
            "把线上反馈固化成 regression case",
            "回放 hard-v2 + 新增 regression case",
            "检查误闭环、越权、泄密、证据缺失、审计缺失",
            "全部通过才允许发布候选治理包版本",
        ],
    }


def _permission_summary(item: StageGovernanceEvalResult) -> dict[str, Any]:
    domain = item.governance["domain"]
    route = item.governance["route"]
    closure = item.governance["closure"]
    permissions = item.governance.get("permissions", {})
    preflight = permissions.get("preflight", {})
    postcheck = permissions.get("postcheck", {})
    combined = permissions.get("combined", {})
    case_result = item.governed_case_result
    business_process = str(domain.get("business_process", "unknown"))
    risk_class = str(domain.get("risk_class", "normal"))
    human_questions = list(case_result.get("human_questions", []))
    requires_human = bool(
        closure.get("needs_human")
        or route.get("needs_human")
        or "human_channel" in route.get("backends", [])
        or human_questions
    )
    if case_result.get("closed") is True:
        decision = "allowed_to_close"
        reason = "closure gates passed for a safe automatable case"
    elif combined.get("blocked") or combined.get("needs_human"):
        decision = "blocked_until_human_input"
        reason = "permission preflight or postcheck requires human approval"
    elif requires_human:
        decision = "blocked_until_human_input"
        reason = "human confirmation or missing fields are required before closure"
    else:
        decision = "blocked_by_governance"
        reason = "governance gates did not allow closure"

    required_human_fields = (
        []
        if decision == "allowed_to_close"
        else _required_human_fields(
            business_process,
            risk_class=risk_class,
            human_questions=human_questions,
        )
    )
    return {
        "permission_level": risk_class,
        "requested_capabilities": list(route.get("backends", [])),
        "preflight": _first_permission_decision(preflight),
        "postcheck": _first_permission_decision(postcheck),
        "decision": decision,
        "reason": reason,
        "required_human_fields": required_human_fields,
    }


def _first_permission_decision(payload: dict[str, Any]) -> dict[str, str]:
    decisions = payload.get("decisions") or []
    if not decisions:
        return {"capability": "unknown", "decision": "unknown"}
    first = decisions[0]
    return {
        "capability": str(first.get("capability", "unknown")),
        "decision": str(first.get("decision", "unknown")),
    }


def _required_human_fields(
    business_process: str,
    *,
    risk_class: str,
    human_questions: list[str],
) -> list[str]:
    fields = list(_REQUIRED_HUMAN_FIELDS.get(business_process, []))
    if risk_class == "privileged_action" and "approval_owner" not in fields:
        fields.append("approval_owner")
    if human_questions and "human_response" not in fields:
        fields.append("human_response")
    return fields


def _baseline_gaps(
    item: StageGovernanceEvalResult,
    baseline_grade: dict[str, Any],
    baseline_case: dict[str, Any],
) -> list[str]:
    gaps: list[str] = []
    baseline_closed = baseline_grade.get("actual_closed", baseline_case.get("closed"))
    if baseline_closed is True and item.expected_closed is False:
        gaps.append("baseline_over_closed")
    if (
        not baseline_case.get("human_questions")
        and item.governed_case_result.get("human_questions")
    ):
        gaps.append("stage_structured_handoff")
    if item.audit_path:
        gaps.append("stage_audit_trace_available")
    if item.governance.get("evidence"):
        gaps.append("stage_evidence_bound")
    if item.governance["route"].get("nodes"):
        gaps.append("stage_route_plan_visible")
    return gaps


def _build_demo_summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "stage_closed": sum(1 for case in cases if case["stage_decision"]["closed"] is True),
        "stage_blocked": sum(1 for case in cases if case["stage_decision"]["closed"] is False),
        "baseline_over_closed": sum(
            1 for case in cases if "baseline_over_closed" in case["baseline_gaps"]
        ),
        "structured_handoffs": sum(
            1 for case in cases if case["stage_decision"]["human_questions"]
        ),
        "trace_supported": sum(
            1
            for case in cases
            if not any(item.get("status") == "unsupported" for item in case["claim_trace"])
        ),
        "audit_traced": sum(
            1 for case in cases if "stage_audit_trace_available" in case["baseline_gaps"]
        ),
    }


def _render_demo_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Stage 服务治理演示",
        "",
        f"演示说明：{report['demo_note']}",
        "",
        "## 总览",
        "",
        f"- 演示 case 数：{report['demo_case_count']}",
        f"- Stage 已闭环：{summary['stage_closed']}",
        f"- Stage 已阻断：{summary['stage_blocked']}",
        f"- Baseline 误闭环：{summary['baseline_over_closed']}",
        f"- 结构化人工介入：{summary['structured_handoffs']}",
        f"- Trace 可支持：{summary['trace_supported']}",
        f"- 有审计链路：{summary['audit_traced']}",
        "",
        "## 线上反馈回流",
        "",
        "用户在 case 页面打标签，例如“不应该闭环”“应该人工介入”“应该引用制度”。Stage 将反馈固化为三类产物：反馈记录、治理包 patch 建议、可 replay 的 regression case。",
        "",
        "Replay 发布判断：候选治理包必须通过 hard-v2 和新增 regression case，且不能出现误闭环、越权、泄密、证据缺失或审计缺失，才允许发布。",
        "",
        "## Case 明细",
        "",
    ]
    for case in report["cases"]:
        permission = case["permission"]
        stage = case["stage_decision"]
        baseline = case["baseline_decision"]
        route_nodes = " -> ".join(
            _node_label(node["capability"]) for node in case["route"]["nodes"]
        )
        human_questions = _display_human_questions(case)
        lines.extend(
            [
                f"### Eval {case['eval_id']}: {case['eval_name']}",
                "",
                f"- 业务流程：`{case['business_process']}`",
                f"- 治理包：`{case['pack']['pack_id']}@{case['pack']['pack_version']}#{case['pack']['rule_id']}`",
                f"- 路由节点：`{route_nodes}`",
                f"- Stage 决策：{_closure_label(stage['closed'])}，原因：`{_failure_mode_label(stage['failure_mode'])}`",
                f"- Baseline 决策：{_closure_label(baseline['closed'])}，通过率：{baseline['pass_rate']}",
                "",
                "**权限判断**",
                "",
                f"- 权限等级：`{_permission_level_label(permission['permission_level'])}`",
                f"- 权限结论：`{_permission_decision_label(permission['decision'])}`",
                f"- Preflight：`{_permission_capability_label(permission['preflight']['capability'])}` / `{_permission_gate_label(permission['preflight']['decision'])}`",
                f"- Postcheck：`{_permission_capability_label(permission['postcheck']['capability'])}` / `{_permission_gate_label(permission['postcheck']['decision'])}`",
                f"- 需要人工补充字段：`{_human_fields_label(permission['required_human_fields'])}`",
                f"- 证据/安全/闭环：证据 {case['evidence']['count']} 条，安全 `{_safety_label(case)}`，闭环 `{_closure_reason_label(case['closure']['reason'])}`",
                "",
                "**人工介入**",
                "",
            ]
        )
        for question in human_questions[:3]:
            lines.append(f"- {question}")
        lines.extend(
            [
                "",
                "**Baseline 差异**",
                "",
            ]
        )
        for gap in case["baseline_gaps"]:
            lines.append(f"- {_gap_label(gap)}")
        lines.append("")
    return "\n".join(lines)


def _render_demo_html(report: dict[str, Any]) -> str:
    summary = report["summary"]
    case_cards = "\n".join(_render_case_card(case) for case in report["cases"])
    feedback_section = _render_feedback_workflow_section(report)
    feedback_script = _render_feedback_script(report)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>Stage 服务治理演示</title>
  <style>
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f5f5f2;
      color: #20201d;
    }}
    header {{
      padding: 32px 44px 20px;
      background: #18201c;
      color: #f8f7f0;
    }}
    main {{
      max-width: 1120px;
      margin: 0 auto;
      padding: 28px 24px 48px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 30px;
      font-weight: 700;
    }}
    h2 {{
      margin: 30px 0 14px;
      font-size: 20px;
    }}
    .note {{
      margin: 0;
      color: #ddd8c8;
      max-width: 880px;
      line-height: 1.5;
    }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 12px;
      margin: 22px 0 30px;
    }}
    .metric, .case {{
      background: #ffffff;
      border: 1px solid #dedbd2;
      border-radius: 8px;
      box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
    }}
    .metric {{
      padding: 14px 16px;
    }}
    .metric strong {{
      display: block;
      font-size: 24px;
      margin-bottom: 3px;
    }}
    .metric span {{
      color: #666155;
      font-size: 13px;
    }}
    .case {{
      padding: 20px;
      margin: 14px 0;
    }}
    .workflow {{
      background: #ffffff;
      border: 1px solid #dedbd2;
      border-radius: 8px;
      padding: 18px 20px;
      margin: 10px 0 28px;
    }}
    .case h3 {{
      margin: 0 0 12px;
      font-size: 18px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 14px;
    }}
    .panel {{
      border-left: 3px solid #64766a;
      padding-left: 12px;
    }}
    .feedback-panel {{
      border-top: 1px solid #e5e2d8;
      margin-top: 16px;
      padding-top: 16px;
    }}
    .label {{
      color: #666155;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      margin-bottom: 4px;
    }}
    code {{
      background: #eeece4;
      border-radius: 4px;
      padding: 2px 5px;
    }}
    ul {{
      margin: 8px 0 0;
      padding-left: 20px;
    }}
    .tag-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 10px 0;
    }}
    .tag-button, .download-button {{
      border: 1px solid #b9b4a8;
      background: #faf9f5;
      color: #20201d;
      border-radius: 6px;
      padding: 7px 10px;
      font-size: 13px;
      cursor: pointer;
    }}
    .tag-button.active {{
      background: #1f5d4d;
      border-color: #1f5d4d;
      color: #ffffff;
    }}
    .download-button {{
      background: #20201d;
      border-color: #20201d;
      color: #ffffff;
      margin-top: 8px;
    }}
    textarea {{
      box-sizing: border-box;
      width: 100%;
      min-height: 72px;
      border: 1px solid #c9c4b8;
      border-radius: 6px;
      padding: 9px 10px;
      font: inherit;
      resize: vertical;
    }}
    pre {{
      white-space: pre-wrap;
      word-break: break-word;
      background: #f6f4ec;
      border: 1px solid #e5e2d8;
      border-radius: 6px;
      padding: 10px;
      font-size: 12px;
      line-height: 1.45;
      max-height: 220px;
      overflow: auto;
    }}
    a {{
      color: #1f5d4d;
      text-decoration: none;
      font-weight: 600;
    }}
    a:hover {{
      text-decoration: underline;
    }}
  </style>
</head>
<body>
  <header>
    <h1>Stage 服务治理演示</h1>
    <p class="note">{escape(report["demo_note"])}</p>
  </header>
  <main>
    <section class="metrics">
      {_metric("演示 case", report["demo_case_count"])}
      {_metric("Stage 已闭环", summary["stage_closed"])}
      {_metric("Stage 已阻断", summary["stage_blocked"])}
      {_metric("Baseline 误闭环", summary["baseline_over_closed"])}
      {_metric("结构化人工介入", summary["structured_handoffs"])}
      {_metric("Trace 可支持", summary["trace_supported"])}
      {_metric("有审计链路", summary["audit_traced"])}
    </section>
    {feedback_section}
    <h2>Case 明细</h2>
    {case_cards}
  </main>
  {feedback_script}
</body>
</html>
"""


def _render_case_card(case: dict[str, Any]) -> str:
    stage = case["stage_decision"]
    baseline = case["baseline_decision"]
    permission = case["permission"]
    route_nodes = " -> ".join(
        _node_label(node["capability"]) for node in case["route"]["nodes"]
    )
    human_questions = _display_human_questions(case)
    gaps = [_gap_label(gap) for gap in case["baseline_gaps"]] or ["无"]
    links = case.get("artifact_links", {})
    return f"""
<article class="case">
  <h3>Eval {case["eval_id"]}: {escape(case["eval_name"])}</h3>
  <div class="grid">
    <div class="panel">
      <div class="label">路由</div>
      <div>业务流程：<code>{escape(case["business_process"])}</code></div>
      <div>治理包：<code>{escape(case["pack"]["pack_id"])}@{escape(case["pack"]["pack_version"])}#{escape(case["pack"]["rule_id"])}</code></div>
      <div>节点：<code>{escape(route_nodes)}</code></div>
    </div>
    <div class="panel">
      <div class="label">闭环决策</div>
      <div>Stage：<code>{escape(_closure_label(stage["closed"]))}</code>，原因 <code>{escape(_failure_mode_label(stage["failure_mode"]))}</code></div>
      <div>Baseline：<code>{escape(_closure_label(baseline["closed"]))}</code>，通过率 <code>{escape(str(baseline["pass_rate"]))}</code></div>
    </div>
    <div class="panel">
      <div class="label">权限判断</div>
      <div>权限等级：<code>{escape(_permission_level_label(permission["permission_level"]))}</code></div>
      <div>权限结论：<code>{escape(_permission_decision_label(permission["decision"]))}</code></div>
      <div>Preflight：<code>{escape(_permission_capability_label(permission["preflight"]["capability"]))}</code> / <code>{escape(_permission_gate_label(permission["preflight"]["decision"]))}</code></div>
      <div>Postcheck：<code>{escape(_permission_capability_label(permission["postcheck"]["capability"]))}</code> / <code>{escape(_permission_gate_label(permission["postcheck"]["decision"]))}</code></div>
      <div>需补充字段：<code>{escape(_human_fields_label(permission["required_human_fields"]))}</code></div>
    </div>
    <div class="panel">
      <div class="label">证据 / 安全 / 闭环</div>
      <div>证据：<code>{escape(str(case["evidence"]["count"]))}</code> 条</div>
      <div>Trace：<code>{escape(str(len(case["claim_trace"])))}</code> 条</div>
      <div>安全：<code>{escape(_safety_label(case))}</code></div>
      <div>闭环门：<code>{escape(_closure_reason_label(case["closure"]["reason"]))}</code></div>
    </div>
    <div class="panel">
      <div class="label">打开过程文件</div>
      <div><a href="{escape(links.get("governance", "#"))}">governance.json</a></div>
      <div><a href="{escape(links.get("case_result", "#"))}">case_result.json</a></div>
      <div><a href="{escape(links.get("audit", "#"))}">audit.jsonl</a></div>
    </div>
  </div>
  <div class="grid">
    <div class="panel">
      <div class="label">人工介入</div>
      <ul>{_items(human_questions)}</ul>
    </div>
    <div class="panel">
      <div class="label">Baseline 差异</div>
      <ul>{_items(gaps)}</ul>
    </div>
  </div>
  {_render_case_feedback_panel(case)}
</article>
"""


def _render_feedback_workflow_section(report: dict[str, Any]) -> str:
    workflow = report["feedback_workflow"]
    gates = workflow["replay_publish_gate"]
    return f"""
    <section class="workflow">
      <h2>线上反馈回流</h2>
      <p>在具体 case 上直接打标签和写备注，Stage 会把这次纠错变成反馈记录、治理包 patch 建议和可 replay 的回归 case。</p>
      <div class="grid">
        <div class="panel">
          <div class="label">用户怎么做</div>
          <ul>
            <li>打开一个 case，选择“不应该闭环”“应该人工介入”等标签。</li>
            <li>写一句人工判断，例如缺少审批人、范围或工单号。</li>
            <li>点击“下载反馈包”，交给 Stage 导入回放。</li>
          </ul>
        </div>
        <div class="panel">
          <div class="label">Replay 发布判断</div>
          <ul>{_items(gates)}</ul>
        </div>
      </div>
    </section>
"""


def _render_case_feedback_panel(case: dict[str, Any]) -> str:
    return f"""
  <div class="feedback-panel" data-feedback-case="{escape(case["feedback_template"]["case_id"])}">
    <div class="label">线上反馈回流</div>
    <div class="tag-row" data-feedback-tags></div>
    <textarea data-feedback-note placeholder="写一句人工判断，例如：这里缺少审批人、资源范围和工单号，不能闭环。"></textarea>
    <button class="download-button" type="button" data-feedback-download>下载反馈包</button>
    <pre data-feedback-preview></pre>
  </div>
"""


def _render_feedback_script(report: dict[str, Any]) -> str:
    payload = {
        "labels": report["feedback_workflow"]["labels"],
        "replay_publish_gate": report["feedback_workflow"]["replay_publish_gate"],
        "cases": {
            case["feedback_template"]["case_id"]: case["feedback_template"]
            for case in report["cases"]
        },
    }
    data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return f"""
  <script type="application/json" id="feedback-report-data">{data_json}</script>
  <script>
    (function () {{
      const dataNode = document.getElementById("feedback-report-data");
      if (!dataNode) return;
      const feedbackData = JSON.parse(dataNode.textContent);

      function unique(values) {{
        return Array.from(new Set(values.filter(Boolean)));
      }}

      function selectedLabels(panel) {{
        return Array.from(panel.querySelectorAll(".tag-button.active")).map((button) => button.dataset.label);
      }}

      function expectedClosed(labels, template) {{
        const blockers = ["wrong_closure", "should_not_close", "should_human_handoff"];
        return blockers.some((label) => labels.includes(label)) ? false : Boolean(template.observed.closed);
      }}

      function routeBackends(labels, template) {{
        let backends = Array.isArray(template.observed.route) ? template.observed.route.slice() : [];
        if (labels.includes("should_not_use_claude_code")) {{
          backends = backends.filter((item) => item !== "claude_code");
        }}
        if ((labels.includes("should_human_handoff") || labels.includes("should_require_approval")) && !backends.includes("human_channel")) {{
          backends.push("human_channel");
        }}
        if (labels.includes("should_cite_doc") && !backends.includes("rag_retrieval")) {{
          backends.unshift("rag_retrieval");
        }}
        return unique(backends.length ? backends : ["rag_retrieval"]);
      }}

      function permissionFields(labels, template) {{
        const fields = Array.isArray(template.observed.permission_required_fields) ? template.observed.permission_required_fields.slice() : [];
        if (labels.includes("wrong_closure") || labels.includes("should_human_handoff") || labels.includes("should_require_approval")) {{
          fields.push("approver", "target", "scope", "ticket_id");
        }}
        return unique(fields);
      }}

      function assertions(labels, template) {{
        const result = [{{ name: expectedClosed(labels, template) ? "case_closed" : "case_not_closed", description: "derived from online feedback labels" }}];
        if (labels.includes("should_human_handoff")) result.push({{ name: "asks_concrete_human_question", description: "case should ask for human follow-up instead of closing" }});
        if (labels.includes("should_cite_doc")) result.push({{ name: "cites_relevant_sources", description: "case should cite the governing documents" }});
        if (labels.includes("should_not_use_claude_code")) result.push({{ name: "does_not_invoke_claude_code", description: "case should not route to ClaudeCode for this request" }});
        if (labels.includes("should_redact_secret")) result.push({{ name: "whole_output_secret_safe", description: "case should not expose secret-bearing content" }});
        return result;
      }}

      function governancePatchYaml(labels, template) {{
        const backends = routeBackends(labels, template);
        const fields = permissionFields(labels, template);
        const risk = labels.includes("risk:privileged_action") ? "privileged_action" : "keep_current";
        return [
          "pack_id: " + template.pack.pack_id,
          "version: feedback-proposal",
          "rules:",
          "  - id: feedback_" + template.case_id,
          "    source_case_id: " + template.case_id,
          "    labels:",
          labels.length ? labels.map((label) => "      - " + label).join("\\n") : "      []",
          "    profile:",
          "      business_process: " + template.pack.rule_id,
          "      risk_class: " + risk,
          "      backend_plan:",
          backends.map((backend) => "        - " + backend).join("\\n"),
          "      permission_required_fields:",
          fields.length ? fields.map((field) => "        - " + field).join("\\n") : "        []",
          "      closure_policy: verify_before_close"
        ].join("\\n");
      }}

      function buildPackage(panel) {{
        const caseId = panel.dataset.feedbackCase;
        const template = feedbackData.cases[caseId];
        const labels = selectedLabels(panel);
        const note = panel.querySelector("[data-feedback-note]").value.trim();
        return {{
          artifact_type: "stage_governance_feedback_package",
          case_id: caseId,
          labels,
          note,
          source_case: template,
          feedback: {{
            case_id: caseId,
            labels,
            note,
            prompt: template.prompt,
            observed: template.observed
          }},
          governance_patch_yaml: governancePatchYaml(labels, template),
          regression_case: {{
            id: "regression-" + caseId,
            name: "feedback-" + caseId,
            business_process: template.pack.rule_id,
            expected_closed: expectedClosed(labels, template),
            prompt: template.prompt,
            labels,
            expected_output: note || "由线上反馈生成",
            route_backends: routeBackends(labels, template),
            assertions: assertions(labels, template)
          }},
          replay_publish_gate: feedbackData.replay_publish_gate
        }};
      }}

      function refreshPreview(panel) {{
        const preview = panel.querySelector("[data-feedback-preview]");
        preview.textContent = JSON.stringify(buildPackage(panel), null, 2);
      }}

      function downloadPackage(panel) {{
        const pkg = buildPackage(panel);
        const blob = new Blob([JSON.stringify(pkg, null, 2)], {{ type: "application/json;charset=utf-8" }});
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = pkg.case_id + "-feedback-package.json";
        document.body.appendChild(link);
        link.click();
        URL.revokeObjectURL(link.href);
        link.remove();
      }}

      document.querySelectorAll("[data-feedback-case]").forEach((panel) => {{
        const tags = panel.querySelector("[data-feedback-tags]");
        feedbackData.labels.forEach((label) => {{
          const button = document.createElement("button");
          button.className = "tag-button";
          button.type = "button";
          button.dataset.label = label.id;
          button.textContent = label.name;
          button.title = label.description;
          button.addEventListener("click", () => {{
            button.classList.toggle("active");
            refreshPreview(panel);
          }});
          tags.appendChild(button);
        }});
        panel.querySelector("[data-feedback-note]").addEventListener("input", () => refreshPreview(panel));
        panel.querySelector("[data-feedback-download]").addEventListener("click", () => downloadPackage(panel));
        refreshPreview(panel);
      }});
    }})();
  </script>
"""


def _metric(label: str, value: Any) -> str:
    return f'<div class="metric"><strong>{escape(str(value))}</strong><span>{escape(label)}</span></div>'


def _items(values: list[str]) -> str:
    return "".join(f"<li>{escape(str(value))}</li>" for value in values)


def _display_human_questions(case: dict[str, Any]) -> list[str]:
    if not case["stage_decision"]["human_questions"]:
        return ["无"]
    process = case["business_process"]
    templates = {
        "identity_and_access_support": [
            "请确认是否已经等待文档要求的重试窗口，并重新尝试注册或找回。"
        ],
        "developer_platform_access_approval": [
            "请补充 sponsor/审批人、项目或仓库名称，以及需要的权限范围。"
        ],
        "developer_service_defect_triage": [
            "请补充错误日志、复现步骤、运行环境和影响范围。"
        ],
        "collaboration_platform_change_approval": [
            "请补充变更范围、审批人、影响范围和回滚方案。"
        ],
        "infrastructure_power_incident": [
            "请补充当前 UPS 状态、报警信息和授权操作人。"
        ],
    }
    return templates.get(process, case["stage_decision"]["human_questions"])


def _node_label(capability: str) -> str:
    labels = {
        "rag_retrieval": "RAG 证据检索",
        "subagent": "子代理调查",
        "claude_code": "ClaudeCode 执行",
        "human_channel": "人工确认",
        "verify": "校验",
        "close": "闭环判断",
    }
    return labels.get(capability, capability)


def _closure_label(closed: Any) -> str:
    if closed is True:
        return "已闭环"
    if closed is False:
        return "已阻断"
    return "未知"


def _failure_mode_label(failure_mode: Any) -> str:
    labels = {
        None: "无",
        "missing_required_information": "缺少必要信息",
        "source_conflict": "来源冲突",
        "need_human_judgment": "需要人工判断",
        "tool_unavailable": "工具不可用",
        "over_budget": "超出预算",
        "safety_policy_failure": "安全策略阻断",
        "permission_policy_failure": "权限策略阻断",
        "verification_failure": "校验失败",
    }
    return labels.get(failure_mode, str(failure_mode))


def _permission_level_label(level: str) -> str:
    labels = {
        "normal": "普通",
        "sensitive": "敏感",
        "privileged_action": "特权操作",
    }
    return labels.get(level, level)


def _permission_decision_label(decision: str) -> str:
    labels = {
        "allowed_to_close": "允许闭环",
        "blocked_until_human_input": "等待人工补充后再处理",
        "blocked_by_governance": "治理门槛阻断",
    }
    return labels.get(decision, decision)


def _permission_capability_label(capability: str) -> str:
    labels = {
        "read_or_analyze": "只读分析",
        "code_or_tool_analysis": "代码/工具分析",
        "sensitive_read_or_triage": "敏感信息调查",
        "write_action": "写入动作",
        "privileged_action": "特权动作",
        "claimed_write_action": "声称已执行写入",
        "unknown": "未知",
    }
    return labels.get(capability, capability)


def _permission_gate_label(decision: str) -> str:
    labels = {
        "allow": "放行",
        "block": "阻断",
        "needs_human": "需要人工确认",
        "unknown": "未知",
    }
    return labels.get(decision, decision)


def _safety_label(case: dict[str, Any]) -> str:
    blocked = bool(case.get("safety", {}).get("blocked"))
    return "阻断" if blocked else "通过"


def _closure_reason_label(reason: str) -> str:
    labels = {
        "ok": "允许闭环",
        "not_closed": "未闭环",
        "handoff_required": "需要人工介入",
        "permission_policy_failed": "权限策略未通过",
        "safety_or_verification_failed": "安全或校验未通过",
    }
    return labels.get(reason, reason)


def _human_fields_label(fields: list[str]) -> str:
    labels = {
        "identity_state": "身份状态",
        "recovery_path": "找回路径",
        "approval_owner": "审批人",
        "human_response": "人工回复",
        "sponsor": "sponsor",
        "resource_scope": "资源范围",
        "logs": "日志",
        "reproduction_steps": "复现步骤",
        "environment": "环境信息",
        "change_scope": "变更范围",
        "live_status": "实时状态",
        "authorized_operator": "授权操作人",
    }
    return ", ".join(labels.get(field, field) for field in fields) or "无"


def _gap_label(gap: str) -> str:
    labels = {
        "baseline_over_closed": "Baseline 误闭环",
        "stage_structured_handoff": "Stage 生成结构化人工介入",
        "stage_audit_trace_available": "Stage 有可追踪审计链路",
        "stage_evidence_bound": "Stage 绑定了证据来源",
        "stage_route_plan_visible": "Stage 展示了治理路由计划",
    }
    return labels.get(gap, gap)


def _add_relative_artifact_links(report: dict[str, Any], output_root: Path) -> None:
    for case in report["cases"]:
        links = {}
        for name, artifact_path in case.get("artifacts", {}).items():
            links[name] = Path(artifact_path).relative_to(output_root).as_posix()
        case["artifact_links"] = links


def _read_audit_events(audit_path: str | Path) -> list[str]:
    path = Path(audit_path)
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        events.append(str(json.loads(line).get("event_type", "")))
    return [event for event in events if event]


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))

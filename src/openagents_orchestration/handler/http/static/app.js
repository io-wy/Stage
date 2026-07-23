let currentRun = null;
let currentView = "run";

const $ = (selector) => document.querySelector(selector);

const TEXT = {
  answer: "回复",
  approver: "审批人",
  ask_human: "询问人工",
  backend_planned: "规划执行后端",
  bypass_governance: "绕过治理",
  "claude_code_gh_repo_permission": "ClaudeCode + GitHub 权限",
  "claude_code_lark_doc_permission": "ClaudeCode + 飞书文档权限",
  case_blocked: "未闭环",
  case_closed: "已闭环",
  claim_trace_built: "生成声明追踪",
  close: "闭环判断",
  close_without_verification: "未验证闭环",
  closed: "已闭环",
  closure: "闭环",
  closure_gate: "闭环门禁",
  closure_checked: "完成闭环检查",
  create_handoff: "创建人工交接",
  document_permission_approval: "文档权限审批",
  domain_resolved: "匹配治理包",
  evidence_added: "新增证据",
  evidence_relevance: "证据相关性",
  external_write_without_approval: "未审批外部写入",
  "gh-cli": "GitHub CLI",
  "gh-skill": "GitHub skill",
  github_repo_access_approval: "GitHub 仓库权限审批",
  human_channel: "人工通道",
  human_handoff_created: "已创建人工交接",
  identity_and_access_support: "身份与访问支持",
  intent_classified: "识别意图",
  "lark-cli": "飞书 CLI",
  "lark-drive": "飞书云文档",
  missing_required_information: "缺少必要信息",
  no_query_evidence_overlap: "证据与请求不匹配",
  not_required: "无需证据支撑",
  "perm:internal": "内部资料",
  "perm:public": "公开资料",
  "perm:sensitive": "敏感资料",
  permission_policy_failure: "权限策略失败",
  permission_postcheck_checked: "完成权限后置检查",
  permission_preflight_checked: "完成权限预检",
  post_execution_state_check: "执行后状态校验",
  prepare_change: "准备变更",
  privileged_action: "特权操作",
  privileged_write: "特权写入",
  public_safe: "可公开",
  rag_retrieval: "RAG 检索",
  read_only: "只读",
  request_human_approval: "请求人工审批",
  required_evidence_present: "必要证据存在",
  revoke_or_restore_previous_permission: "撤销或恢复原权限",
  restricted: "受限",
  safety_scan: "安全扫描",
  safety_checked: "完成安全检查",
  secret_risk: "疑似敏感",
  selected: "已选用",
  sensitive_read: "敏感读取",
  service_desk: "服务台",
  source_conflict: "证据冲突",
  scope: "权限范围",
  stage_governance: "Stage 治理",
  supported: "已支撑",
  target: "目标资源",
  ticket_id: "工单号",
  tool_invoked: "调用工具",
  traceability_passed: "追踪通过",
  unknown: "未知",
  unsupported: "未支撑",
  verification_checked: "完成证据校验",
  verification_failure: "证据校验失败",
  verify: "校验",
  workspace_write: "工作区写入",
};

const NODE_NAMES = {
  Intent: "意图识别",
  Domain: "治理包匹配",
  Route: "路由规划",
  Action: "执行计划",
  Permission: "权限检查",
  Evidence: "证据",
  Verification: "证据校验",
  Safety: "安全扫描",
  Closure: "闭环判断",
  Trace: "声明追踪",
  Audit: "审计",
};

const JSON_KEYS = {
  actions: "动作",
  action_plan: "执行计划",
  action_result: "执行结果",
  action_type: "动作类型",
  adapter_id: "执行适配器",
  adapter_tools: "允许工具",
  answer: "回复",
  backends: "后端",
  blocked: "已阻断",
  business_process: "业务流程",
  case_id: "请求编号",
  claim_count: "声明数量",
  claim_type: "声明类型",
  closed: "已闭环",
  closure_policy: "闭环策略",
  confidence: "置信度",
  decision: "决策",
  evidence_count: "证据数量",
  event_type: "事件类型",
  executed: "已执行",
  executor: "执行器",
  execution_mode: "执行模式",
  external_refs: "外部引用",
  failure_mode: "失败模式",
  forbidden_actions: "禁止动作",
  governance: "治理产物",
  human_questions: "人工问题",
  kb_path: "知识库缓存",
  matched_anchor_terms: "命中实体词",
  needs_human: "需要人工",
  nodes: "节点",
  overlap_ratio: "重合比例",
  overlap_terms: "重合词",
  passed: "通过",
  reason: "原因",
  reasons: "原因列表",
  relevant_evidence_count: "相关证据数",
  required_approval_fields: "审批字段",
  required_evidence: "证据要求",
  risk_class: "风险等级",
  rollback_plan: "回滚要求",
  route_label: "路由类型",
  run_id: "运行编号",
  selected: "已选用",
  sensitivity: "敏感级别",
  side_effect_level: "副作用等级",
  source_ref: "来源",
  source_refs: "来源",
  status: "状态",
  summary: "摘要",
  text: "文本",
  timestamp: "时间",
  verification_claims: "验证声明",
  verify_requirements: "验证要求",
  workflow_type: "流程类型",
  wiki_path: "知识库目录",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail || `${response.status} ${response.statusText}`);
  }
  return response.json();
}

function setBusy(button, busy, text) {
  button.disabled = busy;
  if (text) {
    button.textContent = text;
  }
}

function renderJson(target, payload) {
  target.textContent = JSON.stringify(localizePayload(payload), null, 2);
}

function compactText(value, fallback = "-") {
  if (value === null || value === undefined || value === "") {
    return fallback;
  }
  if (Array.isArray(value)) {
    return value.length ? value.map(label).join(", ") : fallback;
  }
  return label(value);
}

function label(value) {
  if (value === true) {
    return "是";
  }
  if (value === false) {
    return "否";
  }
  const key = String(value ?? "");
  return TEXT[key] || key;
}

function localizePayload(payload) {
  if (Array.isArray(payload)) {
    return payload.map(localizePayload);
  }
  if (payload && typeof payload === "object") {
    return Object.fromEntries(
      Object.entries(payload).map(([key, value]) => [
        JSON_KEYS[key] || key,
        localizePayload(value),
      ]),
    );
  }
  return label(payload);
}

async function loadHealth() {
  try {
    const health = await api("/api/health");
    $("#health").textContent = health.status === "ok" ? "在线" : label(health.status);
  } catch (error) {
    $("#health").textContent = "离线";
  }
}

function showView(view) {
  currentView = view;
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.view === view);
  });
  document.querySelectorAll("[data-view-panel]").forEach((panel) => {
    const views = (panel.dataset.viewPanel || "").split(/\s+/);
    panel.classList.toggle("active", views.includes(view));
  });
  if (view === "history") {
    loadHistory();
  }
  if (view === "audit" && currentRun?.run_key) {
    loadAudit(currentRun.run_key);
  }
}

async function runCase() {
  const button = $("#runCaseButton");
  setBusy(button, true, "运行中");
  try {
    const result = await api("/api/governance/run", {
      method: "POST",
      body: JSON.stringify({
        service_request: $("#serviceRequest").value,
        wiki_path: $("#wikiPath").value,
        embedding: $("#embeddingMode").value,
        top_k: Number($("#topK").value),
      }),
    });
    currentRun = result;
    renderRun(result);
    loadHistory();
    if (result.run_key) {
      loadAudit(result.run_key);
    }
    showView("run");
    $("#feedbackStatus").textContent = "";
  } catch (error) {
    $("#nodeDetails").textContent = error.message;
  } finally {
    setBusy(button, false, "提交治理请求");
  }
}

function renderRun(result) {
  $("#activeCase").textContent =
    result.governance?.case_id || result.run_key || "当前请求";
  $("#closedValue").textContent = result.closed ? "是" : "否";
  $("#failureValue").textContent = compactText(result.failure_mode);
  $("#routeValue").textContent = compactText(result.route.backends);
  $("#humanValue").textContent = result.case_result.human_questions?.length
    ? "需要"
    : "不需要";
  const actionPlan = result.governance?.action_plan || result.route?.action_plan || {};
  const actionResult = result.governance?.action_result || {};
  $("#executorValue").textContent = compactText(actionPlan.executor);
  $("#decisionStrip").className = `decision ${result.closed ? "closed-yes" : "closed-no"}`;
  $("#runSummary").textContent = result.run_key || "当前运行";
  $("#businessValue").textContent = compactText(
    result.governance?.domain?.business_process,
  );
  $("#adapterValue").textContent = compactText(actionPlan.adapter_id, "未指定");
  $("#verificationValue").textContent = result.governance?.verification?.passed
    ? `通过 · ${result.governance.verification.relevant_evidence_count || 0}/${result.governance.verification.evidence_count || 0}`
    : `未通过 · ${compactText(result.governance?.verification?.reasons)}`;
  $("#permissionValue").textContent = result.permissions?.combined?.needs_human
    ? "需要人工"
    : result.permissions?.combined?.passed
      ? "通过"
      : "阻断";
  $("#safetyValue").textContent = result.safety?.blocked ? "阻断" : "通过";
  $("#overviewEvidenceValue").textContent = `${result.evidence.length}`;
  $("#evidenceCount").textContent = `${result.evidence.length}`;
  $("#traceCount").textContent = `${result.claim_trace.length}`;
  renderActionContract(actionPlan, actionResult);
  renderTimeline(result);
  renderEvidence(result.evidence);
  renderTrace(result.claim_trace);
  if (result.rag && !result.rag.skipped) {
    renderRagFromLog(result.rag);
  }
  renderJson($("#artifacts"), result.artifact_paths);
}

function renderActionContract(actionPlan, actionResult) {
  const target = $("#actionContract");
  if (!actionPlan || !Object.keys(actionPlan).length) {
    $("#actionBoundaryStatus").textContent = "没有执行计划";
    target.innerHTML = '<div class="empty">当前请求没有执行边界</div>';
    return;
  }
  $("#actionBoundaryStatus").textContent = actionResult?.executed
    ? "已执行"
    : "未执行";
  target.innerHTML = [
    contractCard("执行器", compactText(actionPlan.executor), [
      `副作用：${compactText(actionPlan.side_effect_level)}`,
      `执行：${actionResult?.executed ? "已执行" : "未执行"}`,
    ]),
    contractCard("执行适配器", compactText(actionPlan.adapter_id, "未指定"), [
      ...(actionPlan.adapter_tools || []).map(label),
    ]),
    contractCard("审批字段", compactText(actionPlan.required_approval_fields), []),
    contractCard("允许动作", compactText(actionPlan.allowed_actions), []),
    contractCard("禁止动作", compactText(actionPlan.forbidden_actions), []),
    contractCard("验证要求", compactText(actionPlan.verify_requirements), [
      ...(actionPlan.rollback_plan || []).map((item) => `回滚：${label(item)}`),
    ]),
  ].join("");
}

function contractCard(title, main, pills) {
  const renderedPills = (pills || [])
    .filter(Boolean)
    .map((item) => `<span class="pill">${escapeHtml(item)}</span>`)
    .join("");
  return `<article class="contract-card">
    <span>${escapeHtml(title)}</span>
    <strong>${escapeHtml(main)}</strong>
    <div>${renderedPills}</div>
  </article>`;
}

function renderTimeline(result) {
  const governance = result.governance;
  const nodes = [
    ["Intent", governance.intent, "info"],
    ["Domain", governance.domain, "info"],
    ["Route", governance.route, governance.route.needs_human ? "warn" : "ok"],
    [
      "Action",
      {
        action_plan: governance.action_plan,
        action_result: governance.action_result,
      },
      governance.action_result?.executed ? "ok" : "warn",
    ],
    [
      "Permission",
      governance.permissions,
      governance.permissions?.combined?.needs_human ? "warn" : "ok",
    ],
    ["Evidence", governance.public_evidence, governance.public_evidence?.length ? "ok" : "warn"],
    [
      "Verification",
      governance.verification,
      governance.verification?.passed ? "ok" : "danger",
    ],
    ["Safety", governance.safety, governance.safety?.blocked ? "danger" : "ok"],
    [
      "Closure",
      governance.closure,
      governance.closure?.closed ? "ok" : "warn",
    ],
    ["Trace", governance.claim_trace, traceState(governance.claim_trace)],
    ["Audit", governance.audit_events, governance.audit_events?.length ? "ok" : "warn"],
  ];
  $("#timeline").innerHTML = nodes
    .map(
      ([name, payload, state], index) =>
        `<button class="node ${state}" data-index="${index}" type="button">
          <strong>${escapeHtml(NODE_NAMES[name] || name)}</strong>
          <span>${escapeHtml(nodeHint(name, payload))}</span>
        </button>`,
    )
    .join("");
  document.querySelectorAll(".node").forEach((button, index) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".node").forEach((node) => {
        node.classList.remove("active");
      });
      button.classList.add("active");
      renderJson($("#nodeDetails"), {
        节点: NODE_NAMES[nodes[index][0]] || nodes[index][0],
        数据: nodes[index][1],
      });
    });
  });
  document.querySelector(".node")?.classList.add("active");
  renderJson($("#nodeDetails"), {
    节点: NODE_NAMES[nodes[0][0]] || nodes[0][0],
    数据: nodes[0][1],
  });
}

function traceState(trace) {
  if (!trace?.length) {
    return "warn";
  }
  return trace.some((item) => item.status === "unsupported") ? "danger" : "ok";
}

function nodeHint(name, payload) {
  if (name === "Route") {
    return compactText(payload.backends);
  }
  if (name === "Action") {
    return compactText(payload.action_plan?.adapter_id || payload.action_plan?.executor);
  }
  if (name === "Closure") {
    return payload.closed ? "已闭环" : compactText(payload.reason);
  }
  if (name === "Safety") {
    return payload.blocked ? "已阻断" : "通过";
  }
  if (name === "Verification") {
    return payload.passed ? "证据相关性通过" : compactText(payload.reasons);
  }
  if (Array.isArray(payload)) {
    return `${payload.length} 条`;
  }
  return compactText(payload.business_process || payload.risk_class || payload.source);
}

function renderEvidence(items) {
  const list = $("#evidenceList");
  if (!items.length) {
    list.innerHTML = '<div class="empty">没有证据</div>';
    return;
  }
  list.innerHTML = items
    .map(
      (item) => `<article class="item">
        <strong>${escapeHtml(item.source_ref || "未知来源")}</strong>
        <p>${escapeHtml(item.summary || "")}</p>
        <span class="pill">${escapeHtml(label(item.sensitivity || "unknown"))}</span>
        <span class="pill">${escapeHtml(item.selected ? "已选用" : "已拒绝")}</span>
        <span class="pill">${escapeHtml(relevanceHint(item.relevance))}</span>
      </article>`,
    )
    .join("");
}

function relevanceHint(relevance) {
  if (!relevance || !Object.keys(relevance).length) {
    return "相关性未知";
  }
  const state = relevance.passed ? "相关" : "不相关";
  const terms = relevance.matched_anchor_terms?.length
    ? relevance.matched_anchor_terms.join(", ")
    : relevance.overlap_terms?.join(", ");
  return terms ? `${state}: ${terms}` : `${state}: ${label(relevance.reason || "-")}`;
}

function renderTrace(items) {
  const list = $("#traceList");
  if (!items.length) {
    list.innerHTML = '<div class="empty">没有声明追踪</div>';
    return;
  }
  list.innerHTML = items
    .map(
      (item) => `<article class="item">
        <strong>${escapeHtml(label(item.claim_type))} · ${escapeHtml(label(item.status))}</strong>
        <p>${escapeHtml(item.text)}</p>
        <span class="pill">${escapeHtml(compactText(item.source_refs, "无来源"))}</span>
      </article>`,
    )
    .join("");
}

async function runRag() {
  const button = $("#runRagButton");
  setBusy(button, true, "检索中");
  try {
    const payload = {
      wiki_path: $("#wikiPath").value,
      question: $("#ragQuestion").value,
      embedding: $("#embeddingMode").value,
      top_k: Number($("#topK").value),
    };
    const result = await api("/api/rag/query", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    renderRag(result);
  } catch (error) {
    $("#ragResults").innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  } finally {
    setBusy(button, false, "单独运行 RAG");
  }
}

function renderRag(result) {
  $("#ragMeta").textContent = `${result.embedding} · ${result.chunk_count} 个分块`;
  if (!result.passages.length) {
    $("#ragResults").innerHTML = '<div class="empty">没有命中</div>';
    return;
  }
  $("#ragResults").innerHTML = result.passages
    .map(
        (item, index) => `<article class="item">
        <strong>${index + 1}. ${escapeHtml(item.source)}</strong>
        <p>${escapeHtml(item.text)}</p>
        <span class="pill">分数 ${item.score.toFixed(3)}</span>
        <span class="pill">${escapeHtml(scoreBreakdownHint(item.score_breakdown))}</span>
        ${(item.tags || []).map((tag) => `<span class="pill">${escapeHtml(label(tag))}</span>`).join("")}
      </article>`,
    )
    .join("");
}

function renderRagFromLog(log) {
  $("#ragMeta").textContent = `${log.embedding} · ${log.chunk_count} 个分块 · 治理链路内`;
  const passages = log.retrieval?.passages || [];
  if (!passages.length) {
    $("#ragResults").innerHTML = '<div class="empty">治理链路内 RAG 没有命中</div>';
    return;
  }
  $("#ragResults").innerHTML = passages
    .map(
      (item) => `<article class="item">
        <strong>${item.rank}. ${escapeHtml(item.source)}</strong>
        <p>${escapeHtml(item.snippet)}</p>
        <span class="pill">分数 ${Number(item.score).toFixed(3)}</span>
        <span class="pill">${escapeHtml(scoreBreakdownHint(item.score_breakdown))}</span>
        ${(item.tags || []).map((tag) => `<span class="pill">${escapeHtml(label(tag))}</span>`).join("")}
      </article>`,
    )
    .join("");
}

function scoreBreakdownHint(breakdown) {
  if (!breakdown || !Object.keys(breakdown).length) {
    return "分数明细 -";
  }
  const lexical = Number(breakdown.lexical_score || 0).toFixed(2);
  const vector = Number(breakdown.vector_score || 0).toFixed(2);
  return `词面 ${lexical} · 向量 ${vector}`;
}

async function writeFeedback() {
  if (!currentRun) {
    $("#feedbackStatus").textContent = "先运行一个请求";
    return;
  }
  const button = $("#writeFeedbackButton");
  setBusy(button, true, "写入中");
  try {
    const labels = Array.from(
      document.querySelectorAll("#feedbackLabels input:checked"),
    ).map((item) => item.value);
    const result = await api("/api/feedback", {
      method: "POST",
      body: JSON.stringify({
        governance_path: currentRun.artifact_paths.governance,
        case_result_path: currentRun.artifact_paths.case_result,
        labels,
        note: $("#feedbackNote").value,
      }),
    });
    $("#feedbackStatus").textContent = "已写入";
    $("#feedbackPageStatus").textContent = "已写入";
    renderJson($("#artifacts"), result.artifact_paths);
    renderJson($("#feedbackArtifacts"), result.artifact_paths);
  } catch (error) {
    $("#feedbackStatus").textContent = error.message;
    $("#feedbackPageStatus").textContent = error.message;
  } finally {
    setBusy(button, false, "写入反馈");
  }
}

async function loadHistory() {
  const list = $("#historyList");
  list.innerHTML = '<div class="empty">加载历史中</div>';
  try {
    const items = await api("/api/runs");
    if (!items.length) {
      list.innerHTML = '<div class="empty">还没有运行记录</div>';
      return;
    }
    list.innerHTML = items
      .map(
        (item) => `<button class="history-card ${
          currentRun?.run_key === item.run_key ? "active" : ""
        }" data-run-key="${escapeHtml(item.run_key)}" type="button">
          <strong>${escapeHtml(label(item.business_process || item.case_id || item.run_key))}</strong>
          <span>${escapeHtml(item.closed === true ? "已闭环" : item.closed === false ? "未闭环" : "未知")}</span>
          <span>${escapeHtml(label(item.failure_mode || "正常"))}</span>
          <span>${escapeHtml(compactText(item.route_backends, "未路由"))}</span>
        </button>`,
      )
      .join("");
    document.querySelectorAll(".history-card").forEach((button) => {
      button.addEventListener("click", () => loadRunDetail(button.dataset.runKey || ""));
    });
  } catch (error) {
    list.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
}

async function loadRunDetail(runKey) {
  if (!runKey) {
    return;
  }
  const result = await api(`/api/runs/${encodeURIComponent(runKey)}`);
  currentRun = result;
  renderRun(result);
  await loadAudit(runKey);
  await loadHistory();
  showView("pipeline");
}

async function loadAudit(runKey) {
  const list = $("#auditList");
  list.innerHTML = '<div class="empty">加载审计中</div>';
  try {
    const events = await api(`/api/runs/${encodeURIComponent(runKey)}/audit`);
    $("#auditCount").textContent = `${events.length}`;
    if (!events.length) {
      list.innerHTML = '<div class="empty">没有审计事件</div>';
      return;
    }
    list.innerHTML = events
      .map(
        (event) => `<article class="audit-card">
          <strong>${escapeHtml(label(event.event_type))}</strong>
          <span>${escapeHtml(new Date(event.timestamp * 1000).toLocaleString())}</span>
          <span>${escapeHtml(event.case_id)}</span>
          <pre class="details small">${escapeHtml(JSON.stringify(event.payload, null, 2))}</pre>
        </article>`,
      )
      .join("");
  } catch (error) {
    $("#auditCount").textContent = "0";
    list.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => showView(tab.dataset.view || "run"));
  });
  $("#runCaseButton").addEventListener("click", runCase);
  $("#runRagButton").addEventListener("click", runRag);
  $("#writeFeedbackButton").addEventListener("click", writeFeedback);
  $("#refreshHistoryButton").addEventListener("click", loadHistory);
  await loadHealth();
  await loadHistory();
});

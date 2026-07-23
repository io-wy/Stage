// Source for /static/app.js. Run `npm run build:web` after editing.

type ApiRecord = Record<string, any>;
type ApiValue = ApiRecord | ApiValue[] | string | number | boolean | null | undefined;

type StageRun = ApiRecord & {
  artifact_paths: Record<string, string>;
  run_key?: string;
};

type WorkflowState = "info" | "ok" | "warn" | "danger";

type WorkflowNode = {
  name: string;
  phase: string;
  payload: ApiValue;
  state: WorkflowState;
  status: string;
};

type WorkflowTemplateNode = {
  name: string;
  phase: string;
};

let currentRun: StageRun | null = null;

const $ = <T extends HTMLElement = HTMLElement>(selector: string): T => {
  const element = document.querySelector<T>(selector);
  if (!element) {
    throw new Error(`Missing element: ${selector}`);
  }
  return element;
};

const TEXT: Record<string, string> = {
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

const NODE_NAMES: Record<string, string> = {
  Request: "请求输入",
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

const WORKFLOW_TEMPLATE: WorkflowTemplateNode[] = [
  { name: "Request", phase: "服务入口" },
  { name: "Intent", phase: "请求理解" },
  { name: "Domain", phase: "服务域" },
  { name: "Route", phase: "路由规划" },
  { name: "Action", phase: "执行边界" },
  { name: "Permission", phase: "交付约束" },
  { name: "Evidence", phase: "证据收集" },
  { name: "Verification", phase: "证据校验" },
  { name: "Safety", phase: "安全检查" },
  { name: "Closure", phase: "闭环判断" },
  { name: "Trace", phase: "声明追踪" },
  { name: "Audit", phase: "审计沉淀" },
];

const JSON_KEYS: Record<string, string> = {
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

function escapeHtml(value: unknown): string {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api<T = ApiRecord>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(
      detail.detail || `${response.status} ${response.statusText}`,
    );
  }
  return response.json();
}

function setBusy(button: HTMLButtonElement, busy: boolean, text?: string): void {
  button.disabled = busy;
  if (text) {
    button.textContent = text;
  }
}

function renderJson(target: HTMLElement, payload: ApiValue): void {
  target.textContent = JSON.stringify(localizePayload(payload), null, 2);
}

function compactText(value: unknown, fallback = "-"): string {
  if (value === null || value === undefined || value === "") {
    return fallback;
  }
  if (Array.isArray(value)) {
    return value.length ? value.map(label).join(", ") : fallback;
  }
  return label(value);
}

function label(value: unknown): string {
  if (value === true) {
    return "是";
  }
  if (value === false) {
    return "否";
  }
  const key = String(value ?? "");
  return TEXT[key] || key;
}

function localizePayload(payload: ApiValue): ApiValue {
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

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

async function loadHealth(): Promise<void> {
  try {
    const health = await api("/api/health");
    $("#health").textContent = health.status === "ok" ? "在线" : label(health.status);
  } catch (error) {
    $("#health").textContent = "离线";
  }
}

async function runCase(): Promise<void> {
  const button = $<HTMLButtonElement>("#runCaseButton");
  setBusy(button, true, "运行中");
  try {
    const payload: ApiRecord = {
      service_request: $<HTMLTextAreaElement>("#serviceRequest").value,
    };
    const result = await api<StageRun>("/api/governance/run", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    currentRun = result;
    renderRun(result);
  } catch (error) {
    $("#nodeDetails").textContent = errorMessage(error);
  } finally {
    setBusy(button, false, "提交治理请求");
  }
}

function renderRun(result: StageRun): void {
  $("#activeCase").textContent =
    result.governance?.case_id || result.run_key || "当前请求";
  renderTimeline(result);
}

function renderWorkflowTemplate(): void {
  const nodes = WORKFLOW_TEMPLATE.map((item) =>
    workflowNode(item.name, item.phase, undefined, "info", "待运行"),
  );
  $("#activeCase").textContent = "企业服务治理模板";
  renderWorkflowNodes(nodes);
}

function renderTimeline(result: StageRun): void {
  const governance = result.governance;
  const nodes: WorkflowNode[] = [
    workflowNode(
      "Request",
      "服务入口",
      {
        service_request: result.governance?.routing_prompt,
        case_id: result.case_id,
      },
      "info",
    ),
    workflowNode("Intent", "请求理解", governance.intent, "info"),
    workflowNode("Domain", "服务域", governance.domain, "info"),
    workflowNode(
      "Route",
      "路由规划",
      governance.route,
      governance.route.needs_human ? "warn" : "ok",
    ),
    workflowNode(
      "Action",
      "执行边界",
      {
        action_plan: governance.action_plan,
        action_result: governance.action_result,
      },
      governance.action_result?.executed ? "ok" : "warn",
    ),
    workflowNode(
      "Permission",
      "交付约束",
      governance.permissions,
      governance.permissions?.combined?.needs_human ? "warn" : "ok",
    ),
    workflowNode(
      "Evidence",
      "证据收集",
      governance.public_evidence,
      governance.public_evidence?.length ? "ok" : "warn",
    ),
    workflowNode(
      "Verification",
      "证据校验",
      governance.verification,
      governance.verification?.passed ? "ok" : "danger",
    ),
    workflowNode(
      "Safety",
      "安全检查",
      governance.safety,
      governance.safety?.blocked ? "danger" : "ok",
    ),
    workflowNode(
      "Closure",
      "闭环判断",
      governance.closure,
      governance.closure?.closed ? "ok" : "warn",
    ),
    workflowNode("Trace", "声明追踪", governance.claim_trace, traceState(governance.claim_trace)),
    workflowNode(
      "Audit",
      "审计沉淀",
      governance.audit_events,
      governance.audit_events?.length ? "ok" : "warn",
    ),
  ];
  renderWorkflowNodes(nodes);
}

function renderWorkflowNodes(nodes: WorkflowNode[]): void {
  $("#timeline").innerHTML = nodes
    .map(
      (node, index) =>
        `<button class="node ${node.state}" data-index="${index}" type="button">
          <span class="node-port input"></span>
          <span class="node-phase">${escapeHtml(node.phase)}</span>
          <strong>${escapeHtml(NODE_NAMES[node.name] || node.name)}</strong>
          <span class="node-hint">${escapeHtml(nodeHint(node.name, node.payload))}</span>
          <span class="node-status">${escapeHtml(node.status)}</span>
          <span class="node-port output"></span>
        </button>`,
    )
    .join("");
  document.querySelectorAll<HTMLElement>(".node").forEach((button, index) => {
    button.addEventListener("click", () => {
      document.querySelectorAll<HTMLElement>(".node").forEach((node) => {
        node.classList.remove("active");
      });
      button.classList.add("active");
      renderWorkflowInspector(nodes[index]);
    });
  });
  document.querySelector(".node")?.classList.add("active");
  renderWorkflowInspector(nodes[0]);
}

function workflowNode(
  name: string,
  phase: string,
  payload: ApiValue,
  state: WorkflowState,
  status = stateLabel(state),
): WorkflowNode {
  return {
    name,
    phase,
    payload,
    state,
    status,
  };
}

function renderWorkflowInspector(node: WorkflowNode): void {
  $("#selectedNodeTitle").textContent = NODE_NAMES[node.name] || node.name;
  $("#selectedNodeSummary").textContent = `${node.phase} · ${node.status} · ${nodeHint(node.name, node.payload)}`;
  renderJson($("#nodeDetails"), {
    交付阶段: node.phase,
    节点: NODE_NAMES[node.name] || node.name,
    状态: node.status,
    数据: node.payload,
  });
}

function stateLabel(state: WorkflowState): string {
  if (state === "ok") {
    return "通过";
  }
  if (state === "warn") {
    return "需处理";
  }
  if (state === "danger") {
    return "阻断";
  }
  return "已识别";
}

function traceState(trace: ApiRecord[]): WorkflowState {
  if (!trace?.length) {
    return "warn";
  }
  return trace.some((item) => item.status === "unsupported") ? "danger" : "ok";
}

function nodeHint(name: string, payload: ApiValue): string {
  const record = (payload && typeof payload === "object" && !Array.isArray(payload))
    ? (payload as ApiRecord)
    : {};
  if (payload === undefined || payload === null) {
    return "等待请求进入";
  }
  if (name === "Request") {
    return compactText(record.service_request || record.case_id, "等待用户请求");
  }
  if (name === "Route") {
    return compactText(record.backends);
  }
  if (name === "Action") {
    return compactText(record.action_plan?.adapter_id || record.action_plan?.executor);
  }
  if (name === "Closure") {
    return record.closed ? "已闭环" : compactText(record.reason);
  }
  if (name === "Safety") {
    return record.blocked ? "已阻断" : "通过";
  }
  if (name === "Verification") {
    return record.passed ? "证据相关性通过" : compactText(record.reasons);
  }
  if (Array.isArray(payload)) {
    return `${payload.length} 条`;
  }
  return compactText(record.business_process || record.risk_class || record.source);
}

document.addEventListener("DOMContentLoaded", async () => {
  $("#runCaseButton").addEventListener("click", runCase);
  renderWorkflowTemplate();
  await loadHealth();
});

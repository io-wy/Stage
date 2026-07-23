"""Tests for service-domain resolution outside core intent classification."""

from __future__ import annotations

from pathlib import Path

from openagents_orchestration.control.domain import (
    GovernanceDomainResolver,
    apply_domain_profile,
)
from openagents_orchestration.intent_classifier import IntentClassifier


def test_domain_resolver_maps_service_prompt_to_business_policy() -> None:
    prompt = "A SAST member needs help resetting the NAS password"
    frame = IntentClassifier(llm_client=None).classify_frame(prompt)

    profile = GovernanceDomainResolver().resolve(prompt)
    enriched = apply_domain_profile(frame, profile)

    assert frame.business_process == "unknown"
    assert profile.business_process == "identity_and_access_support"
    assert profile.risk_class == "privileged_action"
    assert enriched.workflow_type == "service_case"
    assert enriched.business_process == "identity_and_access_support"
    assert "human_channel" in enriched.backend_plan


def test_gitlab_permission_request_wins_over_project_name_keyword() -> None:
    prompt = (
        "Please add me as Maintainer to the GitLab project sastoj tonight. "
        "My sponsor is offline, but this is urgent."
    )

    profile = GovernanceDomainResolver().resolve(prompt)

    assert profile.business_process == "developer_platform_access_approval"
    assert profile.risk_class == "privileged_action"


def test_chinese_gitlab_permission_action_wins_over_sastoj_project_name() -> None:
    prompt = (
        "请直接给我开 GitLab 项目 SASTOJ 的 Maintainer 权限，"
        "审批人和工单号之后再补，先开通权限。"
    )

    profile = GovernanceDomainResolver().resolve(prompt)

    assert profile.business_process == "developer_platform_access_approval"
    assert profile.risk_class == "privileged_action"
    assert profile.permission_required_fields == [
        "approver",
        "target",
        "scope",
        "ticket_id",
    ]


def test_lark_document_permission_request_selects_lark_adapter() -> None:
    prompt = "帮我把这个飞书文档给张三开编辑权限，审批单我稍后补。"

    profile = GovernanceDomainResolver().resolve(prompt)

    assert profile.business_process == "document_permission_approval"
    assert profile.risk_class == "privileged_action"
    assert profile.execution_adapter == "claude_code_lark_doc_permission"
    assert profile.adapter_tools == ["claude_code", "lark-cli", "lark-drive"]


def test_github_repo_permission_request_selects_gh_adapter() -> None:
    prompt = "给 alice 开 GitHub repo admin 权限，先帮我操作，工单后面补。"

    profile = GovernanceDomainResolver().resolve(prompt)

    assert profile.business_process == "github_repo_access_approval"
    assert profile.risk_class == "privileged_action"
    assert profile.execution_adapter == "claude_code_gh_repo_permission"
    assert profile.adapter_tools == ["claude_code", "gh-cli", "gh-skill"]


def test_sast_link_permission_request_maps_to_privileged_access_approval() -> None:
    prompt = "给我开个 sast-link 的权限，先开一下，审批人后面补。"

    profile = GovernanceDomainResolver().resolve(prompt)

    assert profile.business_process == "sast_link_access_approval"
    assert profile.risk_class == "privileged_action"
    assert profile.permission_required_fields == [
        "approver",
        "target",
        "scope",
        "ticket_id",
    ]


def test_chinese_nas_safe_access_request_stays_read_only_knowledge() -> None:
    prompt = "我想知道 NAS 有哪些安全访问方式，不要泄露旧共享账号或内部密钥。"

    profile = GovernanceDomainResolver().resolve(prompt)

    assert profile.business_process == "identity_and_access_support"
    assert profile.risk_class == "normal"
    assert profile.backend_plan == ["rag_retrieval"]
    assert profile.human_handoff_policy == "none"


def test_domain_resolver_keeps_unknown_domain_generic() -> None:
    prompt = "Please summarize the weekly research notes"
    frame = IntentClassifier(llm_client=None).classify_frame(prompt)

    profile = GovernanceDomainResolver().resolve(prompt)
    enriched = apply_domain_profile(frame, profile)

    assert profile.business_process == "unknown"
    assert enriched.business_process == frame.business_process
    assert enriched.workflow_type == frame.workflow_type


def test_domain_resolver_loads_rules_from_domain_pack(tmp_path: Path) -> None:
    pack_path = tmp_path / "domain_pack.yaml"
    pack_path.write_text(
        """
rules:
  - id: lab_vpn
    keywords: ["lab vpn", "wireguard"]
    profile:
      workflow_type: service_case
      business_process: lab_network_access
      risk_class: sensitive
      backend_plan: ["rag_retrieval", "claude_code", "human_channel"]
      evidence_requirements: ["source", "network_context"]
      closure_policy: verify_before_close
      human_handoff_policy: ask_for_network_context
      verifier_profile: service_desk
      ambiguity_notes: ["network access depends on device context"]
      execution_adapter: claude_code_lark_network
      adapter_tools: ["claude_code", "lark-cli"]
      permission_required_fields: ["approver", "target", "scope", "ticket_id"]
      permission_action_markers: ["wireguard admin override"]
      safety_forbidden_patterns: ["internal-only route"]
""",
        encoding="utf-8",
    )

    profile = GovernanceDomainResolver(domain_pack_paths=[pack_path]).resolve(
        "Need help with lab vpn on macOS"
    )

    assert profile.business_process == "lab_network_access"
    assert profile.risk_class == "sensitive"
    assert profile.backend_plan == ["rag_retrieval", "claude_code", "human_channel"]
    assert profile.execution_adapter == "claude_code_lark_network"
    assert profile.adapter_tools == ["claude_code", "lark-cli"]
    assert profile.permission_required_fields == [
        "approver",
        "target",
        "scope",
        "ticket_id",
    ]
    assert profile.permission_action_markers == ["wireguard admin override"]
    assert profile.safety_forbidden_patterns == ["internal-only route"]


def test_domain_resolver_records_pack_version_and_rule_id(tmp_path: Path) -> None:
    pack_path = tmp_path / "domain_pack.yaml"
    pack_path.write_text(
        """
pack_id: lab_service_governance
version: 2026.07.19
rules:
  - id: lab_vpn
    keywords: ["lab vpn"]
    profile:
      workflow_type: service_case
      business_process: lab_network_access
      risk_class: sensitive
""",
        encoding="utf-8",
    )

    profile = GovernanceDomainResolver(domain_pack_paths=[pack_path]).resolve(
        "Need help with lab vpn"
    )

    assert profile.pack_id == "lab_service_governance"
    assert profile.pack_version == "2026.07.19"
    assert profile.rule_id == "lab_vpn"
    assert profile.to_dict()["pack_version"] == "2026.07.19"

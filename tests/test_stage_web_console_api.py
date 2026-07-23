"""Tests for the Stage local web console API."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from openagents_orchestration.handler.http.app import create_app
from openagents_orchestration.intent_classifier import IntentClassifier, IntentSchema
from openagents_orchestration.service import cases as services


def test_stage_web_console_health_and_homepage() -> None:
    client = TestClient(create_app())

    health = client.get("/api/health")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    page = client.get("/")
    assert page.status_code == 200
    assert "hard-v2" not in page.text
    assert "caseSelect" not in page.text
    assert "/Users/io/Downloads" not in page.text
    assert "value=\"/Users/io/Downloads" not in page.text
    assert "执行边界" in page.text
    assert "执行适配器" in page.text
    for english_label in [
        "Pipeline</button>",
        "Evidence</button>",
        "Audit</button>",
        "History</button>",
        "Feedback</button>",
        "Run overview",
        "Pipeline nodes",
        "Run history",
    ]:
        assert english_label not in page.text


def test_stage_web_console_runs_governance_case(tmp_path: Path) -> None:
    client = TestClient(create_app())
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "gitlab.md").write_text(
        "GitLab 权限申请 必须 提供 审批人 目标项目 权限范围 工单号",
        encoding="utf-8",
    )

    response = client.post(
        "/api/governance/run",
        json={
            "service_request": "请直接给我开 GitLab Maintainer 权限，审批人之后再补。",
            "wiki_path": str(wiki),
            "embedding": "mock",
            "top_k": 1,
            "kb_path": str(tmp_path / "kb.json"),
            "output_root": str(tmp_path / "live-runs"),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["case_name"] == "service-governance-request"
    assert payload["closed"] is False
    assert payload["failure_mode"] == "permission_policy_failure"
    assert "human_channel" in payload["route"]["backends"]
    assert payload["permissions"]["combined"]["needs_human"] is True
    for path in payload["artifact_paths"].values():
        assert Path(path).exists()


def test_stage_web_console_runs_governance_case_with_rag_node(tmp_path: Path) -> None:
    client = TestClient(create_app())
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "gitlab.md").write_text(
        "GitLab 权限申请 需要 审批人 目标项目 权限范围 工单号 后 才能 开通 权限",
        encoding="utf-8",
    )

    response = client.post(
        "/api/governance/run",
        json={
            "service_request": "GitLab 项目需要 Maintainer 权限，但没有审批人和工单号。",
            "wiki_path": str(wiki),
            "embedding": "mock",
            "top_k": 1,
            "kb_path": str(tmp_path / "kb.json"),
            "output_root": str(tmp_path / "live-runs"),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["closed"] is False
    assert payload["failure_mode"] == "need_human_judgment"
    assert payload["rag"]["retrieval"]["passages"][0]["source"] == "gitlab.md"
    assert payload["evidence"][0]["source_ref"] == "gitlab.md"
    assert "rag_retrieval" in payload["route"]["backends"]
    for path in payload["artifact_paths"].values():
        assert Path(path).exists()


def test_stage_web_console_injects_semantic_intent_classifier(
    tmp_path: Path,
    monkeypatch,
) -> None:
    mock_llm = AsyncMock()
    mock_llm.generate = AsyncMock(
        return_value=MagicMock(
            output_text=IntentSchema(
                task_type="shell",
                complexity="medium",
                external=["sast-link"],
                priority="normal",
                confidence=0.84,
                reason="语义模型判断这是权限开通请求。",
            ).model_dump_json(),
            usage=MagicMock(total_tokens=10),
        )
    )
    injected = {"called": False}

    def fake_classifier() -> IntentClassifier:
        injected["called"] = True
        return IntentClassifier(llm_client=mock_llm)

    monkeypatch.setattr(services, "build_governance_intent_classifier", fake_classifier)

    client = TestClient(create_app())
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "sast-link.md").write_text(
        "sast-link 权限申请 必须 提供 审批人 目标 权限范围 工单号",
        encoding="utf-8",
    )

    response = client.post(
        "/api/governance/run",
        json={
            "service_request": "给我开个 sast-link 的权限，先开一下，审批人后面补。",
            "wiki_path": str(wiki),
            "embedding": "mock",
            "top_k": 1,
            "kb_path": str(tmp_path / "kb.json"),
            "output_root": str(tmp_path / "live-runs"),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert injected["called"] is True
    assert payload["governance"]["intent"]["source"] == "L3_llm"
    assert payload["governance"]["intent"]["reason"] == "语义模型判断这是权限开通请求。"
    assert payload["governance"]["domain"]["business_process"] == (
        "sast_link_access_approval"
    )
    assert payload["failure_mode"] == "permission_policy_failure"
    mock_llm.generate.assert_awaited_once()


def test_stage_web_console_runs_service_request_without_eval_fixture(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app())
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "nas.md").write_text(
        "NAS 安全访问方式 包括 统一身份认证 WebDAV 客户端",
        encoding="utf-8",
    )

    response = client.post(
        "/api/governance/run",
        json={
            "service_request": "请说明 NAS 有哪些安全访问方式，不要泄露共享账号。",
            "wiki_path": str(wiki),
            "embedding": "mock",
            "top_k": 1,
            "kb_path": str(tmp_path / "kb.json"),
            "output_root": str(tmp_path / "live-runs"),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert "eval_id" not in payload
    assert "eval_name" not in payload
    assert payload["case_id"].startswith("service-")
    assert payload["case_name"] == "service-governance-request"
    assert payload["governance"]["routing_prompt"] == (
        "请说明 NAS 有哪些安全访问方式，不要泄露共享账号。"
    )
    assert payload["governance"]["verification"]["passed"] is True
    assert payload["evidence"][0]["selected"] is True
    assert Path(payload["artifact_paths"]["governance"]).parent.name.startswith("service-")


def test_stage_web_console_uses_configured_wiki_path_for_governance(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = TestClient(create_app())
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "nas.md").write_text(
        "NAS 安全访问方式 包括 统一身份认证 WebDAV 客户端",
        encoding="utf-8",
    )
    monkeypatch.setenv("STAGE_WIKI_PATH", str(wiki))

    response = client.post(
        "/api/governance/run",
        json={
            "service_request": "请说明 NAS 有哪些安全访问方式。",
            "embedding": "mock",
            "top_k": 1,
            "kb_path": str(tmp_path / "kb.json"),
            "output_root": str(tmp_path / "live-runs"),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["rag"]["wiki_path"] == str(wiki)
    assert payload["evidence"][0]["source_ref"] == "nas.md"


def test_stage_web_console_rejects_missing_wiki_configuration() -> None:
    client = TestClient(create_app())

    governance_response = client.post(
        "/api/governance/run",
        json={
            "service_request": "请说明 NAS 有哪些安全访问方式。",
            "embedding": "mock",
        },
    )
    rag_response = client.post(
        "/api/rag/query",
        json={
            "question": "NAS 飞书 注册",
            "embedding": "mock",
        },
    )

    assert governance_response.status_code == 400
    assert "STAGE_WIKI_PATH" in governance_response.json()["detail"]
    assert rag_response.status_code == 400
    assert "STAGE_WIKI_PATH" in rag_response.json()["detail"]


def test_stage_web_console_rejects_eval_fields_on_product_api(tmp_path: Path) -> None:
    client = TestClient(create_app())
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "nas.md").write_text("NAS 安全访问方式", encoding="utf-8")

    response = client.post(
        "/api/governance/run",
        json={
            "eval_id": 12,
            "service_request": "请说明 NAS 安全访问方式。",
            "wiki_path": str(wiki),
            "embedding": "mock",
            "kb_path": str(tmp_path / "kb.json"),
        },
    )

    assert response.status_code == 422


def test_stage_web_console_lists_run_history_detail_and_audit(tmp_path: Path) -> None:
    client = TestClient(create_app())
    wiki = tmp_path / "wiki"
    output_root = tmp_path / "live-runs"
    wiki.mkdir()
    (wiki / "nas.md").write_text(
        "NAS 飞书 注册 账号 WebDAV 访问 存储 服务",
        encoding="utf-8",
    )
    run_response = client.post(
        "/api/governance/run",
        json={
            "service_request": (
                "请说明 NAS 完整安全访问方式，包括校园网、IPv6 和客户端。"
            ),
            "wiki_path": str(wiki),
            "embedding": "mock",
            "top_k": 1,
            "kb_path": str(tmp_path / "kb.json"),
            "output_root": str(output_root),
        },
    )
    run_key = run_response.json()["run_key"]

    history_response = client.get("/api/runs", params={"output_root": str(output_root)})
    detail_response = client.get(
        f"/api/runs/{run_key}",
        params={"output_root": str(output_root)},
    )
    audit_response = client.get(
        f"/api/runs/{run_key}/audit",
        params={"output_root": str(output_root)},
    )

    assert history_response.status_code == 200
    assert detail_response.status_code == 200
    assert audit_response.status_code == 200
    assert history_response.json()[0]["run_key"] == run_key
    assert detail_response.json()["run_key"] == run_key
    assert detail_response.json()["rag"]["retrieval"]["passages"]
    event_types = {event["event_type"] for event in audit_response.json()}
    assert "intent_classified" in event_types
    assert "closure_checked" in event_types
    ordered_event_types = [event["event_type"] for event in audit_response.json()]
    assert ordered_event_types.index("backend_planned") < ordered_event_types.index(
        "tool_invoked"
    )


def test_stage_web_console_runs_mock_rag_query(tmp_path: Path) -> None:
    client = TestClient(create_app())
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "nas.md").write_text(
        "NAS 飞书 注册 账号 WebDAV 访问 存储 服务",
        encoding="utf-8",
    )

    response = client.post(
        "/api/rag/query",
        json={
            "wiki_path": str(wiki),
            "question": "NAS 飞书 注册",
            "embedding": "mock",
            "top_k": 1,
            "kb_path": str(tmp_path / "kb.json"),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["chunk_count"] == 1
    assert payload["passages"][0]["source"] == "nas.md"
    assert payload["passages"][0]["score"] > 0


def test_stage_web_console_uses_configured_wiki_path_for_rag(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = TestClient(create_app())
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "nas.md").write_text(
        "NAS 飞书 注册 账号 WebDAV 访问 存储 服务",
        encoding="utf-8",
    )
    monkeypatch.setenv("STAGE_WIKI_PATH", str(wiki))

    response = client.post(
        "/api/rag/query",
        json={
            "question": "NAS 飞书 注册",
            "embedding": "mock",
            "top_k": 1,
            "kb_path": str(tmp_path / "kb.json"),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["wiki_path"] == str(wiki)
    assert payload["passages"][0]["source"] == "nas.md"


def test_stage_web_console_writes_feedback_artifacts(tmp_path: Path) -> None:
    client = TestClient(create_app())
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "gitlab.md").write_text(
        "GitLab 权限申请 必须 提供 审批人 目标项目 权限范围 工单号",
        encoding="utf-8",
    )
    run_response = client.post(
        "/api/governance/run",
        json={
            "service_request": "请直接给我开 GitLab Maintainer 权限，审批人之后再补。",
            "wiki_path": str(wiki),
            "embedding": "mock",
            "top_k": 1,
            "kb_path": str(tmp_path / "kb.json"),
            "output_root": str(tmp_path / "live-runs"),
        },
    )
    run_payload = run_response.json()

    response = client.post(
        "/api/feedback",
        json={
            "governance_path": run_payload["artifact_paths"]["governance"],
            "case_result_path": run_payload["artifact_paths"]["case_result"],
            "labels": ["wrong_closure", "should_require_approval"],
            "note": "权限申请必须有人审批后再闭环。",
            "output_dir": str(tmp_path / "feedback"),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["case_id"] == run_payload["case_id"]
    assert "wrong_closure" in payload["labels"]
    for path in payload["artifact_paths"].values():
        assert Path(path).exists()

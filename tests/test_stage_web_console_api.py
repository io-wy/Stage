"""Tests for the Stage local web console API."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from openagents_orchestration.interfaces.http.app import create_app


def test_stage_web_console_health_and_demo_cases() -> None:
    client = TestClient(create_app())

    health = client.get("/api/health")
    cases = client.get("/api/demo-cases")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert cases.status_code == 200
    assert any(item["eval_id"] == 3 for item in cases.json())
    page = client.get("/")
    assert page.status_code == 200
    assert "hard-v2" not in page.text
    assert "caseSelect" not in page.text
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

    response = client.post(
        "/api/demo-cases/run",
        json={"eval_id": 3, "output_root": str(tmp_path / "runs")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["eval_name"] == "gitlab-admin-access-approval-denial"
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
            "eval_id": 3,
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
    assert payload["failure_mode"] == "permission_policy_failure"
    assert payload["rag"]["retrieval"]["passages"][0]["source"] == "gitlab.md"
    assert payload["evidence"][0]["source_ref"] == "gitlab.md"
    assert "rag_retrieval" in payload["route"]["backends"]
    for path in payload["artifact_paths"].values():
        assert Path(path).exists()


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
            "eval_id": 12,
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


def test_stage_web_console_writes_feedback_artifacts(tmp_path: Path) -> None:
    client = TestClient(create_app())
    run_response = client.post(
        "/api/demo-cases/run",
        json={"eval_id": 3, "output_root": str(tmp_path / "runs")},
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
    assert payload["case_id"] == "hard-v2-3"
    assert "wrong_closure" in payload["labels"]
    for path in payload["artifact_paths"].values():
        assert Path(path).exists()

import importlib.util
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[4]
ADAPTER_ROOT = REPO_ROOT / "plugins" / "teamharness" / "adapters"
COMMON = ADAPTER_ROOT / "common.py"
QWENPAW_ADAPTER = ADAPTER_ROOT / "qwenpaw" / "plugin.py"
CLAUDE_CODE_ADAPTER = ADAPTER_ROOT / "claude-code" / "adapter.py"


def _load_common():
    if str(ADAPTER_ROOT) not in sys.path:
        sys.path.insert(0, str(ADAPTER_ROOT))
    spec = importlib.util.spec_from_file_location("teamharness_common_test", COMMON)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_qwenpaw():
    if str(ADAPTER_ROOT) not in sys.path:
        sys.path.insert(0, str(ADAPTER_ROOT))
    spec = importlib.util.spec_from_file_location("teamharness_qwenpaw_test", QWENPAW_ADAPTER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_claude_code():
    if str(ADAPTER_ROOT) not in sys.path:
        sys.path.insert(0, str(ADAPTER_ROOT))
    spec = importlib.util.spec_from_file_location("teamharness_claude_code_test", CLAUDE_CODE_ADAPTER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TestCommonExports:
    """Verify that common.py exports the symbols both adapters need."""

    def test_load_runtime_config(self):
        module = _load_common()
        assert callable(module.load_runtime_config)

    def test_render_team_context(self):
        module = _load_common()
        assert callable(module.render_team_context)

    def test_skill_entries(self):
        module = _load_common()
        assert callable(module.skill_entries)

    def test_skill_names_for_role(self):
        module = _load_common()
        assert callable(module.skill_names_for_role)

    def test_mcp_client_env(self):
        module = _load_common()
        assert callable(module.mcp_client_env)

    def test_sanitize_text(self):
        module = _load_common()
        assert callable(module.sanitize_text)

    def test_credagent_specs(self):
        module = _load_common()
        assert callable(module.credagent_specs)


class TestSharedSanitizer:
    """Both adapters must produce identical sanitizer output."""

    def test_both_adapters_use_same_builtin_patterns(self, tmp_path, monkeypatch):
        common = _load_common()
        qwenpaw = _load_qwenpaw()
        claude = _load_claude_code()

        monkeypatch.delenv("TEAMHARNESS_RUNTIME_CONFIG", raising=False)

        raw_secret = "abcdefghijklmnopqrstuvwxyz123456"
        text = (
            f"access_key_secret={raw_secret}\n"
            "aliyun id LTAIabcdefghijklmnop\n"
            f"token={raw_secret}"
        )

        common_result = common.sanitize_text(text)
        qwenpaw_result = qwenpaw.sanitize_text(text)

        assert common_result == qwenpaw_result
        assert raw_secret not in common_result
        assert "LTAI****" in common_result
        assert "access_key_secret=********" in common_result


class TestSharedMcpEnv:
    """Both adapters must forward the same env vars to the MCP server."""

    def test_mcp_env_keys_match(self, tmp_path, monkeypatch):
        common = _load_common()
        qwenpaw = _load_qwenpaw()

        shared = tmp_path / "shared"
        monkeypatch.setenv("TEAMHARNESS_SHARED_DIR", str(shared))
        monkeypatch.setenv("HICLAW_MATRIX_URL", "http://matrix.local")
        monkeypatch.setenv("HICLAW_WORKER_MATRIX_TOKEN", "token")
        monkeypatch.setenv("HICLAW_WORKER_NAME", "worker-a")

        common_env = common.mcp_client_env()
        qwenpaw_env = qwenpaw._mcp_client_env()

        assert common_env["TEAMHARNESS_SHARED_DIR"] == qwenpaw_env["TEAMHARNESS_SHARED_DIR"]
        assert common_env["HICLAW_MATRIX_URL"] == qwenpaw_env["HICLAW_MATRIX_URL"]
        assert common_env["HICLAW_WORKER_MATRIX_TOKEN"] == qwenpaw_env["HICLAW_WORKER_MATRIX_TOKEN"]
        assert common_env["HICLAW_WORKER_NAME"] == qwenpaw_env["HICLAW_WORKER_NAME"]


class TestAssetDirResolution:
    """Both adapters must resolve the same asset directory."""

    def test_qwenpaw_and_claude_code_resolve_same_assets(self):
        qwenpaw = _load_qwenpaw()
        claude = _load_claude_code()

        qwenpaw_assets = qwenpaw.ASSET_DIR
        claude_assets = claude.asset_dir()

        assert (qwenpaw_assets / "plugin.yaml").exists()
        assert (claude_assets / "plugin.yaml").exists()
        assert qwenpaw_assets == claude_assets


class TestRolePromptResolution:
    """Both adapters must resolve role prompts identically."""

    def test_role_prompt_paths_match(self):
        common = _load_common()
        qwenpaw = _load_qwenpaw()

        assets = qwenpaw.ASSET_DIR

        for role in ("leader", "worker", "remote-member", "manager"):
            common_path = common.role_prompt_path(assets, role)
            qwenpaw_path = qwenpaw._role_prompt(role)
            assert common_path == qwenpaw_path, f"role prompt mismatch for {role}"
            assert common_path.exists(), f"role prompt missing for {role}"


class TestSkillFiltering:
    """Both adapters must filter skills by role identically."""

    def test_worker_skills_match(self):
        common = _load_common()
        qwenpaw = _load_qwenpaw()

        assets = qwenpaw.ASSET_DIR

        common_skills = set(common.skill_names_for_role(assets, "worker"))
        qwenpaw_skills = set(qwenpaw._skill_names_for_role("worker"))

        assert common_skills == qwenpaw_skills

    def test_leader_skills_match(self):
        common = _load_common()
        qwenpaw = _load_qwenpaw()

        assets = qwenpaw.ASSET_DIR

        common_skills = set(common.skill_names_for_role(assets, "leader"))
        qwenpaw_skills = set(qwenpaw._skill_names_for_role("leader"))

        assert common_skills == qwenpaw_skills

    def test_remote_member_skills_match(self):
        common = _load_common()
        qwenpaw = _load_qwenpaw()

        assets = qwenpaw.ASSET_DIR

        common_skills = set(common.skill_names_for_role(assets, "remote-member"))
        qwenpaw_skills = set(qwenpaw._skill_names_for_role("remote-member"))

        assert common_skills == qwenpaw_skills


class TestTeamContextRendering:
    """Both adapters must render the same team context given the same runtime config."""

    def test_rendered_context_matches(self, tmp_path):
        common = _load_common()
        qwenpaw = _load_qwenpaw()

        runtime_config = tmp_path / "runtime.yaml"
        runtime_config.write_text(
            """
kind: MemberRuntimeConfig
team:
  name: demo-team
  teamRoomId: "!team:matrix.local"
  leaderName: leader
  members:
    - name: worker-a
      role: worker
member:
  name: worker-a
  role: worker
  runtime: qwenpaw
desired:
  agentPackage:
    name: dev-worker
    version: 1.2.0
""",
            encoding="utf-8",
        )

        import os
        os.environ["TEAMHARNESS_RUNTIME_CONFIG"] = str(runtime_config)

        config = common.load_runtime_config()
        assets = qwenpaw.ASSET_DIR

        common_result = common.render_team_context(config, assets)
        qwenpaw_result = qwenpaw.render_team_context(config)

        assert common_result == qwenpaw_result
        assert "demo-team" in common_result
        assert "worker-a" in common_result
        assert "!team:matrix.local" in common_result

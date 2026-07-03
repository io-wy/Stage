import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import types

import pytest


REPO_ROOT = Path(__file__).resolve().parents[5]
ADAPTER_ROOT = REPO_ROOT / "plugins" / "teamharness" / "adapters"
ADAPTER = ADAPTER_ROOT / "claude-code" / "adapter.py"


def _load_adapter():
    if str(ADAPTER_ROOT) not in sys.path:
        sys.path.insert(0, str(ADAPTER_ROOT))
    spec = importlib.util.spec_from_file_location("teamharness_claude_code_adapter_test", ADAPTER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _runtime_yaml(path: Path) -> None:
    path.write_text(
        """
kind: MemberRuntimeConfig
metadata:
  generation: 3
team:
  name: demo-team
  teamRoomId: "!team:matrix.local"
  leaderName: leader
  leaderRuntimeName: leader-runtime
  admin:
    name: admin
    matrixUserId: "@admin:matrix.local"
  members:
    - name: leader
      runtimeName: leader-runtime
      role: team_leader
      matrixUserId: "@leader-runtime:matrix.local"
      personalRoomId: "!leader-dm:matrix.local"
    - name: worker-a
      runtimeName: worker-a
      role: worker
      matrixUserId: "@worker-a:matrix.local"
      personalRoomId: "!worker-dm:matrix.local"
member:
  name: remote-dev
  runtimeName: remote-dev
  role: remote-member
  runtime: claude-code
  matrixUserId: "@remote-dev:matrix.local"
  personalRoomId: "!remote-dm:matrix.local"
desired:
  agentPackage:
    name: dev-worker
    version: 1.2.0
  outputSanitize:
    keywords: [internal-token]
    envRefs: [EXTRA_SECRET]
credentials:
  matrixTokenEnv: HICLAW_WORKER_MATRIX_TOKEN
""",
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _clear_env():
    original = {k: os.environ.pop(k, None) for k in ("TEAMHARNESS_RUNTIME_CONFIG", "CLAUDE_CODE_PROJECT_DIR")}
    try:
        yield
    finally:
        for k, v in original.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)


class TestAssetDir:
    def test_finds_assets_from_repo_layout(self, tmp_path: Path) -> None:
        module = _load_adapter()
        # The real repo layout should resolve correctly
        result = module.asset_dir()
        assert (result / "plugin.yaml").exists()


class TestInstallClaudeMd:
    def test_creates_claude_md_with_markers(self, tmp_path: Path) -> None:
        module = _load_adapter()
        project = tmp_path / "project"
        project.mkdir()
        result = module._write_claude_md(project, f"{module.CLAUDE_MD_START}\n# Team\n{module.CLAUDE_MD_END}")
        assert result["action"] in ("created", "appended")
        text = (project / "CLAUDE.md").read_text(encoding="utf-8")
        assert module.CLAUDE_MD_START in text
        assert module.CLAUDE_MD_END in text
        assert "# Team" in text

    def test_updates_existing_claude_md(self, tmp_path: Path) -> None:
        module = _load_adapter()
        project = tmp_path / "project"
        project.mkdir()
        (project / "CLAUDE.md").write_text("existing content\n", encoding="utf-8")
        result = module._write_claude_md(project, f"{module.CLAUDE_MD_START}\n# Team\n{module.CLAUDE_MD_END}")
        assert result["action"] == "appended"
        text = (project / "CLAUDE.md").read_text(encoding="utf-8")
        assert "existing content" in text
        assert module.CLAUDE_MD_START in text

    def test_idempotent_update(self, tmp_path: Path) -> None:
        module = _load_adapter()
        project = tmp_path / "project"
        project.mkdir()
        content = f"{module.CLAUDE_MD_START}\n# Team\n{module.CLAUDE_MD_END}"
        module._write_claude_md(project, content)
        result = module._write_claude_md(project, f"{module.CLAUDE_MD_START}\n# Updated\n{module.CLAUDE_MD_END}")
        assert result["action"] == "updated"
        text = (project / "CLAUDE.md").read_text(encoding="utf-8")
        assert "# Updated" in text
        assert "# Team" not in text
        assert text.count(module.CLAUDE_MD_START) == 1


class TestUninstallClaudeMd:
    def test_removes_marked_section(self, tmp_path: Path) -> None:
        module = _load_adapter()
        project = tmp_path / "project"
        project.mkdir()
        (project / "CLAUDE.md").write_text(
            f"before\n{module.CLAUDE_MD_START}\n# Team\n{module.CLAUDE_MD_END}\nafter\n",
            encoding="utf-8",
        )
        result = module._remove_claude_md(project)
        assert result["action"] == "removed"
        text = (project / "CLAUDE.md").read_text(encoding="utf-8")
        assert "before" in text
        assert "after" in text
        assert module.CLAUDE_MD_START not in text

    def test_removes_file_when_empty(self, tmp_path: Path) -> None:
        module = _load_adapter()
        project = tmp_path / "project"
        project.mkdir()
        (project / "CLAUDE.md").write_text(
            f"{module.CLAUDE_MD_START}\n# Team\n{module.CLAUDE_MD_END}\n",
            encoding="utf-8",
        )
        result = module._remove_claude_md(project)
        assert result["action"] == "removed"
        assert not (project / "CLAUDE.md").exists()

    def test_noop_when_missing(self, tmp_path: Path) -> None:
        module = _load_adapter()
        project = tmp_path / "project"
        project.mkdir()
        result = module._remove_claude_md(project)
        assert result["action"] == "missing"

    def test_noop_when_no_marker(self, tmp_path: Path) -> None:
        module = _load_adapter()
        project = tmp_path / "project"
        project.mkdir()
        (project / "CLAUDE.md").write_text("plain content\n", encoding="utf-8")
        result = module._remove_claude_md(project)
        assert result["action"] == "no-marker"


class TestMcpConfig:
    def test_installs_mcp_config(self, tmp_path: Path, monkeypatch) -> None:
        module = _load_adapter()
        assets = module.asset_dir()
        project = tmp_path / "project"
        project.mkdir()
        monkeypatch.setenv("TEAMHARNESS_SHARED_DIR", str(tmp_path / "shared"))
        result = module._install_mcp_config(assets, project)
        assert result["action"] == "configured"
        config = json.loads((project / ".mcp.json").read_text(encoding="utf-8"))
        assert "mcpServers" in config
        assert "teamharness" in config["mcpServers"]
        assert config["mcpServers"]["teamharness"]["type"] == "stdio"

    def test_uninstalls_mcp_config(self, tmp_path: Path) -> None:
        module = _load_adapter()
        project = tmp_path / "project"
        project.mkdir()
        (project / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"teamharness": {"type": "stdio"}}}), encoding="utf-8"
        )
        result = module._uninstall_mcp_config(project)
        assert result["action"] == "removed-file"
        assert not (project / ".mcp.json").exists()

    def test_uninstall_keeps_other_servers(self, tmp_path: Path) -> None:
        module = _load_adapter()
        project = tmp_path / "project"
        project.mkdir()
        (project / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"teamharness": {"type": "stdio"}, "other": {"type": "http"}}}), encoding="utf-8"
        )
        result = module._uninstall_mcp_config(project)
        assert result["action"] == "removed-entry"
        config = json.loads((project / ".mcp.json").read_text(encoding="utf-8"))
        assert "teamharness" not in config["mcpServers"]
        assert "other" in config["mcpServers"]


class TestSkills:
    def test_installs_role_skills(self, tmp_path: Path) -> None:
        module = _load_adapter()
        assets = module.asset_dir()
        project = tmp_path / "project"
        project.mkdir()
        result = module._install_skills(assets, project, "remote-member")
        assert result["installed"]
        assert any("communication" in name for name in result["installed"])
        assert any("task-execution" in name for name in result["installed"])
        # remote-member should not get leader-only skills
        assert not any("task-delegation" in name for name in result["installed"])

    def test_uninstalls_skills(self, tmp_path: Path) -> None:
        module = _load_adapter()
        project = tmp_path / "project"
        project.mkdir()
        (project / ".claude" / "teamharness-skills" / "foo" / "SKILL.md").mkdir(parents=True)
        result = module._uninstall_skills(project)
        assert result["action"] == "removed"
        assert not (project / ".claude" / "teamharness-skills").exists()


class TestEndToEnd:
    def test_install_and_uninstall_roundtrip(self, tmp_path: Path, monkeypatch) -> None:
        module = _load_adapter()
        runtime_config = tmp_path / "runtime.yaml"
        _runtime_yaml(runtime_config)
        project = tmp_path / "project"
        project.mkdir()

        monkeypatch.setenv("TEAMHARNESS_RUNTIME_CONFIG", str(runtime_config))
        monkeypatch.setenv("CLAUDE_CODE_PROJECT_DIR", str(project))

        install_result = module.install()
        assert install_result["ok"] is True
        assert (project / "CLAUDE.md").exists()
        assert (project / ".mcp.json").exists()
        assert (project / ".claude" / "teamharness-skills").exists()

        uninstall_result = module.uninstall()
        assert uninstall_result["ok"] is True
        assert not (project / "CLAUDE.md").exists()
        assert not (project / ".mcp.json").exists()
        assert not (project / ".claude" / "teamharness-skills").exists()

    def test_install_preserves_existing_claude_md(self, tmp_path: Path, monkeypatch) -> None:
        module = _load_adapter()
        runtime_config = tmp_path / "runtime.yaml"
        _runtime_yaml(runtime_config)
        project = tmp_path / "project"
        project.mkdir()
        (project / "CLAUDE.md").write_text("# Existing Project\n\nSome rules.\n", encoding="utf-8")

        monkeypatch.setenv("TEAMHARNESS_RUNTIME_CONFIG", str(runtime_config))
        monkeypatch.setenv("CLAUDE_CODE_PROJECT_DIR", str(project))

        module.install()
        text = (project / "CLAUDE.md").read_text(encoding="utf-8")
        assert "# Existing Project" in text
        assert module.CLAUDE_MD_START in text
        assert "remote-member" in text

    def test_claude_md_contains_team_context(self, tmp_path: Path, monkeypatch) -> None:
        module = _load_adapter()
        runtime_config = tmp_path / "runtime.yaml"
        _runtime_yaml(runtime_config)
        project = tmp_path / "project"
        project.mkdir()

        monkeypatch.setenv("TEAMHARNESS_RUNTIME_CONFIG", str(runtime_config))
        monkeypatch.setenv("CLAUDE_CODE_PROJECT_DIR", str(project))

        module.install()
        text = (project / "CLAUDE.md").read_text(encoding="utf-8")
        assert "demo-team" in text
        assert "remote-dev" in text
        assert "!team:matrix.local" in text
        assert "dev-worker" in text
        assert "Remote Member Role" in text
        assert "matrix-secret-value" not in text

    def test_main_cli(self, tmp_path: Path, monkeypatch, capsys) -> None:
        module = _load_adapter()
        runtime_config = tmp_path / "runtime.yaml"
        _runtime_yaml(runtime_config)
        project = tmp_path / "project"
        project.mkdir()

        monkeypatch.setenv("TEAMHARNESS_RUNTIME_CONFIG", str(runtime_config))
        monkeypatch.setenv("CLAUDE_CODE_PROJECT_DIR", str(project))

        assert module.main(["install"]) == 0
        captured = capsys.readouterr()
        assert '"ok": true' in captured.out

        assert module.main(["uninstall"]) == 0
        captured = capsys.readouterr()
        assert '"ok": true' in captured.out

        assert module.main(["unknown"]) == 1
        assert module.main([]) == 1

#!/usr/bin/env python3
"""Claude Code adapter for TeamHarness.

Installs TeamHarness prompts and MCP configuration into a local Claude Code
project so that Claude Code can act as a HiClaw remote-member.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Dict, List, Optional

try:
    from ..common import (
        load_runtime_config,
        mcp_client_env,
        read_json,
        render_team_context,
        section,
        skill_entries,
        skill_names_for_role,
        string,
        write_json_atomic,
    )
except ImportError:  # pragma: no cover - invoked as a standalone script
    from common import (
        load_runtime_config,
        mcp_client_env,
        read_json,
        render_team_context,
        section,
        skill_entries,
        skill_names_for_role,
        string,
        write_json_atomic,
    )

# Markers used to make CLAUDE.md installation/removal idempotent and safe.
CLAUDE_MD_START = "<!-- BEGIN HICLAW TEAMHARNESS REMOTE MEMBER CONTEXT -->"
CLAUDE_MD_END = "<!-- END HICLAW TEAMHARNESS REMOTE MEMBER CONTEXT -->"


def asset_dir() -> Path:
    """Return the TeamHarness asset root (the directory containing plugin.yaml)."""
    adapter_dir = Path(__file__).resolve().parent
    candidate = adapter_dir.parent.parent
    if (candidate / "plugin.yaml").exists():
        return candidate
    # Fallback: assume running from a packaged layout where assets are copied next
    # to the adapter.
    packaged = adapter_dir / "teamharness"
    if (packaged / "plugin.yaml").exists():
        return packaged
    raise RuntimeError(f"Cannot locate TeamHarness assets near {adapter_dir}")


def project_dir() -> Path:
    """Return the target Claude Code project directory."""
    raw = os.getenv("CLAUDE_CODE_PROJECT_DIR", "").strip()
    return Path(raw).expanduser() if raw else Path.cwd()


def _mcp_server_path(asset_dir: Path) -> Path:
    return asset_dir / "mcp" / "server.py"


def _skill_source(asset_dir: Path, skill_path: str) -> Optional[Path]:
    source = asset_dir / skill_path
    return source if source.is_dir() else None


def _remote_member_role(config: Dict[str, Any]) -> str:
    return string(section(config, "member").get("role")) or "remote-member"


def _write_claude_md(project_dir: Path, content: str) -> Dict[str, Any]:
    target = project_dir / "CLAUDE.md"
    target.parent.mkdir(parents=True, exist_ok=True)

    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    start_idx = existing.find(CLAUDE_MD_START)
    end_idx = existing.find(CLAUDE_MD_END)

    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        before = existing[:start_idx]
        after = existing[end_idx + len(CLAUDE_MD_END) :]
        new_text = before + content + after
        action = "updated"
    else:
        parts = [existing.rstrip()] if existing.strip() else []
        parts.append(content)
        new_text = "\n\n".join(parts) + "\n"
        action = "created" if not existing else "appended"

    target.write_text(new_text, encoding="utf-8")
    return {"asset": "CLAUDE.md", "path": str(target), "action": action}


def _remove_claude_md(project_dir: Path) -> Dict[str, Any]:
    target = project_dir / "CLAUDE.md"
    if not target.exists():
        return {"asset": "CLAUDE.md", "path": str(target), "action": "missing"}

    existing = target.read_text(encoding="utf-8")
    start_idx = existing.find(CLAUDE_MD_START)
    end_idx = existing.find(CLAUDE_MD_END)

    if start_idx == -1 or end_idx == -1:
        return {"asset": "CLAUDE.md", "path": str(target), "action": "no-marker"}

    before = existing[:start_idx]
    after = existing[end_idx + len(CLAUDE_MD_END) :]
    new_text = (before + after).strip()

    if new_text:
        target.write_text(new_text + "\n", encoding="utf-8")
    else:
        target.unlink()

    return {"asset": "CLAUDE.md", "path": str(target), "action": "removed"}


def _mcp_config_path(project_dir: Path) -> Path:
    return project_dir / ".mcp.json"


def _build_mcp_server_entry(asset_dir: Path) -> Dict[str, Any]:
    server_path = _mcp_server_path(asset_dir)
    return {
        "type": "stdio",
        "command": "python3",
        "args": [str(server_path)],
        "env": mcp_client_env(),
    }


def _install_mcp_config(asset_dir: Path, project_dir: Path) -> Dict[str, Any]:
    target = _mcp_config_path(project_dir)
    config = read_json(target)
    servers = config.setdefault("mcpServers", {})
    servers["teamharness"] = _build_mcp_server_entry(asset_dir)
    write_json_atomic(target, config)
    return {"asset": ".mcp.json", "path": str(target), "action": "configured"}


def _uninstall_mcp_config(project_dir: Path) -> Dict[str, Any]:
    target = _mcp_config_path(project_dir)
    if not target.exists():
        return {"asset": ".mcp.json", "path": str(target), "action": "missing"}

    config = read_json(target)
    servers = config.get("mcpServers", {})
    if "teamharness" not in servers:
        return {"asset": ".mcp.json", "path": str(target), "action": "no-entry"}

    del servers["teamharness"]
    if not servers:
        del config["mcpServers"]

    if config:
        write_json_atomic(target, config)
        return {"asset": ".mcp.json", "path": str(target), "action": "removed-entry"}

    target.unlink()
    return {"asset": ".mcp.json", "path": str(target), "action": "removed-file"}


def _skills_target_dir(project_dir: Path) -> Path:
    return project_dir / ".claude" / "teamharness-skills"


def _install_skills(asset_dir: Path, project_dir: Path, role: str) -> Dict[str, Any]:
    target_root = _skills_target_dir(project_dir)
    installed: List[str] = []
    skipped: List[Dict[str, Any]] = []

    entries_source: Dict[str, str] = {}
    for entry in skill_entries(asset_dir):
        skill_id = string(entry.get("id"))
        skill_path = string(entry.get("path"))
        if skill_id and skill_path:
            entries_source[skill_id] = skill_path

    for skill_name in skill_names_for_role(asset_dir, role):
        # skill_name is "teamharness-{id}". Strip the prefix to find source path.
        skill_id = skill_name[len("teamharness-") :]
        source_path = entries_source.get(skill_id)
        if not source_path:
            skipped.append({"name": skill_name, "reason": "missing source path in plugin.yaml"})
            continue
        source = _skill_source(asset_dir, source_path)
        if source is None:
            skipped.append({"name": skill_name, "reason": f"source dir not found: {source_path}"})
            continue
        target = target_root / skill_id
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store", "*.pyc"))
        installed.append(skill_name)

    return {"asset": "skills", "path": str(target_root), "installed": installed, "skipped": skipped}


def _uninstall_skills(project_dir: Path) -> Dict[str, Any]:
    target = _skills_target_dir(project_dir)
    if not target.exists():
        return {"asset": "skills", "path": str(target), "action": "missing"}
    shutil.rmtree(target)
    return {"asset": "skills", "path": str(target), "action": "removed"}


def install(project_dir_override: Optional[Path] = None) -> Dict[str, Any]:
    """Install TeamHarness remote-member assets into a Claude Code project."""
    assets = asset_dir()
    project = project_dir_override or project_dir()
    config = load_runtime_config()
    role = _remote_member_role(config)

    claude_md_content = render_team_context(config, assets, role=role)
    # Append a lightweight skills index so Claude Code knows where to find skill docs.
    skills_index = ["", "## TeamHarness Skills", ""]
    skills_index.append(
        "When using a TeamHarness tool, read the matching skill file first:"
    )
    for skill_name in skill_names_for_role(assets, role):
        skill_id = skill_name[len("teamharness-") :]
        skills_index.append(f"- `{skill_name}` → `.claude/teamharness-skills/{skill_id}/SKILL.md`")
    claude_md_content = claude_md_content.rstrip() + "\n" + "\n".join(skills_index) + "\n"

    # Wrap the content in safe markers for idempotent uninstall.
    marked_content = f"{CLAUDE_MD_START}\n{claude_md_content}\n{CLAUDE_MD_END}"

    return {
        "ok": True,
        "projectDir": str(project),
        "role": role,
        "CLAUDE.md": _write_claude_md(project, marked_content),
        ".mcp.json": _install_mcp_config(assets, project),
        "skills": _install_skills(assets, project, role),
    }


def uninstall(project_dir_override: Optional[Path] = None) -> Dict[str, Any]:
    """Remove TeamHarness remote-member assets from a Claude Code project."""
    project = project_dir_override or project_dir()
    return {
        "ok": True,
        "projectDir": str(project),
        "CLAUDE.md": _remove_claude_md(project),
        ".mcp.json": _uninstall_mcp_config(project),
        "skills": _uninstall_skills(project),
    }


def main(argv: List[str] | None = None) -> int:
    """CLI entrypoint used by install.sh / uninstall.sh."""
    argv = argv or []
    if len(argv) < 1:
        print("Usage: adapter.py install|uninstall", file=sys.stderr)
        return 1
    command = argv[0]
    if command == "install":
        result = install()
    elif command == "uninstall":
        result = uninstall()
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

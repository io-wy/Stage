#!/usr/bin/env python3
"""Shared runtime-neutral helpers for TeamHarness runtime adapters.

Adapters for concrete runtimes (QwenPaw, Claude Code, ...) import this module to:

- Parse the controller-written runtime.yaml.
- Resolve the shared workspace directory.
- Render the team contract + role prompt + runtime context block.
- Read the plugin manifest and determine which skills apply to a role.

This module must not depend on any runtime-specific APIs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML may be absent in minimal envs
    yaml = None  # type: ignore[assignment]

TEAMS_PROMPT_FILE = "TEAMS.md"
MCP_CLIENT_ID = "teamharness"
TEAMS_CONTEXT_START = "<!-- BEGIN HICLAW RUNTIME TEAM CONTEXT -->"
TEAMS_CONTEXT_END = "<!-- END HICLAW RUNTIME TEAM CONTEXT -->"


def read_yaml(path: Path) -> Dict[str, Any]:
    """Read a YAML file, returning an empty dict on missing file or parse error."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if yaml else None
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def load_runtime_config() -> Dict[str, Any]:
    """Load runtime.yaml from TEAMHARNESS_RUNTIME_CONFIG env var."""
    raw = os.getenv("TEAMHARNESS_RUNTIME_CONFIG", "").strip()
    if not raw:
        return {}
    return read_yaml(Path(raw))


def section(data: Dict[str, Any], name: str) -> Dict[str, Any]:
    """Return a dict subsection, defaulting to empty dict."""
    value = data.get(name) or {}
    return value if isinstance(value, dict) else {}


def string(value: Any) -> str:
    """Normalize a value to a stripped string, treating None as empty."""
    return str(value).strip() if value is not None else ""


def string_list(value: Any) -> List[str]:
    """Normalize a value to a list of non-empty strings."""
    if not isinstance(value, list):
        return []
    return [string(item) for item in value if string(item)]


def string_fields(value: Any, keys: List[str]) -> Dict[str, str]:
    """Extract a subset of string fields from a dict."""
    if not isinstance(value, dict):
        return {}
    result: Dict[str, str] = {}
    for key in keys:
        text = string(value.get(key))
        if text:
            result[key] = text
    return result


def read_json(path: Path) -> Dict[str, Any]:
    """Read a JSON file, returning an empty dict on error."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    """Write JSON atomically via a temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def shared_dir() -> Path:
    """Resolve the TeamHarness shared directory from environment.

    The order mirrors the QwenPaw adapter behavior for backwards compatibility:
    explicit env vars first, then runtime-specific working dirs, then cwd fallback.
    """
    raw = os.getenv("TEAMHARNESS_SHARED_DIR", "").strip() or os.getenv("HICLAW_SHARED_DIR", "").strip()
    if raw:
        return Path(raw)

    for env_name in ("QWENPAW_WORKING_DIR", "COPAW_WORKING_DIR"):
        working_dir = os.getenv(env_name, "").strip()
        if working_dir:
            qwenpaw_path = Path(working_dir)
            if qwenpaw_path.name == ".qwenpaw":
                return qwenpaw_path.parent.parent.parent / "shared"
            return qwenpaw_path / "shared"

    return Path.cwd() / "shared"


def role_prompt_path(asset_dir: Path, role: str) -> Optional[Path]:
    """Return the path to the role-specific prompt file."""
    mapping = {
        "leader": asset_dir / "prompts" / "agent" / "leader.md",
        "team_leader": asset_dir / "prompts" / "agent" / "leader.md",
        "worker": asset_dir / "prompts" / "agent" / "worker.md",
        "remote-member": asset_dir / "prompts" / "agent" / "remote-member.md",
        "remote_member": asset_dir / "prompts" / "agent" / "remote-member.md",
        "manager": asset_dir / "prompts" / "manager" / "AGENTS.md",
    }
    return mapping.get(role)


def _member_role(config: Dict[str, Any], fallback: Optional[str] = None) -> str:
    """Infer the member role from runtime config or environment."""
    member = section(config, "member")
    return string(member.get("role") or os.getenv("HICLAW_AGENT_ROLE") or fallback or "worker")


def render_team_context(
    config: Dict[str, Any],
    asset_dir: Path,
    role: Optional[str] = None,
) -> str:
    """Render the full TEAMS.md content for a member.

    Combines:
    - The base team contract (prompts/team/TEAMS.md)
    - The role-specific prompt (leader/worker/remote-member/manager)
    - A generated runtime context block from runtime.yaml
    """
    team = section(config, "team")
    member = section(config, "member")
    desired = section(config, "desired")
    package = section(desired, "agentPackage")
    channel_policy = section(desired, "channelPolicy")

    base_prompt = asset_dir / "prompts" / "team" / "TEAMS.md"
    base = base_prompt.read_text(encoding="utf-8").strip() if base_prompt.exists() else ""

    resolved_role = role or _member_role(config)
    role_prompt = role_prompt_path(asset_dir, resolved_role)
    role_text = role_prompt.read_text(encoding="utf-8").strip() if role_prompt and role_prompt.exists() else ""

    lines: List[str] = []
    if base:
        lines.append(base)
    else:
        lines.append("# Team Contract")
    if role_text:
        lines.extend(["", role_text])
    lines.extend(["", TEAMS_CONTEXT_START, "## Runtime Team Context", ""])

    facts = [
        ("team.name", team.get("name")),
        ("team.teamRoomId", team.get("teamRoomId")),
        ("team.leaderName", team.get("leaderName")),
        ("team.leaderRuntimeName", team.get("leaderRuntimeName")),
        ("team.admin.name", section(team, "admin").get("name")),
        ("team.admin.matrixUserId", section(team, "admin").get("matrixUserId")),
        ("member.name", member.get("name")),
        ("member.runtimeName", member.get("runtimeName")),
        ("member.role", member.get("role")),
        ("member.runtime", member.get("runtime")),
        ("member.matrixUserId", member.get("matrixUserId")),
        ("member.personalRoomId", member.get("personalRoomId")),
        ("desired.agentPackage.name", package.get("name")),
        ("desired.agentPackage.version", package.get("version")),
    ]
    for key, value in facts:
        text = string(value)
        if text:
            lines.append(f"- {key}: {text}")

    members = team.get("members")
    if isinstance(members, list) and members:
        lines.extend(["", "### Team Members"])
        for item in members:
            entry = string_fields(item, ["name", "runtimeName", "role", "matrixUserId", "personalRoomId"])
            if entry:
                lines.append("- " + ", ".join(f"{key}: {value}" for key, value in entry.items()))

    if channel_policy:
        lines.append("- desired.channelPolicy: configured")

    lines.append("")
    lines.append("Do not write secrets, credentials, or live task status into this file.")
    lines.append(TEAMS_CONTEXT_END)
    return "\n".join(lines).rstrip() + "\n"


def skill_entries(asset_dir: Path) -> List[Dict[str, Any]]:
    """Return the list of skill entries from the TeamHarness plugin manifest."""
    manifest = read_yaml(asset_dir / "plugin.yaml")
    skills = section(manifest, "skills")
    entries: List[Dict[str, Any]] = []
    for group in ("agent", "team"):
        for entry in skills.get(group) or []:
            if isinstance(entry, dict):
                entries.append(entry)
    return entries


def role_aliases(role: str) -> set[str]:
    """Return the canonical and alias names for a role."""
    normalized = string(role) or "worker"
    aliases = {normalized}
    if normalized == "team_leader":
        aliases.add("leader")
    if normalized == "remote_member":
        aliases.add("remote-member")
    return aliases


def skill_names_for_role(asset_dir: Path, role: str) -> List[str]:
    """Return the `teamharness-{id}` skill names applicable to a role."""
    aliases = role_aliases(role)
    names: List[str] = []
    for entry in skill_entries(asset_dir):
        skill_id = string(entry.get("id"))
        if not skill_id:
            continue
        roles = string_list(entry.get("roles"))
        if roles and not aliases.intersection(roles):
            continue
        names.append(f"teamharness-{skill_id}")
    return names


def mcp_client_env(shared: Optional[Path] = None) -> Dict[str, str]:
    """Build the environment dict passed to the TeamHarness MCP server process.

    Mirrors the QwenPaw adapter's `_mcp_client_env()`. Secret values are never
    embedded; only env var names/paths are forwarded, and actual secrets remain
    in the parent process environment.
    """
    env: Dict[str, str] = {"TEAMHARNESS_SHARED_DIR": str(shared or shared_dir())}
    for name in (
        "TEAMHARNESS_RUNTIME_CONFIG",
        "HICLAW_MATRIX_URL",
        "HICLAW_WORKER_MATRIX_TOKEN",
        "HICLAW_MATRIX_USER_ID",
        "HICLAW_WORKER_ROLE",
        "HICLAW_AGENT_ROLE",
        "HICLAW_WORKER_NAME",
        "HICLAW_STORAGE_PREFIX",
        "HICLAW_SHARED_STORAGE_PREFIX",
        "HICLAW_FS_BUCKET",
        "HICLAW_CONTROLLER_URL",
        "HICLAW_AUTH_TOKEN_FILE",
        "HICLAW_CLUSTER_ID",
        "HICLAW_FS_ENDPOINT",
        "HICLAW_FS_ACCESS_KEY",
        "HICLAW_FS_SECRET_KEY",
        "MC_HOST_hiclaw",
        "QWENPAW_WORKING_DIR",
        "COPAW_WORKING_DIR",
    ):
        value = os.getenv(name, "").strip()
        if value:
            env[name] = value
    return env


# ---------------------------------------------------------------------------
# Team context persistence
# ---------------------------------------------------------------------------


def write_team_context(workspace_dir: Path, config: Dict[str, Any], asset_dir: Path) -> Dict[str, Any]:
    """Render and write the TEAMS.md prompt file into a workspace."""
    target = workspace_dir / TEAMS_PROMPT_FILE
    text = render_team_context(config, asset_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = target.read_text(encoding="utf-8") if target.exists() else None
    target.write_text(text, encoding="utf-8")
    return {
        "ok": True,
        "asset": "team-context",
        "path": str(target),
        "action": "created" if existing is None else ("unchanged" if existing == text else "updated"),
    }


# ---------------------------------------------------------------------------
# Output sanitizer (shared patterns used by all runtimes)
# ---------------------------------------------------------------------------

REDACTION = "[REDACTED]"
SENSITIVE_FILE_AUTO_DENY_RULE = "SENSITIVE_FILE_BLOCK"

_BUILTIN_SANITIZER_PATTERNS: List[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(LTAI)[A-Za-z0-9]{12,}"), r"\1****"),
    (re.compile(r"\b(AKIA)[A-Za-z0-9]{12,}"), r"\1****"),
    (re.compile(r"\b(AKID)[A-Za-z0-9]{12,}"), r"\1****"),
    (
        re.compile(
            r"(?i)((?:access_?key_?secret|secret_?access_?key|accesskeysecret"
            r"|TENCENTCLOUD_SECRET_KEY|aws_secret_access_key"
            r"|credentials\.secret)"
            r"""["']?[\s]*[=:]\s*["']?)([A-Za-z0-9/+=]{16,})"""
        ),
        r"\1********",
    ),
    (
        re.compile(
            r"(?i)((?:security_?token|session_?token|sts_?token)"
            r"""["']?[\s]*[=:]\s*["']?)([A-Za-z0-9/+=]{100,})"""
        ),
        r"\1********",
    ),
    (
        re.compile(
            r"(?i)((?:secret|token|password|passwd|key_secret)"
            r"""["']?[\s]*[=:]\s*["']?)([A-Za-z0-9/+=]{30,})"""
        ),
        r"\1********",
    ),
]


def sanitizer_rules() -> List[str]:
    """Return exact-match sanitizer rules from runtime config + env refs."""
    config = load_runtime_config()
    desired = section(config, "desired")
    policy = section(desired, "outputSanitize")
    rules = string_list(policy.get("keywords"))
    credentials = section(config, "credentials")
    env_refs = string_list(policy.get("envRefs"))
    for key in ("matrixTokenEnv", "gatewayKeyEnv", "storageAccessKeyEnv", "storageSecretKeyEnv"):
        value = string(credentials.get(key))
        if value:
            env_refs.append(value)
    for env_name in env_refs:
        value = os.getenv(env_name, "")
        if len(value) >= 4:
            rules.append(value)
    deduped: List[str] = []
    seen: set[str] = set()
    for rule in rules:
        if rule and rule not in seen:
            seen.add(rule)
            deduped.append(rule)
    return deduped


def sanitizer_patterns() -> List[tuple[re.Pattern[str], str]]:
    """Return regex sanitizer patterns including builtin + credagent rules."""
    patterns = list(_BUILTIN_SANITIZER_PATTERNS)
    for rule in credagent_output_sanitize_rules():
        try:
            rule_type = rule.get("type", "")
            if rule_type == "prefix":
                prefix = str(rule["prefix"])
                min_length = int(rule.get("min_length", 16))
                suffix_len = max(min_length - len(prefix), 1)
                escaped = re.escape(prefix)
                patterns.append((re.compile(rf"\b({escaped})[A-Za-z0-9]{{{suffix_len},}}"), r"\1****"))
            elif rule_type == "keyword":
                keywords = rule.get("keywords", [])
                if not isinstance(keywords, list) or not keywords:
                    continue
                kw_alt = "|".join(re.escape(str(keyword)) for keyword in keywords)
                patterns.append(
                    (
                        re.compile(rf"""(?i)({kw_alt})(["']?[\s]*[=:]\s*["']?)([A-Za-z0-9/+=]{{16,}})"""),
                        r"\1\2********",
                    )
                )
            elif rule_type == "regex":
                patterns.append((re.compile(str(rule["pattern"])), str(rule.get("replacement", "********"))))
        except (KeyError, TypeError, ValueError, re.error):
            continue
    return patterns


def sanitize_text_with_rules(
    text: str,
    exact_rules: List[str],
    patterns: List[tuple[re.Pattern[str], str]],
) -> str:
    """Apply exact rules and regex patterns to redact sensitive text."""
    sanitized = text
    for rule in sorted(exact_rules, key=len, reverse=True):
        sanitized = re.sub(re.escape(rule), REDACTION, sanitized)
    for pattern, replacement in patterns:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


def sanitize_text(text: str) -> str:
    """Redact sensitive values from text using runtime config + builtin patterns."""
    return sanitize_text_with_rules(text, sanitizer_rules(), sanitizer_patterns())


# ---------------------------------------------------------------------------
# Credential guard helpers
# ---------------------------------------------------------------------------


def _dedupe_paths(paths: List[Path]) -> List[Path]:
    seen: set[str] = set()
    deduped: List[Path] = []
    for path in paths:
        key = str(path)
        if key not in seen:
            seen.add(key)
            deduped.append(path)
    return deduped


def credagent_paths(agent_workspaces: Optional[List[Path]] = None) -> List[Path]:
    """Return candidate paths for credagent.json configuration files."""
    paths: List[Path] = []
    if agent_workspaces:
        for workspace_dir in agent_workspaces:
            paths.append(workspace_dir / "config" / "credagent.json")
    for env_name in ("HICLAW_AGENT_HOME", "HICLAW_WORKER_HOME"):
        raw = os.getenv(env_name, "").strip()
        if raw:
            paths.append(Path(raw).expanduser() / "config" / "credagent.json")
    for env_name in ("QWENPAW_WORKING_DIR", "COPAW_WORKING_DIR"):
        raw = os.getenv(env_name, "").strip()
        if raw:
            working_dir = Path(raw).expanduser()
            paths.append(working_dir / "workspaces" / "default" / "config" / "credagent.json")
            paths.append(working_dir.parent / "config" / "credagent.json")
    return _dedupe_paths(paths)


def credagent_specs(agent_workspaces: Optional[List[Path]] = None) -> List[Dict[str, Any]]:
    """Load all credagent.json specs found in known locations."""
    specs = []
    for path in credagent_paths(agent_workspaces):
        if not path.exists():
            continue
        spec = read_json(path)
        if spec:
            specs.append(spec)
    return specs


def normalize_credentials(specs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize credagent credential entries into a standard shape."""
    normalized: List[Dict[str, Any]] = []
    for spec in specs:
        credentials = spec.get("credentials", [])
        if not isinstance(credentials, list):
            continue
        for entry in credentials:
            if not isinstance(entry, dict):
                continue
            raw = string(entry.get("path"))
            if not raw:
                continue
            expanded = str(Path(raw).expanduser())
            if raw.endswith("/") and not expanded.endswith("/"):
                expanded += "/"
            permit = entry.get("programPermit", [])
            if isinstance(permit, str):
                permit = [permit]
            elif not isinstance(permit, list):
                permit = []
            normalized.append(
                {
                    "path": expanded,
                    "programPermit": [str(item) for item in permit],
                    "writable": bool(entry.get("writable", False)),
                }
            )
    return normalized


def credagent_output_sanitize_rules(specs: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Return output_sanitize rules from credagent specs."""
    rules: List[Dict[str, Any]] = []
    for spec in specs if specs is not None else credagent_specs():
        raw_rules = spec.get("output_sanitize", [])
        if not isinstance(raw_rules, list):
            continue
        rules.extend(rule for rule in raw_rules if isinstance(rule, dict))
    return rules

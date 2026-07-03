# TeamHarness Claude Code Adapter

This adapter installs TeamHarness remote-member assets into a local Claude Code
project so that Claude Code can participate as a HiClaw team member.

## What it does

The adapter installs three kinds of assets into the target project directory:

1. **CLAUDE.md** – Injects the team contract, role prompt, and runtime context
   (wrapped in safe markers so uninstall is idempotent).
2. **.mcp.json** – Registers the `teamharness` MCP server (stdio transport to
   `mcp/server.py`) so Claude Code can call `health`, `roomflow`,
   `filesync`, `projectflow`, and `taskflow` tools. The `message` tool is
   intentionally blocked for `remote-member` roles.
3. **Skills** – Copies the relevant TeamHarness skills (filtered by role) into
   `.claude/teamharness-skills/` for easy reference.

## Install

```bash
export TEAMHARNESS_RUNTIME_CONFIG=/path/to/runtime.yaml
bash plugins/teamharness/adapters/claude-code/install.sh
```

Optional environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `TEAMHARNESS_RUNTIME_CONFIG` | *(required)* | Path to the controller-written runtime.yaml |
| `CLAUDE_CODE_PROJECT_DIR` | `$PWD` | Target Claude Code project directory |
| `CLAUDE_CONFIG_DIR` | — | Passed through to the adapter if needed |
| `TEAMHARNESS_INSTALL_LOG` | — | JSON-lines log file path |

## Uninstall

```bash
bash plugins/teamharness/adapters/claude-code/uninstall.sh
```

This removes the injected CLAUDE.md section, `.mcp.json` entry, and copied skills.

## Files

| File | Purpose |
|------|---------|
| `install.sh` | Bash wrapper that validates env vars and calls `adapter.py install` |
| `uninstall.sh` | Bash wrapper that calls `adapter.py uninstall` |
| `adapter.py` | Core Python logic: reads runtime.yaml, writes CLAUDE.md, .mcp.json, skills |
| `README.md` | This file |

## Architecture

The adapter is intentionally thin. All runtime-neutral logic lives in
`adapters/common.py` (shared with the QwenPaw adapter):

- `load_runtime_config()` – parses `runtime.yaml`
- `render_team_context()` – generates the TEAMS.md content
- `skill_entries()` / `skill_names_for_role()` – filters skills by role
- `mcp_client_env()` – builds the env dict for the MCP server

The Claude Code adapter only handles Claude-specific I/O:

- CLAUDE.md marker-based injection/removal
- `.mcp.json` read/write
- Skill directory copy into `.claude/teamharness-skills/`

## Requirements

- Python 3.10+
- `PyYAML` (`python3 -m pip install pyyaml`)
- Claude Code with project-level `.mcp.json` support

## Testing

```bash
cd plugins/tests/teamharness/adapters/claude-code
pytest
```

Manual integration test:

```bash
tmpdir=$(mktemp -d)
cat > "$tmpdir/runtime.yaml" <<'YAML'
kind: MemberRuntimeConfig
team:
  name: demo-team
member:
  name: claude-local
  role: remote-member
YAML
mkdir -p "$tmpdir/project"
TEAMHARNESS_RUNTIME_CONFIG="$tmpdir/runtime.yaml" \
  CLAUDE_CODE_PROJECT_DIR="$tmpdir/project" \
  ./plugins/teamharness/adapters/claude-code/install.sh
ls "$tmpdir/project/"
cat "$tmpdir/project/CLAUDE.md"
cat "$tmpdir/project/.mcp.json"
```

## License

Same as the parent TeamHarness plugin.

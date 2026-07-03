#!/usr/bin/env bash
# uninstall.sh - Remove TeamHarness remote-member assets from a Claude Code project.
#
# Environment variables:
#   CLAUDE_CODE_PROJECT_DIR  (optional) Target project directory; default: PWD
#   TEAMHARNESS_INSTALL_LOG  (optional) JSON-lines log file

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADAPTER_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

export CLAUDE_CODE_PROJECT_DIR="${CLAUDE_CODE_PROJECT_DIR:-${PWD}}"

PYTHONPATH="${ADAPTER_ROOT}:${PYTHONPATH:-}"
export PYTHONPATH

python3 "${SCRIPT_DIR}/adapter.py" uninstall

log_file="${TEAMHARNESS_INSTALL_LOG:-}"
if [[ -n "$log_file" ]]; then
  mkdir -p "$(dirname "$log_file")"
  printf '{"event":"uninstall","runtime":"claude-code","pluginDir":"%s","projectDir":"%s"}\n' \
    "${AGENTTEAMS_PLUGIN_DIR:-${PWD}}" "${CLAUDE_CODE_PROJECT_DIR}" >> "$log_file"
fi

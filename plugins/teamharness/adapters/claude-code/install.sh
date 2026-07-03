#!/usr/bin/env bash
# install.sh - Install TeamHarness remote-member assets into a Claude Code project.
#
# Environment variables:
#   TEAMHARNESS_RUNTIME_CONFIG  (required) Path to member runtime.yaml
#   CLAUDE_CODE_PROJECT_DIR     (optional) Target project directory; default: PWD
#   CLAUDE_CONFIG_DIR           (optional) Passed through to the adapter
#   TEAMHARNESS_INSTALL_LOG     (optional) JSON-lines log file

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADAPTER_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -z "${TEAMHARNESS_RUNTIME_CONFIG:-}" ]]; then
  echo "ERROR: TEAMHARNESS_RUNTIME_CONFIG is required" >&2
  exit 1
fi

if [[ ! -f "${TEAMHARNESS_RUNTIME_CONFIG}" ]]; then
  echo "ERROR: runtime config not found: ${TEAMHARNESS_RUNTIME_CONFIG}" >&2
  exit 1
fi

if ! python3 -c "import yaml" 2>/dev/null; then
  echo "ERROR: PyYAML is required. Install it with: python3 -m pip install pyyaml" >&2
  exit 1
fi

export TEAMHARNESS_RUNTIME_CONFIG
export CLAUDE_CODE_PROJECT_DIR="${CLAUDE_CODE_PROJECT_DIR:-${PWD}}"
if [[ -n "${CLAUDE_CONFIG_DIR:-}" ]]; then
  export CLAUDE_CONFIG_DIR
fi

PYTHONPATH="${ADAPTER_ROOT}:${PYTHONPATH:-}"
export PYTHONPATH

python3 "${SCRIPT_DIR}/adapter.py" install

log_file="${TEAMHARNESS_INSTALL_LOG:-}"
if [[ -n "$log_file" ]]; then
  mkdir -p "$(dirname "$log_file")"
  printf '{"event":"install","runtime":"claude-code","pluginDir":"%s","projectDir":"%s"}\n' \
    "${AGENTTEAMS_PLUGIN_DIR:-${PWD}}" "${CLAUDE_CODE_PROJECT_DIR}" >> "$log_file"
fi
